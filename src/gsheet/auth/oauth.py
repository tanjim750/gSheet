"""Explicit installed-app OAuth and connection-bound, private local token files."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import stat
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, make_server

from google.oauth2.credentials import Credentials
from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from ..config import ConfigurationError, GSheetConfig
from ..core.exceptions import AuthenticationError, AuthenticationErrorCode as Code
from .credentials import _check_scopes, _quiet_auth_logging, _read_json

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_AUTH_URIS = {"https://accounts.google.com/o/oauth2/auth",
              "https://accounts.google.com/o/oauth2/v2/auth"}


class _QuietCallbackHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # The base handler logs the callback URL, including authorization codes.
        pass


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class OAuthAuthentication:
    """Internal strategy, owned by exactly one CredentialManager.

    Token files use Google's authorized_user JSON with additional gSheet binding
    fields. Unbound/foreign files are rejected, never adopted or overwritten.
    Connection IDs are hashed, but are not secrets or an OS security boundary.
    Use a distinct, trusted local path per connection. Local token files contain
    plaintext secrets protected by OS permissions; use OS isolation in production.
    """

    def __init__(self, config: GSheetConfig, connection_id: str) -> None:
        self._config = config
        self._binding = hashlib.sha256(connection_id.encode()).hexdigest()
        path = config.auth.oauth_token_file
        self._path = path.absolute() if path is not None else None
        self._observed_digest: str | None = None
        self._client: dict[str, Any] | None = None

    def __repr__(self) -> str:
        return "OAuthAuthentication(<redacted>)"

    def _load_client(self) -> dict[str, Any]:
        if self._config.auth.mode != "oauth":
            raise AuthenticationError(Code.UNSUPPORTED_MODE)
        path = self._config.auth.oauth_client_file
        if path is None:
            raise AuthenticationError(Code.MISSING_CREDENTIALS)
        info = _read_json(path)
        client = info.get("installed")
        if (not isinstance(client, dict)
                or not all(isinstance(client.get(key), str) and client[key].strip()
                           for key in ("client_id", "client_secret", "auth_uri", "token_uri"))
                or client["token_uri"] != _TOKEN_URI
                or client["auth_uri"] not in _AUTH_URIS):
            raise AuthenticationError(Code.INVALID_CREDENTIALS)
        # Only documented local installed-app flow, not web apps or custom issuers.
        self._client = {key: client[key] for key in
                        ("client_id", "client_secret", "auth_uri", "token_uri")}
        return self._client

    def _validate_binding(self, info: dict[str, Any]) -> None:
        if (info.get("gsheet_token_version") != 1
                or info.get("gsheet_connection") != self._binding
                or info.get("client_id") != self._client["client_id"]
                or info.get("client_secret") != self._client["client_secret"]):
            raise AuthenticationError(Code.CONNECTION_MISMATCH)

    def load(self) -> Credentials | None:
        self._load_client()
        if self._path is None:
            return None  # In-memory authorization explicitly supported.
        info = None
        failure = None
        try:
            info = _read_json(self._path, secure=True)
        except AuthenticationError as exc:
            if exc.code != Code.MISSING_CREDENTIALS:
                failure = exc
        if failure is not None:
            raise failure
        if info is None:
            return None
        self._validate_binding(info)
        if (info.get("type") != "authorized_user"
                or info.get("token_uri") != _TOKEN_URI
                or info.get("universe_domain", "googleapis.com") != "googleapis.com"
                or not isinstance(info.get("scopes"), list)
                or not all(isinstance(scope, str) for scope in info["scopes"])):
            raise AuthenticationError(Code.INVALID_CREDENTIALS)
        if set(info["scopes"]) != set(self._config.api.scopes):
            raise AuthenticationError(Code.INSUFFICIENT_SCOPES, requires_interaction=True)
        for name in ("token", "refresh_token", "expiry"):
            value = info.get(name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise AuthenticationError(Code.INVALID_CREDENTIALS)
        if not info.get("token") and not info.get("refresh_token"):
            raise AuthenticationError(Code.AUTHORIZATION_REQUIRED, requires_interaction=True)
        credentials = None
        try:
            credentials = Credentials.from_authorized_user_info(
                {**info, "refresh_token": info.get("refresh_token")}, scopes=self._config.api.scopes)
        except (ValueError, TypeError, KeyError):
            pass
        if credentials is None:
            raise AuthenticationError(Code.INVALID_CREDENTIALS)
        # Unknown expiry is treated conservatively as requiring refresh by manager.
        _check_scopes(credentials, self._config)
        self._observed_digest = _digest(info)
        return credentials

    def prepare_reauthorization(self) -> None:
        """Validate ownership/storage, not usability of the token being replaced.

        Recover scope mismatches and corrupt credential fields only when the
        connection/client binding remains verifiable. Unparseable, unbound,
        foreign, or insecure files still fail closed. Snapshot the old file so
        persist() can reject concurrent replacement after the browser flow.
        """
        self._load_client()
        if self._path is None:
            return
        info = None
        failure = None
        try:
            info = _read_json(self._path, secure=True)
        except AuthenticationError as exc:
            if exc.code != Code.MISSING_CREDENTIALS:
                failure = exc
        if failure is not None:
            raise failure
        if info is not None:
            self._validate_binding(info)
            self._observed_digest = _digest(info)

    def authorize(self, *, timeout_seconds: float) -> Credentials:
        valid_timeout = type(timeout_seconds) in (int, float) and timeout_seconds > 0
        if valid_timeout:
            try:
                valid_timeout = math.isfinite(timeout_seconds)
            except OverflowError:
                valid_timeout = False
        if not valid_timeout:
            raise ConfigurationError("timeout_seconds", "use positive finite seconds")
        client = self._client or self._load_client()
        credentials = None
        failure = Code.AUTHORIZATION_FAILED
        try:
            with _quiet_auth_logging():
                flow = InstalledAppFlow.from_client_config(
                    {"installed": client}, scopes=self._config.api.scopes,
                    autogenerate_code_verifier=True)
                try:
                    credentials = self._run_loopback(flow, timeout_seconds)
                finally:
                    flow.oauth2session.close()
        except RequestException:
            failure = Code.NETWORK_FAILED
        except (GoogleAuthError, OAuth2Error, OSError, ValueError, TypeError, KeyError, OverflowError,
                AttributeError, webbrowser.Error):
            pass
        if credentials is None:
            raise AuthenticationError(failure, retryable=failure == Code.NETWORK_FAILED,
                                      requires_interaction=True)
        _check_scopes(credentials, self._config)
        return credentials

    def _run_loopback(self, flow: InstalledAppFlow, timeout_seconds: float) -> Credentials:
        query = None
        state = ""

        def callback(environ: dict[str, Any], start_response: Any) -> list[bytes]:
            nonlocal query
            candidate = environ.get("QUERY_STRING", "")
            params = parse_qs(candidate)
            received_state = params.get("state", [])
            accepted = (environ.get("PATH_INFO") == "/" and len(received_state) == 1
                        and hmac.compare_digest(received_state[0].encode(), state.encode())
                        and ("code" in params or "error" in params))
            if accepted:
                query = candidate
            start_response("200 OK" if accepted else "400 Bad Request",
                           [("Content-Type", "text/plain; charset=utf-8"),
                            ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer")])
            return [b"Authorization response received. You may close this window." if accepted
                    else b"Invalid authorization response."]

        # Standard loopback HTTP is used only for the local callback. Token
        # exchange is HTTPS. No library callback handler or URL logger is used.
        with make_server("127.0.0.1", 0, callback, handler_class=_QuietCallbackHandler) as server:
            flow.redirect_uri = f"http://127.0.0.1:{server.server_port}/"
            authorization_url, state = flow.authorization_url(
                access_type="offline", prompt="consent select_account")
            if not webbrowser.open(authorization_url, new=1, autoraise=True):
                raise ValueError
            deadline = time.monotonic() + timeout_seconds
            while query is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError
                server.timeout = remaining
                server.handle_request()
            # oauthlib requires HTTPS for parsing; redirect_uri sent to Google
            # remains the registered loopback HTTP URI (as in Google's helper).
            flow.fetch_token(authorization_response=flow.redirect_uri.replace("http:", "https:", 1) + "?" + query,
                             timeout=self._config.api.request_timeout_seconds)
            return flow.credentials

    def _check_directory(self) -> None:
        metadata = self._path.parent.stat()
        if not stat.S_ISDIR(metadata.st_mode) or (os.name == "posix" and (
                metadata.st_uid != os.getuid() or metadata.st_mode & 0o022)):
            raise AuthenticationError(Code.STORAGE_FAILED)

    def persist(self, credentials: Credentials) -> None:
        """Atomic private writes, checking binding and loaded-file fingerprint.

        Exclusive sibling .lock prevents concurrent cooperating writers. A crash
        can leave a stale lock; remove it only after ensuring no writer is active.
        Uncooperative editors can still race; the directory must be trusted.
        """
        if self._path is None:
            return
        failure = False
        lock_fd = None
        temp_path = None
        lock_path = self._path.with_name(self._path.name + ".lock")
        try:
            self._check_directory()
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            # Never overwrite an unknown token, even with explicit reauthorization.
            current = None
            try:
                current = _read_json(self._path, secure=True)
            except AuthenticationError as exc:
                if exc.code != Code.MISSING_CREDENTIALS:
                    raise
            if current is None:
                if self._observed_digest is not None:
                    raise AuthenticationError(Code.STORAGE_FAILED)
            else:
                self._validate_binding(current)
                if self._observed_digest is None or _digest(current) != self._observed_digest:
                    raise AuthenticationError(Code.STORAGE_FAILED)
            data = json.loads(credentials.to_json())
            data.update(gsheet_token_version=1, gsheet_connection=self._binding)
            # Google serialization does not always include type or token_uri.
            data.update(type="authorized_user", token_uri=_TOKEN_URI)
            descriptor, filename = tempfile.mkstemp(prefix=".gsheet-token-", dir=self._path.parent)
            temp_path = Path(filename)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                if os.name == "posix":
                    os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if current is None:
                os.link(temp_path, self._path)  # Atomic create; never clobber a new file.
            else:
                os.replace(temp_path, self._path)
            self._observed_digest = _digest(data)
        except (OSError, ValueError, TypeError):
            failure = True
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    failure = True
            if lock_fd is not None:
                os.close(lock_fd)
                try:
                    lock_path.unlink()
                except OSError:
                    failure = True
        if failure:
            raise AuthenticationError(Code.STORAGE_FAILED)
