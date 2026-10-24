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
    UNCERTAIN_WRITE = "write_outcome_uncertain"
    HTTP_ERROR = "http_error"
    CLOSED = "transport_closed"


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
    APIErrorCode.UNCERTAIN_WRITE: "The write may have completed; inspect its outcome before issuing another mutation.",
    APIErrorCode.HTTP_ERROR: "Google rejected the API request.",
    APIErrorCode.CLOSED: "The transport is closed.",
}


class APIClientError(Exception):
    """Safe request failure; never retains response bodies, URLs, or exceptions."""

    def __init__(self, code: APIErrorCode, *, status_code: int | None = None,
                 retryable: bool = False, outcome: str = "confirmed_failure",
                 attempts: int = 0, request_id: str | None = None,
                 retry_after_seconds: float | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.outcome = outcome
        self.attempts = attempts
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(_API_MESSAGES[code])

    def to_dict(self) -> dict[str, str | bool | int | float | None]:
        return {"code": self.code.value, "message": str(self),
                "status_code": self.status_code, "retryable": self.retryable,
                "outcome": self.outcome, "attempts": self.attempts,
                "request_id": self.request_id, "retry_after_seconds": self.retry_after_seconds}
