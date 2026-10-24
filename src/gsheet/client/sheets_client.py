"""Low-level Sheets endpoint selection; no layouts, repositories, or business rules."""

from collections.abc import Mapping
from typing import Any

from ..auth import CredentialManager
from ..config import GSheetConfig
from ..core.permissions import AuthorizationPolicy
from ..core.exceptions import APIClientError, APIErrorCode
from .transport import APIResponse, SheetsEndpoint, SheetsTransport


class SheetsClient:
    """Connection-local synchronous client; owns its transport and closes it.

    Endpoint wrappers only construct API requests. The transport validates,
    authorizes, authenticates, throttles, and executes them. params uses Google's
    native camelCase parameter names. Responses preserve Google's JSON contract.
    """

    def __init__(self, config: GSheetConfig, credentials: CredentialManager, *,
                 authorization: AuthorizationPolicy | None = None) -> None:
        self._transport = SheetsTransport(config, credentials, authorization=authorization)

    def __repr__(self) -> str:
        return "SheetsClient(<isolated connection>)"

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "SheetsClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def execute(self, endpoint: SheetsEndpoint, spreadsheet_id: str, *,
                range_name: str | None = None, params: Mapping[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> APIResponse:
        """Generic access to the supported endpoint registry, without arbitrary URLs."""
        return self._transport.request(endpoint, spreadsheet_id, range_name=range_name, params=params, body=body)

    def get_spreadsheet(self, spreadsheet_id: str, *, params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.SPREADSHEET_GET, spreadsheet_id, params=params)

    def get_values(self, spreadsheet_id: str, range_name: str, *,
                   params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.VALUES_GET, spreadsheet_id, range_name=range_name, params=params)

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str] | tuple[str, ...], *,
                         params: Mapping[str, Any] | None = None) -> APIResponse:
        # ranges is explicit, so it takes precedence over the optional params map.
        if params is not None and not isinstance(params, Mapping):
            raise APIClientError(APIErrorCode.INVALID_REQUEST, outcome="validation_rejection")
        return self.execute(SheetsEndpoint.VALUES_BATCH_GET, spreadsheet_id,
                            params={**(params or {}), "ranges": ranges})

    def update_values(self, spreadsheet_id: str, range_name: str, body: dict[str, Any], *,
                      params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.VALUES_UPDATE, spreadsheet_id, range_name=range_name,
                            params=params, body=body)

    def batch_update_values(self, spreadsheet_id: str, body: dict[str, Any], *,
                            params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.VALUES_BATCH_UPDATE, spreadsheet_id, params=params, body=body)

    def append_values(self, spreadsheet_id: str, range_name: str, body: dict[str, Any], *,
                      params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.VALUES_APPEND, spreadsheet_id, range_name=range_name,
                            params=params, body=body)

    def clear_values(self, spreadsheet_id: str, range_name: str, *,
                     params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.VALUES_CLEAR, spreadsheet_id, range_name=range_name,
                            params=params, body={})

    def batch_update(self, spreadsheet_id: str, body: dict[str, Any], *,
                     params: Mapping[str, Any] | None = None) -> APIResponse:
        return self.execute(SheetsEndpoint.SPREADSHEET_BATCH_UPDATE, spreadsheet_id, params=params, body=body)
