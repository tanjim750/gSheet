"""Low-level Sheets endpoint selection; no layouts, repositories, or business rules."""

from collections.abc import Mapping
from dataclasses import replace
import json
import re
from threading import RLock
from typing import Any

from ..auth import CredentialManager
from ..config import GSheetConfig
from ..core.permissions import AuthorizationPolicy
from ..core.exceptions import APIClientError, APIErrorCode
from ..core.cache import MetadataCache
from .transport import APIResponse, RequestPreview, SheetsEndpoint, SheetsTransport


class SheetsClient:
    """Connection-local synchronous client; owns its transport and closes it.

    Endpoint wrappers only construct API requests. The transport validates,
    authorizes, authenticates, throttles, and executes them. params uses Google's
    native camelCase parameter names. Responses preserve Google's JSON contract.
    """

    def __init__(self, config: GSheetConfig, credentials: CredentialManager, *,
                 authorization: AuthorizationPolicy | None = None) -> None:
        self._transport = SheetsTransport(config, credentials, authorization=authorization)
        self._cache_config = config.cache
        self._metadata_cache = MetadataCache(config.cache.ttl_seconds, config.cache.max_entries)
        self._lock = RLock()

    def __repr__(self) -> str:
        return "SheetsClient(<isolated connection>)"

    def close(self) -> None:
        with self._lock:
            self._metadata_cache.clear()
            self._transport.close()

    def invalidate_metadata(self) -> None:
        """Forget this client's metadata without changing credentials or grants."""
        with self._lock:
            self._metadata_cache.clear()

    def __enter__(self) -> "SheetsClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def execute(self, endpoint: SheetsEndpoint, spreadsheet_id: str, *,
                range_name: str | None = None, params: Mapping[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> APIResponse:
        """Generic access to the supported endpoint registry, without arbitrary URLs."""
        with self._lock:
            try:
                return self._transport.request(endpoint, spreadsheet_id, range_name=range_name, params=params, body=body)
            finally:
                # An uncertain mutation may have changed metadata remotely.
                if endpoint not in (SheetsEndpoint.SPREADSHEET_GET, SheetsEndpoint.VALUES_GET, SheetsEndpoint.VALUES_BATCH_GET):
                    self._metadata_cache.clear()

    def validate_request(self, endpoint: SheetsEndpoint, spreadsheet_id: str, *,
                         range_name: str | None = None, params: Mapping[str, Any] | None = None,
                         body: dict[str, Any] | None = None) -> RequestPreview:
        """Authorize/validate an operation without obtaining credentials or sending it."""
        return self._transport.validate_request(endpoint, spreadsheet_id, range_name=range_name,
                                                 params=params, body=body)

    def get_spreadsheet(self, spreadsheet_id: str, *, params: Mapping[str, Any] | None = None,
                        force_refresh: bool = False) -> APIResponse:
        """Optionally cache metadata only; grid/cell data always reaches Google.

        Every hit is authorized through the same transport preflight. A cache hit
        retains the original fetch's HTTP/request metadata and reports its age.
        execute(SPREADSHEET_GET, ...) remains an uncached low-level escape hatch.
        """
        if type(force_refresh) is not bool:
            raise APIClientError(APIErrorCode.INVALID_REQUEST, outcome="validation_rejection")
        with self._lock:
            if not self._cache_config.enabled:
                return self.execute(SheetsEndpoint.SPREADSHEET_GET, spreadsheet_id, params=params)
            preview = self.validate_request(SheetsEndpoint.SPREADSHEET_GET, spreadsheet_id, params=params)
            mask = preview.params.get("fields", "")
            cacheable = (self._cache_config.enabled and preview.params.get("includeGridData") == "false"
                         and "*" not in mask and re.search(r"\bdata\b", mask) is None)
            key = json.dumps([spreadsheet_id, preview.params], sort_keys=True)
            if cacheable and not (force_refresh or self._cache_config.force_refresh):
                hit = self._metadata_cache.get(key)
                if hit is not None:
                    value, age = hit
                    return replace(value, cache_hit=True, cache_age_seconds=age)
            result = self.execute(SheetsEndpoint.SPREADSHEET_GET, spreadsheet_id, params=params)
            if cacheable:
                self._metadata_cache.put(key, result)
            return result

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
