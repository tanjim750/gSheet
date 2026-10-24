"""Immutable, connection-local configuration; no authentication or API execution.

GSheetConfig() loads GSHEET_<SECTION>_<FIELD> environment variables. Explicit
section objects replace that entire section (including its environment values).
Unset fields use the defaults below. Pass environ={} for deterministic defaults.
An explicitly supplied dotenv_path adds a lowest-priority environment source;
it never changes os.environ or searches parent directories for secret files.

Timeouts/backoffs/TTLs are seconds. Numeric defaults other than quota budgets
are application policy, not Google limits. Quota defaults are documented at
https://developers.google.com/workspace/sheets/api/limits ; quotas can vary by
project. Batch limits count ranges, not cells or bytes. Runtime enforcement is
the responsibility of future clients/services. Retry categories only identify
candidates: unsafe or uncertain writes must never be blindly replayed.

No raw keys or tokens are accepted. Credential/token locations are excluded
from repr and redacted by to_dict(); do not use generic object introspection
as a logging/serialization API. Relative paths are relative to the process cwd.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, get_args, get_type_hints

READ_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_SCOPES = frozenset({READ_SCOPE, WRITE_SCOPE,
                     "https://www.googleapis.com/auth/drive.readonly",
                     "https://www.googleapis.com/auth/drive.file",
                     "https://www.googleapis.com/auth/drive"})
_WRITE_SCOPES = _SCOPES - {READ_SCOPE, "https://www.googleapis.com/auth/drive.readonly"}
_RETRY_CATEGORIES = frozenset({"rate_limit", "temporary_upstream", "timeout"})


class ConfigurationError(ValueError):
    """Safe, structured error; never stores the rejected configuration value."""

    code = "invalid_configuration"

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.reason}


class _Section:
    def __post_init__(self) -> None:
        # Python annotations alone do not validate constructor inputs.
        for item in fields(self):
            value = getattr(self, item.name)
            expected = get_type_hints(type(self))[item.name]
            options = get_args(expected)
            if expected is bool:
                valid = type(value) is bool
            elif expected is int:
                valid = type(value) is int
            elif expected is float:
                valid = type(value) in (int, float)
                if valid:
                    try:
                        valid = math.isfinite(value)
                    except OverflowError:
                        valid = False
            elif expected is str:
                valid = isinstance(value, str) and bool(value.strip())
            elif expected == str | None:
                valid = value is None or (isinstance(value, str) and bool(value.strip()))
            elif expected == Path | None:
                valid = value is None or isinstance(value, (str, Path))
            elif expected == tuple[str, ...] or expected == tuple[str, ...] | None:
                valid = (value is None and type(None) in options) or (
                    isinstance(value, tuple) and all(isinstance(v, str) and v.strip() for v in value))
            else:  # Literal choices
                valid = isinstance(value, str) and value in options
            if not valid:
                raise ConfigurationError(item.name, "invalid type or unsupported value")


def _positive(name: str, value: float | int) -> None:
    if value <= 0:
        raise ConfigurationError(name, "must be greater than zero")


def _path(name: str, value: Path | str | None, *, output: bool = False) -> Path | None:
    if value is None:
        return None
    valid = False
    normalized = None
    try:
        if not str(value).strip() or "\x00" in str(value):
            raise ValueError
        normalized = Path(value).expanduser()
        valid = normalized.is_file() or (
            output and not normalized.exists() and normalized.parent.is_dir())
    except (OSError, RuntimeError, ValueError):
        pass
    if not valid:
        raise ConfigurationError(name, "must identify an existing file" if not output else
                                 "must identify a file or a new file in an existing directory")
    return normalized


@dataclass(frozen=True)
class APIConfig(_Section):
    access_mode: Literal["read_only", "read_write"] = "read_only"
    scopes: tuple[str, ...] | None = None
    request_timeout_seconds: float = 30.0
    application_name: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _positive("api.request_timeout_seconds", self.request_timeout_seconds)
        scopes = self.scopes
        if scopes is None:
            scopes = (READ_SCOPE if self.access_mode == "read_only" else WRITE_SCOPE,)
            object.__setattr__(self, "scopes", scopes)
        if not scopes or not set(scopes) <= _SCOPES:
            raise ConfigurationError("api.scopes", "GSHEET_API_SCOPES must contain supported raw HTTPS scope URLs; Markdown links, malformed URLs, and unsupported scopes are not accepted")
        if len(set(scopes)) != len(scopes):
            raise ConfigurationError("api.scopes", "provide distinct scopes without duplicates")
        has_write = bool(set(scopes) & _WRITE_SCOPES)
        if has_write != (self.access_mode == "read_write"):
            raise ConfigurationError("api.scopes", "scopes must match the selected access_mode")


@dataclass(frozen=True)
class AuthConfig(_Section):
    mode: Literal["none", "service_account", "oauth"] = "none"
    service_account_file: Path | None = field(default=None, repr=False, metadata={"sensitive": True})
    oauth_client_file: Path | None = field(default=None, repr=False, metadata={"sensitive": True})
    oauth_token_file: Path | None = field(default=None, repr=False, metadata={"sensitive": True})
    refresh_enabled: bool = True
    refresh_margin_seconds: float = 60.0

    def __post_init__(self) -> None:
        super().__post_init__()
        _positive("auth.refresh_margin_seconds", self.refresh_margin_seconds)
        if self.mode != "service_account" and self.service_account_file is not None:
            raise ConfigurationError("auth.service_account_file", "requires service_account mode")
        if self.mode != "oauth" and (self.oauth_client_file is not None or self.oauth_token_file is not None):
            raise ConfigurationError("auth.oauth_client_file", "OAuth file settings require oauth mode")
        for name in ("service_account_file", "oauth_client_file", "oauth_token_file"):
            object.__setattr__(self, name, _path(f"auth.{name}", getattr(self, name),
                                               output=name == "oauth_token_file"))

    def validate_for_auth(self) -> None:
        """Optional readiness check for file-based auth, not a login flow.

        OAuth token storage is optional (callers may use in-memory tokens).
        Configuration construction itself never requires credentials.
        """
        if self.mode == "none":
            raise ConfigurationError("auth.mode", "select service_account or oauth before authentication")
        name = "service_account_file" if self.mode == "service_account" else "oauth_client_file"
        if getattr(self, name) is None:
            raise ConfigurationError(f"auth.{name}", "supply a credential file before file-based authentication")


@dataclass(frozen=True)
class RetryConfig(_Section):
    max_attempts: int = 3  # Total attempts, including the first; 1 disables retries.
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 32.0
    multiplier: float = 2.0
    # Future full jitter samples [0, capped delay]; additive adds up to 1s before
    # capping; none leaves the capped delay unchanged. No delays execute here.
    jitter: Literal["none", "full", "additive"] = "full"
    retryable_categories: tuple[str, ...] = ("rate_limit", "temporary_upstream")

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("max_attempts", "initial_delay_seconds", "max_delay_seconds"):
            _positive(f"retry.{name}", getattr(self, name))
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ConfigurationError("retry.max_delay_seconds", "must be at least initial_delay_seconds")
        if self.multiplier <= 1:
            raise ConfigurationError("retry.multiplier", "must be greater than one")
        if len(set(self.retryable_categories)) != len(self.retryable_categories) or not set(self.retryable_categories) <= _RETRY_CATEGORIES:
            raise ConfigurationError("retry.retryable_categories", "use distinct rate_limit, temporary_upstream, or timeout categories")


@dataclass(frozen=True)
class CacheConfig(_Section):
    enabled: bool = False
    ttl_seconds: float = 60.0
    force_refresh: bool = False  # Bypass policy, even when the cache is enabled.
    max_entries: int = 128

    def __post_init__(self) -> None:
        super().__post_init__()
        _positive("cache.ttl_seconds", self.ttl_seconds)
        _positive("cache.max_entries", self.max_entries)


@dataclass(frozen=True)
class OperationalConfig(_Section):
    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    throttling_enabled: bool = True
    read_requests_per_minute_per_project: int = 300
    write_requests_per_minute_per_project: int = 300
    read_requests_per_minute_per_user: int = 60
    write_requests_per_minute_per_user: int = 60
    max_read_batch_ranges: int = 100
    max_write_batch_ranges: int = 100
    max_read_cells: int = 100_000

    def __post_init__(self) -> None:
        super().__post_init__()
        for item in fields(self):
            if item.name not in {"logging_level", "throttling_enabled"}:
                _positive(f"operations.{item.name}", getattr(self, item.name))


def _parse_env(name: str, raw: str, expected: Any) -> Any:
    valid = True
    result: Any = None
    try:
        if not isinstance(raw, str):
            raise ValueError
        if expected is bool:
            if raw not in {"true", "false"}:
                raise ValueError
            result = raw == "true"
        elif expected is int:
            result = int(raw)
        elif expected is float:
            result = float(raw)
        elif expected in (tuple[str, ...], tuple[str, ...] | None):
            result = () if raw == "" else tuple(part.strip() for part in raw.split(","))
        else:
            result = raw
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ConfigurationError(name, "invalid environment value; check the documented type")
    return result


def _environment(environ: Mapping[str, str] | None, dotenv_path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if dotenv_path is not None:
        path = _path("dotenv_path", dotenv_path)
        parser = None
        try:
            from dotenv import dotenv_values
            parser = dotenv_values
        except ImportError:
            pass
        if parser is None:
            raise ConfigurationError("dotenv_path", "install gsheet[dotenv] to load .env files")
        failed = False
        try:
            # No interpolation: .env references cannot import unrelated env secrets.
            with path.open(encoding="utf-8") as stream:
                content = stream.read()
            # Validate every line before python-dotenv can log malformed input.
            from dotenv.parser import parse_stream
            from io import StringIO
            if any(binding.error for binding in parse_stream(StringIO(content))):
                failed = True
            else:
                parsed = parser(stream=StringIO(content), interpolate=False)
                values.update({k: v for k, v in parsed.items() if v is not None})
        except (OSError, UnicodeError):
            failed = True
        if failed:
            raise ConfigurationError("dotenv_path", "cannot read or parse the selected .env file")
    values.update(os.environ if environ is None else environ)
    return values


@dataclass(frozen=True, init=False)
class GSheetConfig:
    """Explicit section > environment > explicit .env file > safe defaults.

    Each instance owns immutable sections. Loading snapshots environment values;
    changing the environment later does not modify an existing configuration.
    Spreadsheet allowlists and operation permissions must be supplied/enforced
    by the future authenticated connection layer; OAuth scopes do not replace them.
    """

    api: APIConfig
    auth: AuthConfig
    retry: RetryConfig
    cache: CacheConfig
    operations: OperationalConfig

    def __init__(self, *, api: APIConfig | None = None, auth: AuthConfig | None = None,
                 retry: RetryConfig | None = None, cache: CacheConfig | None = None,
                 operations: OperationalConfig | None = None,
                 environ: Mapping[str, str] | None = None,
                 dotenv_path: str | Path | None = None) -> None:
        environment = _environment(environ, dotenv_path)
        for name, cls, explicit in (("api", APIConfig, api), ("auth", AuthConfig, auth),
                                    ("retry", RetryConfig, retry), ("cache", CacheConfig, cache),
                                    ("operations", OperationalConfig, operations)):
            if explicit is not None:
                if not isinstance(explicit, cls):
                    raise ConfigurationError(name, "supply the matching typed configuration section")
                value = explicit
            else:
                hints = get_type_hints(cls)
                kwargs = {}
                for item in fields(cls):
                    key = f"GSHEET_{name}_{item.name}".upper()
                    if key in environment:
                        kwargs[item.name] = _parse_env(key, environment[key], hints[item.name])
                value = cls(**kwargs)
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Return JSON-compatible settings with credential locations redacted."""
        result = {}
        for section in fields(self):
            obj = getattr(self, section.name)
            result[section.name] = {
                item.name: ("[REDACTED]" if getattr(obj, item.name) is not None else None)
                if item.metadata.get("sensitive") else getattr(obj, item.name)
                for item in fields(obj)
            }
        return result
