"""Synchronous Sheets v4 transport with fixed routes and fail-closed permissions.

No arbitrary URLs, redirect following, implicit credential refresh, or HTTP-layer
retries. Endpoint validation lives at this network boundary; SheetsClient only
provides convenient endpoint selection. Successful payloads are untrusted data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from random import Random
from threading import RLock
from typing import Any
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ChunkedEncodingError, ConnectionError, ContentDecodingError, RequestException,
    SSLError, Timeout,
)

from ..auth import AuthenticationError, CredentialManager
from ..auth.credentials import _quiet_auth_logging
from ..config import ConfigurationError, GSheetConfig
from ..core.exceptions import APIClientError, APIErrorCode as Code
from ..core.permissions import AuthorizationPolicy, OperationKind as Op


class SheetsEndpoint(StrEnum):
    SPREADSHEET_GET = "spreadsheets.get"
    VALUES_GET = "spreadsheets.values.get"
    VALUES_BATCH_GET = "spreadsheets.values.batchGet"
    VALUES_UPDATE = "spreadsheets.values.update"
    VALUES_BATCH_UPDATE = "spreadsheets.values.batchUpdate"
    VALUES_APPEND = "spreadsheets.values.append"
    VALUES_CLEAR = "spreadsheets.values.clear"
    SPREADSHEET_BATCH_UPDATE = "spreadsheets.batchUpdate"


# method, resource suffix, requires range, endpoint-specific query parameters
_ROUTES = {
    SheetsEndpoint.SPREADSHEET_GET: ("GET", "", False, {"ranges", "includeGridData"}),
    SheetsEndpoint.VALUES_GET: ("GET", "/values/{range}", True, {"majorDimension", "valueRenderOption", "dateTimeRenderOption"}),
    SheetsEndpoint.VALUES_BATCH_GET: ("GET", "/values:batchGet", False, {"ranges", "majorDimension", "valueRenderOption", "dateTimeRenderOption"}),
    SheetsEndpoint.VALUES_UPDATE: ("PUT", "/values/{range}", True, {"valueInputOption", "includeValuesInResponse", "responseValueRenderOption", "responseDateTimeRenderOption"}),
    SheetsEndpoint.VALUES_BATCH_UPDATE: ("POST", "/values:batchUpdate", False, set()),
    SheetsEndpoint.VALUES_APPEND: ("POST", "/values/{range}:append", True, {"valueInputOption", "insertDataOption", "includeValuesInResponse", "responseValueRenderOption", "responseDateTimeRenderOption"}),
    SheetsEndpoint.VALUES_CLEAR: ("POST", "/values/{range}:clear", True, set()),
    SheetsEndpoint.SPREADSHEET_BATCH_UPDATE: ("POST", ":batchUpdate", False, set()),
}
_BATCH_TYPES = frozenset({"addSheet", "duplicateSheet", "insertDimension", "appendDimension",
    "deleteSheet", "deleteDimension", "mergeCells", "unmergeCells", "updateSheetProperties",
    "updateDimensionProperties", "repeatCell", "setDataValidation", "updateCells", "appendCells",
    "copyPaste", "cutPaste", "moveDimension", "addNamedRange", "updateNamedRange",
    "deleteNamedRange", "updateBorders", "autoResizeDimensions", "sortRange"})
_DESTRUCTIVE_TYPES = frozenset({"deleteSheet", "deleteDimension", "deleteNamedRange",
    "mergeCells", "cutPaste", "moveDimension", "sortRange", "updateSheetProperties"})
_BOOL_PARAMS = frozenset({"prettyPrint", "includeGridData", "includeValuesInResponse"})
_CHOICES = {
    "majorDimension": {"ROWS", "COLUMNS"},
    "valueRenderOption": {"FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"},
    "responseValueRenderOption": {"FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"},
    "dateTimeRenderOption": {"SERIAL_NUMBER", "FORMATTED_STRING"},
    "responseDateTimeRenderOption": {"SERIAL_NUMBER", "FORMATTED_STRING"},
    "valueInputOption": {"RAW", "USER_ENTERED"},
    "insertDataOption": {"OVERWRITE", "INSERT_ROWS"},
}


def _reject() -> None:
    raise APIClientError(Code.INVALID_REQUEST, outcome="validation_rejection")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(
        ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)


def _snapshot(body: Any) -> dict[str, Any]:
    def check(value: Any) -> None:
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise ValueError
            for item in value.values():
                check(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                check(item)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError
    result = None
    try:
        if isinstance(body, dict):
            check(body)
            result = json.loads(json.dumps(body, allow_nan=False))
    except (ValueError, TypeError, OverflowError, RecursionError):
        pass
    if result is None:
        _reject()
    return result


def _value_range(body: dict[str, Any], *, require_range: bool) -> None:
    if not set(body) <= {"range", "majorDimension", "values"}:
        _reject()
    if (require_range or "range" in body) and not _text(body.get("range")):
        _reject()
    if "majorDimension" in body and body["majorDimension"] not in ("ROWS", "COLUMNS"):
        _reject()
    values = body.get("values")
    if not isinstance(values, list) or not values or not all(isinstance(row, list) for row in values):
        _reject()
    if not all(cell is None or type(cell) in (str, int, float, bool) for row in values for cell in row):
        _reject()


@dataclass(frozen=True)
class APIResponse:
    """Raw Google JSON plus safe request metadata; data is excluded from repr."""

    data: dict[str, Any] = field(repr=False)
    status_code: int
    request_id: str
    attempts: int
    elapsed_seconds: float


class SheetsTransport:
    """One synchronous connection, credential manager, policy, and HTTP session.

    Construction performs no auth or network work. Authorization defaults deny
    all. Trusted callers must use the matching connection's config/manager/policy.
    Local rolling-minute budgets do not coordinate projects across processes.
    A full local budget fails immediately with retry_after_seconds (backpressure).
    """

    def __init__(self, config: GSheetConfig, credentials: CredentialManager, *,
                 authorization: AuthorizationPolicy | None = None) -> None:
        if not isinstance(config, GSheetConfig) or not isinstance(credentials, CredentialManager):
            raise ConfigurationError("transport", "supply GSheetConfig and CredentialManager")
        if authorization is not None and not isinstance(authorization, AuthorizationPolicy):
            raise ConfigurationError("authorization", "supply AuthorizationPolicy")
        app = config.api.application_name
        if app is not None and (not app.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in app)):
            raise ConfigurationError("api.application_name", "use printable ASCII for HTTP identification")
        self._config = config
        self._credentials = credentials
        self._authorization = authorization if authorization is not None else AuthorizationPolicy()
        self._session = Session()
        self._session.trust_env = False  # No implicit netrc identity or proxy context.
        self._session.mount("https://", HTTPAdapter(max_retries=0))
        self._session.headers.update({"Accept": "application/json", "User-Agent": app or "gsheet/0.1.0"})
        self._lock = RLock()
        self._closed = False
        self._history: dict[str, deque[float]] = {"read": deque(), "write": deque()}
        self._random = Random()
        # Instance-local level; no changes to application/global logger settings.
        self._logger = logging.Logger("gsheet.client.transport", level=config.operations.logging_level)
        self._logger.parent = logging.getLogger("gsheet")

    def __repr__(self) -> str:
        return "SheetsTransport(<isolated connection>)"

    def close(self) -> None:
        with self._lock:
            self._session.close()
            self._closed = True

    def __enter__(self) -> SheetsTransport:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _prepare(self, endpoint: SheetsEndpoint, spreadsheet_id: str, range_name: str | None,
                 params: Mapping[str, Any] | None, body: dict[str, Any] | None
                 ) -> tuple[str, str, dict[str, Any], dict[str, Any] | None, bool]:
        if not isinstance(endpoint, SheetsEndpoint) or not _text(spreadsheet_id) or spreadsheet_id in {".", ".."}:
            _reject()
        method, suffix, ranged, allowed = _ROUTES[endpoint]
        if ranged != (range_name is not None) or (ranged and (not _text(range_name) or range_name in (".", ".."))):
            _reject()
        if params is not None and not isinstance(params, Mapping):
            _reject()
        query = dict(params) if params is not None else {}
        if not set(query) <= allowed | {"fields", "prettyPrint"}:
            _reject()
        for key, value in query.items():
            if key in _BOOL_PARAMS:
                if type(value) is not bool:
                    _reject()
            elif key == "ranges":
                if not isinstance(value, (tuple, list)) or not value or not all(_text(v) for v in value):
                    _reject()
                query[key] = list(value)
                if len(value) > self._config.operations.max_read_batch_ranges:
                    _reject()
            elif not _text(value) or (key in _CHOICES and value not in _CHOICES[key]):
                _reject()
        read = method == "GET"
        data = None
        operations = {Op.READ} if read else {Op.WRITE}
        batch_types: frozenset[str] = frozenset()
        if read:
            if body is not None:
                _reject()
            if endpoint == SheetsEndpoint.VALUES_BATCH_GET and "ranges" not in query:
                _reject()
            if endpoint == SheetsEndpoint.SPREADSHEET_GET:
                query.setdefault("includeGridData", False)
                if not query["includeGridData"]:
                    query.setdefault("fields", "spreadsheetId,properties,sheets.properties")
        else:
            data = _snapshot(body)
            if endpoint in {SheetsEndpoint.VALUES_UPDATE, SheetsEndpoint.VALUES_APPEND}:
                _value_range(data, require_range=False)
                if "range" in data and data["range"] != range_name:
                    _reject()
                query.setdefault("valueInputOption", "RAW")
            elif endpoint == SheetsEndpoint.VALUES_BATCH_UPDATE:
                if not set(data) <= {"data", "valueInputOption", "includeValuesInResponse", "responseValueRenderOption", "responseDateTimeRenderOption"}:
                    _reject()
                ranges = data.get("data")
                if not isinstance(ranges, list) or not ranges or len(ranges) > self._config.operations.max_write_batch_ranges:
                    _reject()
                for item in ranges:
                    if not isinstance(item, dict):
                        _reject()
                    _value_range(item, require_range=True)
                data.setdefault("valueInputOption", "RAW")
                for key, choices in _CHOICES.items():
                    if key in data and (not isinstance(data[key], str) or data[key] not in choices):
                        _reject()
                if "includeValuesInResponse" in data and type(data["includeValuesInResponse"]) is not bool:
                    _reject()
            elif endpoint == SheetsEndpoint.VALUES_CLEAR:
                if data:
                    _reject()
                operations.add(Op.DESTRUCTIVE)
            else:
                if not set(data) <= {"requests", "includeSpreadsheetInResponse", "responseRanges", "responseIncludeGridData"}:
                    _reject()
                requests = data.get("requests")
                if not isinstance(requests, list) or not requests:
                    _reject()
                names = set()
                for request in requests:
                    if not isinstance(request, dict) or len(request) != 1:
                        _reject()
                    name = next(iter(request))
                    if name not in _BATCH_TYPES or not isinstance(request[name], dict):
                        _reject()
                    names.add(name)
                for key in ("includeSpreadsheetInResponse", "responseIncludeGridData"):
                    if key in data and type(data[key]) is not bool:
                        _reject()
                if "responseRanges" in data and (not isinstance(data["responseRanges"], list)
                        or not all(_text(v) for v in data["responseRanges"])
                        or len(data["responseRanges"]) > self._config.operations.max_read_batch_ranges):
                    _reject()
                batch_types = frozenset(names)
                operations.add(Op.STRUCTURAL)
                if names & _DESTRUCTIVE_TYPES:
                    operations.add(Op.DESTRUCTIVE)
            if query.get("includeValuesInResponse") is True or data.get("includeValuesInResponse") is True or data.get("includeSpreadsheetInResponse") is True:
                operations.add(Op.READ)
            if self._config.api.access_mode != "read_write":
                raise APIClientError(Code.PERMISSION_DENIED, outcome="validation_rejection")
        self._authorization.authorize(spreadsheet_id, frozenset(operations), batch_types)
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id, safe="")
        url += suffix.replace("{range}", quote(range_name, safe="") if ranged else "")
        # requests otherwise serializes bools as True/False instead of API JSON literals.
        query = {key: str(value).lower() if type(value) is bool else value for key, value in query.items()}
        return method, url, query, data, read

    def _budget(self, read: bool, request_id: str, attempts: int) -> None:
        settings = self._config.operations
        if not settings.throttling_enabled:
            return
        kind = "read" if read else "write"
        limit = min(getattr(settings, f"{kind}_requests_per_minute_per_project"),
                    getattr(settings, f"{kind}_requests_per_minute_per_user"))
        history = self._history[kind]
        now = time.monotonic()
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= limit:
            raise APIClientError(Code.THROTTLED, retryable=True, attempts=attempts,
                                 request_id=request_id, retry_after_seconds=max(0.0, 60 - (now - history[0])))
        history.append(now)

    def _delay(self, attempt: int) -> float:
        settings = self._config.retry
        try:
            delay = min(settings.max_delay_seconds, settings.initial_delay_seconds * settings.multiplier ** (attempt - 1))
        except OverflowError:
            delay = settings.max_delay_seconds
        if settings.jitter == "full":
            return self._random.uniform(0, delay)
        if settings.jitter == "additive":
            return min(settings.max_delay_seconds, delay + self._random.random())
        return delay

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        result = None
        try:
            if value is not None:
                try:
                    result = float(value)
                except ValueError:
                    result = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            pass
        return max(0.0, result) if result is not None and math.isfinite(result) else None

    def _observe(self, endpoint: Any, spreadsheet_id: Any, request_id: str, attempts: int,
                 start: float, status: int | None, outcome: str) -> None:
        self._logger.log(logging.INFO if outcome == "confirmed_success" else logging.WARNING,
            "gsheet_request", extra={"request_id": request_id,
                "operation": endpoint.value if isinstance(endpoint, SheetsEndpoint) else "unsupported",
                "target": hashlib.sha256(spreadsheet_id.encode(errors="replace")).hexdigest()[:16] if isinstance(spreadsheet_id, str) else None,
                "attempt": attempts, "latency_seconds": time.monotonic() - start,
                "http_status": status, "outcome": outcome})

    def request(self, endpoint: SheetsEndpoint, spreadsheet_id: str, *,
                range_name: str | None = None, params: Mapping[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> APIResponse:
        """Execute a supported endpoint; callers cannot label writes as safe reads."""
        with self._lock:
            if self._closed:
                raise APIClientError(Code.CLOSED)
            request_id = uuid.uuid4().hex
            start = time.monotonic()
            try:
                method, url, query, data, read = self._prepare(endpoint, spreadsheet_id, range_name, params, body)
            except APIClientError as exc:
                exc.request_id = request_id
                self._observe(endpoint, spreadsheet_id, request_id, 0, start, None, exc.outcome)
                raise
            attempts = 0
            while True:
                try:
                    self._budget(read, request_id, attempts)
                    credentials = self._credentials.get_credentials()
                except (APIClientError, AuthenticationError) as exc:
                    self._observe(endpoint, spreadsheet_id, request_id, attempts, start, None, exc.code.value)
                    raise
                attempts += 1
                response = None
                failure = None
                category = None
                hint = None
                payload = None
                status = None
                try:
                    with _quiet_auth_logging():
                        headers: dict[str, str] = {}
                        credentials.apply(headers)  # No before_request/implicit refresh.
                        self._session.cookies.clear()
                        response = self._session.request(method, url, params=query, json=data,
                            headers=headers, timeout=self._config.api.request_timeout_seconds,
                            allow_redirects=False)
                        status = response.status_code
                        hint = self._retry_after(response.headers.get("Retry-After"))
                        try:
                            payload = response.json() if status != 204 else {}
                        except (ValueError, RecursionError):
                            payload = None
                    if 200 <= status < 300:
                        if not isinstance(payload, dict) or "error" in payload:
                            failure = Code.MALFORMED_RESPONSE
                    else:
                        failure = self._http_error(status, payload)
                        if failure == Code.QUOTA_EXCEEDED:
                            category = "rate_limit"
                        elif failure == Code.TIMEOUT:
                            category = "timeout"
                        elif status in {500, 502, 503, 504}:
                            category = "temporary_upstream"
                except Timeout:
                    failure, category = Code.TIMEOUT, "timeout"
                except SSLError:
                    failure = Code.TRANSPORT  # Certificate failures are not transient.
                except (ConnectionError, ChunkedEncodingError):
                    failure, category = Code.TRANSPORT, "temporary_upstream"
                except ContentDecodingError:
                    failure = Code.MALFORMED_RESPONSE
                except RequestException:
                    failure = Code.TRANSPORT
                except (GoogleAuthError, ValueError, TypeError, UnicodeError):
                    failure = Code.AUTHENTICATION
                finally:
                    if response is not None:
                        response.close()
                    self._session.cookies.clear()
                elapsed = time.monotonic() - start
                uncertain = failure is not None and not read and (
                    failure in {Code.TIMEOUT, Code.TRANSPORT, Code.MALFORMED_RESPONSE}
                    or status is not None and status >= 500
                    or status is not None and 300 <= status < 400)
                self._observe(endpoint, spreadsheet_id, request_id, attempts, start, status,
                              "uncertain" if uncertain else failure.value if failure else "confirmed_success")
                if failure is None:
                    return APIResponse(payload, status, request_id, attempts, elapsed)
                if uncertain:
                    raise APIClientError(Code.UNCERTAIN_WRITE, status_code=status, outcome="uncertain",
                                         attempts=attempts, request_id=request_id)
                retryable = read and category is not None
                if (retryable and category in self._config.retry.retryable_categories
                        and attempts < self._config.retry.max_attempts
                        and (hint is None or hint <= self._config.retry.max_delay_seconds)):
                    time.sleep(max(self._delay(attempts), hint or 0))
                    continue
                raise APIClientError(failure, status_code=status, retryable=retryable,
                                     attempts=attempts, request_id=request_id, retry_after_seconds=hint)

    @staticmethod
    def _http_error(status: int, payload: Any) -> Code:
        reasons = set()
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            errors = payload["error"].get("errors", [])
            if isinstance(errors, list):
                reasons = {error.get("reason") for error in errors if isinstance(error, dict)
                           and isinstance(error.get("reason"), str)}
        if status == 429 or status == 403 and reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
            return Code.QUOTA_EXCEEDED
        return {400: Code.INVALID_REQUEST, 401: Code.AUTHENTICATION, 403: Code.PERMISSION_DENIED,
                404: Code.NOT_FOUND, 408: Code.TIMEOUT, 500: Code.UPSTREAM, 502: Code.UPSTREAM,
                503: Code.UPSTREAM, 504: Code.UPSTREAM}.get(status, Code.HTTP_ERROR)
