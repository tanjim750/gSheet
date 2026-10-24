"""Generic worksheet metadata; no header or business interpretation."""

from dataclasses import dataclass, field
from typing import Any

from .ranges import CellRange


@dataclass(frozen=True)
class WorksheetInfo:
    sheet_id: int
    title: str
    index: int
    row_count: int
    column_count: int
    merged_ranges: tuple[CellRange, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)
