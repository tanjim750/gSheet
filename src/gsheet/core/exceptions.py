"""Sanitized authentication and API errors with separate authorization categories."""

from enum import StrEnum


class AuthenticationErrorCode(StrEnum):
    MISSING_CREDENTIALS = "missing_credentials"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNSUPPORTED_MODE = "unsupported_authentication_mode"
    AUTHORIZATION_REQUIRED = "oauth_authorization_required"
    AUTHORIZATION_EXPIRED = "authorization_expired_or_revoked"
    REFRESH_UNAVAILABLE = "credential_refresh_unavailable"
    REFRESH_FAILED = "credential_refresh_failed"
    INSUFFICIENT_SCOPES = "insufficient_configured_scopes"
    STORAGE_FAILED = "credential_storage_failure"
    CONNECTION_MISMATCH = "credential_connection_mismatch"
    NETWORK_FAILED = "authentication_network_failure"
    INTERACTION_NOT_ALLOWED = "interactive_authorization_not_allowed"
    AUTHORIZATION_FAILED = "oauth_authorization_failed"


_MESSAGES = {
    AuthenticationErrorCode.MISSING_CREDENTIALS: "Provide the configured credential file before authentication.",
    AuthenticationErrorCode.INVALID_CREDENTIALS: "The configured credential file is unreadable or invalid.",
    AuthenticationErrorCode.UNSUPPORTED_MODE: "Select service_account or oauth authentication.",
    AuthenticationErrorCode.AUTHORIZATION_REQUIRED: "Explicit OAuth authorization is required for this connection.",
    AuthenticationErrorCode.AUTHORIZATION_EXPIRED: "Authorization expired or was revoked; explicitly reauthorize OAuth or replace service-account credentials.",
    AuthenticationErrorCode.REFRESH_UNAVAILABLE: "Refresh is disabled or no refresh credential is available.",
    AuthenticationErrorCode.REFRESH_FAILED: "Credential refresh failed; no alternative identity was selected.",
    AuthenticationErrorCode.INSUFFICIENT_SCOPES: "Credential scopes do not match the configured API scopes; reauthorize explicitly.",
    AuthenticationErrorCode.STORAGE_FAILED: "Credential storage is unavailable, insecure, locked, or changed; use a private connection-specific token file.",
    AuthenticationErrorCode.CONNECTION_MISMATCH: "Stored credentials belong to a different connection or OAuth client.",
    AuthenticationErrorCode.NETWORK_FAILED: "The authentication request could not reach Google.",
    AuthenticationErrorCode.INTERACTION_NOT_ALLOWED: "Pass allow_browser=True to explicitly permit local OAuth authorization.",
    AuthenticationErrorCode.AUTHORIZATION_FAILED: "OAuth authorization failed or timed out; no replacement credentials were installed.",
}


class AuthenticationError(Exception):
    """Contains only fixed messages and safe categories, never upstream details."""

    def __init__(self, code: AuthenticationErrorCode, *, retryable: bool = False,
                 requires_interaction: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        self.requires_interaction = requires_interaction
        super().__init__(_MESSAGES[code])

    def to_dict(self) -> dict[str, str | bool]:
        return {"code": self.code.value, "message": str(self),
                "retryable": self.retryable, "requires_interaction": self.requires_interaction}


class APIErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "api_authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "spreadsheet_not_found_or_inaccessible"
    QUOTA_EXCEEDED = "quota_exceeded"
    THROTTLED = "locally_throttled"
    TIMEOUT = "request_timeout"
    TRANSPORT = "transport_failure"
    UPSTREAM = "upstream_failure"
    MALFORMED_RESPONSE = "malformed_response"
    RESPONSE_TOO_LARGE = "response_size_limit_exceeded"
    UNCERTAIN_WRITE = "write_outcome_uncertain"
    HTTP_ERROR = "http_error"
    CLOSED = "transport_closed"


class ServiceErrorCode(StrEnum):
    INVALID_INPUT = "invalid_service_input"
    INVALID_RANGE = "invalid_service_range"
    WORKSHEET_NOT_FOUND = "worksheet_not_found"
    HEADER_NOT_FOUND = "header_not_found"
    AMBIGUOUS_HEADER = "ambiguous_header"
    UNSUPPORTED_LAYOUT = "unsupported_layout_operation"
    MALFORMED_RESPONSE = "malformed_service_response"
    PRECONDITION_FAILED = "write_precondition_failed"
    FORMULA_PROTECTED = "formula_overwrite_rejected"


class SheetServiceError(ValueError):
    """Fixed diagnostics; sheet values, titles, and input payloads are omitted."""

    def __init__(self, code: ServiceErrorCode, *, field: str | None = None) -> None:
        self.code, self.field = code, field
        self.outcome = "confirmed_failure" if code == ServiceErrorCode.MALFORMED_RESPONSE else "validation_rejection"
        self.retryable = False
        messages = {
            ServiceErrorCode.INVALID_INPUT: "Invalid service input; check the named argument.",
            ServiceErrorCode.INVALID_RANGE: "Range or index is invalid, out of grid bounds, or exceeds configured read limits.",
            ServiceErrorCode.WORKSHEET_NOT_FOUND: "Worksheet was not found in fresh spreadsheet metadata.",
            ServiceErrorCode.HEADER_NOT_FOUND: "No matching header or occurrence was found in the requested header row.",
            ServiceErrorCode.AMBIGUOUS_HEADER: "Multiple headers match; select an explicit occurrence or column.",
            ServiceErrorCode.UNSUPPORTED_LAYOUT: "This operation cannot safely target the current sheet layout.",
            ServiceErrorCode.MALFORMED_RESPONSE: "The API response does not match the expected service contract.",
            ServiceErrorCode.PRECONDITION_FAILED: "A write precondition was not satisfied or could not be established; resolve the target again.",
            ServiceErrorCode.FORMULA_PROTECTED: "The mutation would overwrite an existing formula under the selected protection policy.",
        }
        super().__init__(messages[code])

    def to_dict(self) -> dict[str, str | bool | None]:
        return {"code": self.code.value, "field": self.field, "message": str(self),
                "source": "service_response" if self.code == ServiceErrorCode.MALFORMED_RESPONSE else "service_validation",
                "outcome": self.outcome, "retryable": self.retryable}


_API_MESSAGES = {
    APIErrorCode.INVALID_REQUEST: "Invalid or unsupported Sheets API request.",
    APIErrorCode.AUTHENTICATION: "Google rejected authentication; check the connection before retrying.",
    APIErrorCode.PERMISSION_DENIED: "Spreadsheet or operation access was denied.",
    APIErrorCode.NOT_FOUND: "The spreadsheet was not found or is inaccessible.",
    APIErrorCode.QUOTA_EXCEEDED: "Google API quota was exceeded.",
    APIErrorCode.THROTTLED: "The local request budget is exhausted; retry after the indicated delay.",
    APIErrorCode.TIMEOUT: "The Google API request timed out.",
    APIErrorCode.TRANSPORT: "The Google API connection failed.",
    APIErrorCode.UPSTREAM: "Google reported a temporary upstream failure.",
    APIErrorCode.MALFORMED_RESPONSE: "Google returned an invalid or unexpected response.",
    APIErrorCode.RESPONSE_TOO_LARGE: "The response exceeded the configured byte limit; request fewer cells or metadata fields.",
    APIErrorCode.UNCERTAIN_WRITE: "The write may have completed; inspect its outcome before issuing another mutation.",
    APIErrorCode.HTTP_ERROR: "Google rejected the API request.",
    APIErrorCode.CLOSED: "The transport is closed.",
}


class APIErrorSource(StrEnum):
    CONFIGURATION = "configuration"
    REQUEST_VALIDATION = "request_validation"
    AUTHORIZATION = "local_authorization"
    UPSTREAM = "google_api"
    TRANSPORT = "transport"
    THROTTLING = "local_throttling"
    CLIENT = "client"


class APIErrorReason(StrEnum):
    READ_ONLY_CONFIGURATION = "read_only_configuration"
    SPREADSHEET_NOT_ALLOWED = "spreadsheet_not_allowed"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    BATCH_REQUEST_NOT_ALLOWED = "batch_request_not_allowed"


_REASON_MESSAGES = {
    APIErrorReason.READ_ONLY_CONFIGURATION: "The client's api.access_mode is read_only; writes require read_write.",
    APIErrorReason.SPREADSHEET_NOT_ALLOWED: "The target spreadsheet is not allowed by this connection's authorization policy.",
    APIErrorReason.OPERATION_NOT_ALLOWED: "The authorization policy does not grant every operation required by this request; see missing_operations.",
    APIErrorReason.BATCH_REQUEST_NOT_ALLOWED: "A structural request type is not allowed by this connection's authorization policy.",
}


class APIClientError(Exception):
    """Safe request failure; never retains response bodies, URLs, or exceptions."""

    def __init__(self, code: APIErrorCode, *, status_code: int | None = None,
                 retryable: bool = False, outcome: str = "confirmed_failure",
                 attempts: int = 0, request_id: str | None = None,
                 retry_after_seconds: float | None = None,
                 source: APIErrorSource | None = None, reason: APIErrorReason | None = None,
                 missing_operations: tuple[str, ...] = ()) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.outcome = outcome
        self.attempts = attempts
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        if source is None:
            source = APIErrorSource.UPSTREAM if status_code is not None else {
                APIErrorCode.INVALID_REQUEST: APIErrorSource.REQUEST_VALIDATION,
                APIErrorCode.PERMISSION_DENIED: APIErrorSource.AUTHORIZATION,
                APIErrorCode.THROTTLED: APIErrorSource.THROTTLING,
                APIErrorCode.TIMEOUT: APIErrorSource.TRANSPORT,
                APIErrorCode.TRANSPORT: APIErrorSource.TRANSPORT,
                APIErrorCode.MALFORMED_RESPONSE: APIErrorSource.TRANSPORT,
                APIErrorCode.RESPONSE_TOO_LARGE: APIErrorSource.TRANSPORT,
                APIErrorCode.UNCERTAIN_WRITE: APIErrorSource.TRANSPORT,
            }.get(code, APIErrorSource.CLIENT)
        if any(value not in {"read", "write", "structural", "destructive"} for value in missing_operations):
            raise ValueError("Unsupported operation diagnostic")
        self.source = source
        self.reason = reason
        self.missing_operations = tuple(missing_operations)
        super().__init__(_REASON_MESSAGES[reason] if reason is not None else _API_MESSAGES[code])

    def to_dict(self) -> dict[str, str | bool | int | float | None | tuple[str, ...]]:
        return {"code": self.code.value, "message": str(self),
                "status_code": self.status_code, "retryable": self.retryable,
                "outcome": self.outcome, "attempts": self.attempts,
                "request_id": self.request_id, "retry_after_seconds": self.retry_after_seconds,
                "source": self.source.value, "reason": self.reason.value if self.reason is not None else None,
                "missing_operations": self.missing_operations}
