"""Connection-local Google credential lifecycle; no spreadsheet authorization.

Construction is inert. initialize() reads local files only. get_credentials()
and refresh() may contact Google's token endpoint. Only authorize_interactively()
can start a browser flow, and requires explicit allow_browser=True.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Iterator

from google.auth.credentials import Credentials
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from requests import Session
from requests.exceptions import RequestException

from ..config import ConfigurationError, GSheetConfig
from ..core.exceptions import AuthenticationError, AuthenticationErrorCode as Code


@contextmanager
def _quiet_auth_logging() -> Iterator[None]:
    """Suppress dependency diagnostics for this thread while secrets are in use.

    OAuth/HTTP debug logs can include tokens. Filters are temporary, contain no
    credentials, and leave other threads and application logging unaffected.
    """
    thread_id = get_ident()

    class OtherThreadsOnly(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.thread != thread_id

    prefixes = ("google.auth", "google.oauth2", "google_auth_oauthlib", "requests",
                "requests_oauthlib", "oauthlib", "urllib3")
    names = set(prefixes) | {name for name in list(logging.Logger.manager.loggerDict)
                             if any(name.startswith(prefix + ".") for prefix in prefixes)}
    loggers = [logging.getLogger(name) for name in names]
    guard = OtherThreadsOnly()
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)


def _read_json(path: Path, *, secure: bool = False) -> dict[str, Any]:
    """Read regular files without following final symlinks; sanitize all failures."""
    failure = Code.INVALID_CREDENTIALS
    result = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError
            if secure and os.name == "posix" and (
                    metadata.st_uid != os.getuid() or metadata.st_mode & 0o077
                    or metadata.st_nlink != 1):
                failure = Code.STORAGE_FAILED
            else:
                parsed = json.load(stream)
                if isinstance(parsed, dict):
                    result = parsed
    except FileNotFoundError:
        failure = Code.MISSING_CREDENTIALS
    except OSError:
        failure = Code.STORAGE_FAILED if secure else Code.INVALID_CREDENTIALS
    except (ValueError, UnicodeError, RecursionError):
        pass
    if result is None:
        raise AuthenticationError(failure)
    return result


def _check_scopes(credentials: Credentials, config: GSheetConfig) -> None:
    # Exact scope sets avoid reusing a broad OAuth token under read-only policy.
    requested = set(config.api.scopes)
    scopes = set(credentials.scopes or ())
    granted = getattr(credentials, "granted_scopes", None)
    if isinstance(granted, str):
        granted = granted.split()  # OAuth scope responses use space-delimited strings.
    if scopes != requested or (granted is not None and set(granted) != requested):
        raise AuthenticationError(Code.INSUFFICIENT_SCOPES, requires_interaction=config.auth.mode == "oauth")


def _refresh_error(exc: Exception, *, oauth: bool) -> AuthenticationError:
    if isinstance(exc, (TransportError, RequestException)):
        return AuthenticationError(Code.NETWORK_FAILED, retryable=True)
    # Inspect only a known protocol code, never copy server descriptions/tokens.
    codes = {arg.get("error") for arg in exc.args if isinstance(arg, dict)
             and isinstance(arg.get("error"), str)}
    if "invalid_grant" in codes:
        return AuthenticationError(Code.AUTHORIZATION_EXPIRED, requires_interaction=oauth)
    if "invalid_scope" in codes:
        return AuthenticationError(Code.INSUFFICIENT_SCOPES, requires_interaction=oauth)
    if "invalid_client" in codes:
        return AuthenticationError(Code.INVALID_CREDENTIALS)
    return AuthenticationError(Code.REFRESH_FAILED, retryable=bool(getattr(exc, "retryable", False)))


@dataclass(frozen=True)
class AuthenticationStatus:
    """Local observations, not proof of Google identity or spreadsheet access."""

    mode: str
    initialized: bool
    credentials_loaded: bool
    locally_valid: bool
    refresh_required: bool
    interactive_required: bool


class CredentialManager:
    """One manager per trusted connection; never share between businesses.

    connection_id must be a stable, caller-assigned identifier, never selected by
    sheet content or an AI tool. It binds local tokens, not spreadsheet access.
    Returned Google credentials are sensitive client-internal objects; never
    expose them through AI-facing tools, logging, or serialization. The future
    client must enforce spreadsheet and operation permissions before each call.
    """

    def __init__(self, config: GSheetConfig, *, connection_id: str) -> None:
        if not isinstance(config, GSheetConfig):
            raise ConfigurationError("config", "supply GSheetConfig")
        if not isinstance(connection_id, str) or not connection_id.strip():
            raise ConfigurationError("connection_id", "supply a nonempty stable connection identifier")
        self._config = config
        self._connection_id = connection_id
        self._credentials: Credentials | None = None
        self._oauth = None
        self._initialized = False
        self._interaction_required = False
        self._lock = RLock()

    def __repr__(self) -> str:
        return "CredentialManager(<isolated connection>)"

    def _needs_refresh(self) -> bool:
        credentials = self._credentials
        if credentials is None or not credentials.valid or credentials.expiry is None:
            return True
        expiry = credentials.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return (expiry - datetime.now(timezone.utc)).total_seconds() <= self._config.auth.refresh_margin_seconds

    @property
    def status(self) -> AuthenticationStatus:
        with self._lock:
            credentials = self._credentials
            needs_refresh = self._needs_refresh()
            refreshable = (self._config.auth.refresh_enabled and credentials is not None
                           and (self._config.auth.mode == "service_account"
                                or bool(getattr(credentials, "refresh_token", None))))
            interactive = self._config.auth.mode == "oauth" and (
                self._interaction_required or credentials is None or (needs_refresh and not refreshable))
            return AuthenticationStatus(self._config.auth.mode, self._initialized, credentials is not None,
                                        bool(credentials and credentials.valid), needs_refresh, interactive)

    def initialize(self, *, reload: bool = False) -> AuthenticationStatus:
        """Load local files without network/browser activity; reload replaces state."""
        with self._lock:
            if self._initialized and not reload:
                return self.status
            self.reset()
            if self._config.auth.mode == "none":
                raise AuthenticationError(Code.UNSUPPORTED_MODE)
            failure = False
            try:
                self._config.auth.validate_for_auth()
            except ConfigurationError:
                failure = True
            if failure:
                raise AuthenticationError(Code.MISSING_CREDENTIALS)
            if self._config.auth.mode == "service_account":
                from .service_account import load_service_account
                credentials = load_service_account(self._config.auth, self._config.api)
            else:
                from .oauth import OAuthAuthentication
                self._oauth = OAuthAuthentication(self._config, self._connection_id)
                credentials = self._oauth.load()
            if credentials is not None:
                _check_scopes(credentials, self._config)
            self._credentials = credentials
            self._initialized = True
            return self.status

    def requires_interactive_authorization(self) -> bool:
        self.initialize()
        return self.status.interactive_required

    def get_credentials(self) -> Credentials:
        """Return client-internal credentials, refreshing if policy requires it.

        Locally valid tokens may still be revoked remotely. No Sheets request is
        made here. Future clients should obtain credentials before each operation
        and must not bypass refresh policy via automatic transport refresh.
        """
        with self._lock:
            self.initialize()
            if self._interaction_required:
                raise AuthenticationError(Code.AUTHORIZATION_EXPIRED, requires_interaction=True)
            if self._credentials is None:
                raise AuthenticationError(Code.AUTHORIZATION_REQUIRED, requires_interaction=True)
            if self._needs_refresh():
                if not self._config.auth.refresh_enabled and self._credentials.valid:
                    return self._credentials  # Near expiry, but automatic refresh disabled.
                return self.refresh()
            return self._credentials

    def refresh(self) -> Credentials:
        """Explicit network refresh; respects refresh_enabled, never opens a browser."""
        with self._lock:
            self.initialize()
            credentials = self._credentials
            oauth = self._config.auth.mode == "oauth"
            if credentials is None:
                raise AuthenticationError(Code.AUTHORIZATION_REQUIRED, requires_interaction=oauth)
            if self._interaction_required:
                raise AuthenticationError(Code.AUTHORIZATION_EXPIRED, requires_interaction=True)
            if not self._config.auth.refresh_enabled or (oauth and not credentials.refresh_token):
                raise AuthenticationError(Code.REFRESH_UNAVAILABLE, requires_interaction=oauth)
            failure = None
            try:
                with _quiet_auth_logging(), Session() as session:
                    request = Request(session=session)

                    def bounded_request(*args: Any, **kwargs: Any) -> Any:
                        kwargs["timeout"] = self._config.api.request_timeout_seconds
                        return request(*args, **kwargs)

                    credentials.refresh(bounded_request)
            except (GoogleAuthError, RequestException, ValueError, TypeError, KeyError, OverflowError) as exc:
                failure = _refresh_error(exc, oauth=oauth)
            if failure is not None:
                self._interaction_required = failure.requires_interaction
                # Discard potentially partially mutated credentials on any failure.
                self._credentials = None
                raise failure
            try:
                _check_scopes(credentials, self._config)
                if not credentials.valid:
                    raise AuthenticationError(Code.REFRESH_FAILED)
                if oauth:
                    self._oauth.persist(credentials)
            except AuthenticationError:
                self._credentials = None
                raise
            return credentials

    def authorize_interactively(self, *, allow_browser: bool = False,
                                reauthorize: bool = False,
                                timeout_seconds: float = 300.0) -> AuthenticationStatus:
        """Explicit local loopback OAuth; reauthorization never changes strategy."""
        if allow_browser is not True:
            raise AuthenticationError(Code.INTERACTION_NOT_ALLOWED)
        with self._lock:
            self.initialize()
            if self._config.auth.mode != "oauth":
                raise AuthenticationError(Code.UNSUPPORTED_MODE)
            if self._credentials is not None and not reauthorize:
                raise AuthenticationError(Code.AUTHORIZATION_REQUIRED, requires_interaction=True)
            credentials = self._oauth.authorize(timeout_seconds=timeout_seconds)
            _check_scopes(credentials, self._config)
            self._oauth.persist(credentials)
            self._credentials = credentials
            self._interaction_required = False
            return self.status

    def reset(self) -> None:
        """Forget local state, without deleting stored tokens or revoking Google access."""
        with self._lock:
            self._credentials = None
            self._oauth = None
            self._initialized = False
            self._interaction_required = False
