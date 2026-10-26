"""Location-aware native metadata; absence is not an invented visibility state."""

from dataclasses import dataclass, field
from typing import Any

from ..client import APIResponse
from .ranges import CellRange
from .spreadsheet import WorksheetInfo


@dataclass(frozen=True)
class WorksheetMetadata:
    spreadsheet_id: str
    worksheet: WorksheetInfo
    requested_fields: tuple[str, ...]
    metadata: dict[str, Any] = field(repr=False)
    response: APIResponse = field(repr=False)


@dataclass(frozen=True)
class DimensionInfo:
    index: int
    requested_fields: tuple[str, ...]
    metadata: dict[str, Any] = field(repr=False)

    @property
    def hidden_by_user(self) -> bool | None:
        return self.metadata.get("hiddenByUser")

    @property
    def hidden_by_filter(self) -> bool | None:
        return self.metadata.get("hiddenByFilter")

    @property
    def pixel_size(self) -> int | None:
        return self.metadata.get("pixelSize")


@dataclass(frozen=True)
class DimensionsResult:
    spreadsheet_id: str
    worksheet: WorksheetInfo
    row_count: int
    column_count: int
    region: CellRange | None
    row_metadata: tuple[DimensionInfo, ...] = field(repr=False)
    column_metadata: tuple[DimensionInfo, ...] = field(repr=False)
    response: APIResponse = field(repr=False)


@dataclass(frozen=True)
class DeveloperMetadataResult:
    """Native developer entries scoped to the file, tab, or requested dimensions."""

    spreadsheet_id: str
    worksheet: WorksheetInfo | None
    region: CellRange | None
    metadata: tuple[dict[str, Any], ...] = field(repr=False)
    response: APIResponse = field(repr=False)
