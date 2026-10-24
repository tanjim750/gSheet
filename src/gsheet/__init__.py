"""Public configuration, authentication, and low-level API interfaces for gSheet."""

from .config import (
    APIConfig,
    AuthConfig,
    CacheConfig,
    ConfigurationError,
    GSheetConfig,
    OperationalConfig,
    RetryConfig,
)
from .auth import AuthenticationError, AuthenticationErrorCode, AuthenticationStatus, CredentialManager
from .client import APIResponse, SheetsClient, SheetsEndpoint, SheetsTransport
from .core.exceptions import APIClientError, APIErrorCode
from .core.permissions import AuthorizationPolicy, OperationKind

__all__ = [
    "APIConfig", "AuthConfig", "CacheConfig", "ConfigurationError",
    "GSheetConfig", "OperationalConfig", "RetryConfig",
    "AuthenticationError", "AuthenticationErrorCode", "AuthenticationStatus", "CredentialManager",
    "APIClientError", "APIErrorCode", "APIResponse", "AuthorizationPolicy", "OperationKind",
    "SheetsClient", "SheetsEndpoint", "SheetsTransport",
]
