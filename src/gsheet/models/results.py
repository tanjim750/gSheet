"""Location-aware reads; None padding means unavailable, not a proven empty cell."""

from dataclasses import dataclass, field
from typing import Literal

from ..client import APIResponse
from .ranges import CellRange
from .spreadsheet import WorksheetInfo
from .cells import CellInfo

CellValue = str | int | float | bool | None
ReadMode = Literal["FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"]
WriteMode = Literal["RAW", "USER_ENTERED"]


@dataclass(frozen=True)
class RangeValues:
    spreadsheet_id: str
    worksheet: WorksheetInfo
    region: CellRange
    values: list[list[CellValue]] = field(repr=False)
    read_mode: ReadMode
    response: APIResponse = field(repr=False)


@dataclass(frozen=True)
class GridDataResult:
    spreadsheet_id: str
    worksheet: WorksheetInfo
    region: CellRange
    cells: tuple[CellInfo, ...] = field(repr=False)
    locale: str | None
    timezone: str | None
    response: APIResponse = field(repr=False)
