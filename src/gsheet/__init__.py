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
from .client import APIResponse, RequestPreview, SheetsClient, SheetsEndpoint, SheetsTransport
from .core.exceptions import APIClientError, APIErrorCode, APIErrorReason, APIErrorSource
from .core.permissions import AuthorizationPolicy, OperationKind
from .core.exceptions import ServiceErrorCode, SheetServiceError
from .core.range_resolver import (
    column_a1_to_index, column_index_to_a1, format_a1_range, parse_a1_range,
    quote_sheet_name, row_a1_to_index, row_index_to_a1,
)
from .models import (CellInfo, CellRange, CellValue, GridDataResult, RangeValues, ReadMode,
                     ResolvedRange, StructuralChange, WorksheetInfo, WriteMode, WritePrecondition, WriteSafety)
from .models import DeveloperMetadataResult, DimensionInfo, DimensionsResult, WorksheetMetadata
from .services import SheetService

__all__ = [
    "APIConfig", "AuthConfig", "CacheConfig", "ConfigurationError",
    "GSheetConfig", "OperationalConfig", "RetryConfig",
    "AuthenticationError", "AuthenticationErrorCode", "AuthenticationStatus", "CredentialManager",
    "APIClientError", "APIErrorCode", "APIResponse", "AuthorizationPolicy", "OperationKind",
    "APIErrorReason", "APIErrorSource",
    "SheetsClient", "SheetsEndpoint", "SheetsTransport",
    "CellRange", "RangeValues", "ServiceErrorCode", "SheetService", "SheetServiceError",
    "CellValue", "ReadMode", "WriteMode",
    "CellInfo", "GridDataResult", "RequestPreview", "ResolvedRange", "WritePrecondition", "WriteSafety",
    "StructuralChange", "WorksheetInfo", "column_a1_to_index", "column_index_to_a1",
    "DeveloperMetadataResult", "DimensionInfo", "DimensionsResult", "WorksheetMetadata",
    "format_a1_range", "parse_a1_range", "quote_sheet_name", "row_a1_to_index", "row_index_to_a1",
]
