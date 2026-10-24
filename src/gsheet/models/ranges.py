"""Physical zero-based ranges; end bounds are exclusive and may be unknown."""

from dataclasses import dataclass

from ..core.exceptions import ServiceErrorCode as Code, SheetServiceError


@dataclass(frozen=True)
class CellRange:
    start_row: int = 0
    start_column: int = 0
    end_row: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        for start, end in ((self.start_row, self.end_row), (self.start_column, self.end_column)):
            if type(start) is not int or start < 0 or (
                    end is not None and (type(end) is not int or end <= start)):
                raise SheetServiceError(Code.INVALID_RANGE, field="range")

    def bounded(self, row_count: int, column_count: int) -> "CellRange":
        if any(type(value) is not int or value <= 0 for value in (row_count, column_count)):
            raise SheetServiceError(Code.INVALID_RANGE, field="dimensions")
        result = CellRange(self.start_row, self.start_column,
                           self.end_row if self.end_row is not None else row_count,
                           self.end_column if self.end_column is not None else column_count)
        if result.end_row > row_count or result.end_column > column_count:
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        return result

    @property
    def cell_count(self) -> int:
        if self.end_row is None or self.end_column is None:
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        return (self.end_row - self.start_row) * (self.end_column - self.start_column)


@dataclass(frozen=True)
class ResolvedRange:
    spreadsheet_id: str
    sheet_id: int
    sheet_title: str
    region: CellRange

    @property
    def a1(self) -> str:
        from ..core.range_resolver import format_a1_range
        return format_a1_range(self.sheet_title, self.region)
