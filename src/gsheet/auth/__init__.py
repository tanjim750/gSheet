"""Authentication entry point; Google credential objects are client-internal."""

from .credentials import AuthenticationStatus, CredentialManager
from ..core.exceptions import AuthenticationError, AuthenticationErrorCode

__all__ = ["AuthenticationStatus", "CredentialManager", "AuthenticationError", "AuthenticationErrorCode"]
