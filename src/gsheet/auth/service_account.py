"""Load only the explicitly configured Google service-account identity."""

from google.oauth2.service_account import Credentials
from google.auth.exceptions import GoogleAuthError

from ..config import APIConfig, AuthConfig
from ..core.exceptions import AuthenticationError, AuthenticationErrorCode as Code
from .credentials import _quiet_auth_logging, _read_json


def load_service_account(auth: AuthConfig, api: APIConfig) -> Credentials:
    """Local key validation only; does not prove Google authorization/access."""
    if auth.mode != "service_account":
        raise AuthenticationError(Code.UNSUPPORTED_MODE)
    if auth.service_account_file is None:
        raise AuthenticationError(Code.MISSING_CREDENTIALS)
    info = _read_json(auth.service_account_file)
    if (info.get("type") != "service_account"
            or not all(isinstance(info.get(key), str) and info[key].strip()
                       for key in ("client_email", "private_key", "token_uri"))
            or info["token_uri"] != "https://oauth2.googleapis.com/token"
            or info.get("universe_domain", "googleapis.com") != "googleapis.com"):
        raise AuthenticationError(Code.INVALID_CREDENTIALS)
    credentials = None
    try:
        # No delegation, ADC discovery, or credential-family fallback.
        with _quiet_auth_logging():
            credentials = Credentials.from_service_account_info(info, scopes=api.scopes)
    except (GoogleAuthError, ValueError, TypeError, KeyError):
        pass
    if credentials is None:
        raise AuthenticationError(Code.INVALID_CREDENTIALS)
    return credentials
