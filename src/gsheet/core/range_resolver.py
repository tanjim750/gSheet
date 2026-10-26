"""A1 conversions with explicit worksheet identity; named ranges are not inferred."""

import re

from ..models.ranges import CellRange
from .exceptions import ServiceErrorCode as Code, SheetServiceError

_CELL = re.compile(r"\$?([A-Za-z]+)\$?([1-9][0-9]*)\Z")
_COLUMN = re.compile(r"\$?([A-Za-z]+)\Z")
_ROW = re.compile(r"\$?([1-9][0-9]*)\Z")


def index(value: int, field: str = "index", *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise SheetServiceError(Code.INVALID_INPUT, field=field)
    return value


def column_index_to_a1(column: int) -> str:
    index(column, "column")
    result = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def column_a1_to_index(column: str) -> int:
    if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z]+", column):
        raise SheetServiceError(Code.INVALID_RANGE, field="column")
    result = 0
    for character in column.upper():
        result = result * 26 + ord(character) - 64
    return result - 1


def row_index_to_a1(row: int) -> str:
    return str(index(row, "row") + 1)


def row_a1_to_index(row: str) -> int:
    if not isinstance(row, str) or not re.fullmatch(r"[1-9][0-9]*", row):
        raise SheetServiceError(Code.INVALID_RANGE, field="row")
    try:
        return int(row) - 1
    except ValueError:
        pass
    raise SheetServiceError(Code.INVALID_RANGE, field="row")


def quote_sheet_name(title: str) -> str:
    if not isinstance(title, str) or not title.strip() or any(
            ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in title):
        raise SheetServiceError(Code.INVALID_INPUT, field="worksheet")
    return "'" + title.replace("'", "''") + "'"


def parse_a1_range(reference: str) -> tuple[str | None, CellRange]:
    """Parse cell, rectangle, whole-row/column, and A1:B/A1:5 open references."""
    if not isinstance(reference, str) or not reference:
        raise SheetServiceError(Code.INVALID_RANGE, field="range")
    title = None
    if reference.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')+)'!(.+)", reference)
        if match is None:
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        title, reference = match.group(1).replace("''", "'"), match.group(2)
    elif "!" in reference:
        title, reference = reference.split("!", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", title):
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
    parts = reference.split(":")
    if len(parts) not in (1, 2):
        raise SheetServiceError(Code.INVALID_RANGE, field="range")

    def point(value: str) -> tuple[int | None, int | None]:
        cell, column, row = _CELL.fullmatch(value), _COLUMN.fullmatch(value), _ROW.fullmatch(value)
        if cell:
            return row_a1_to_index(cell[2]), column_a1_to_index(cell[1])
        if column:
            return None, column_a1_to_index(column[1])
        if row:
            return row_a1_to_index(row[1]), None
        raise SheetServiceError(Code.INVALID_RANGE, field="range")

    start_row, start_column = point(parts[0])
    if len(parts) == 1:
        if start_row is None or start_column is None:
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        end_row, end_column = start_row + 1, start_column + 1
    else:
        if parts[1] == "" and start_row is not None and start_column is not None:
            last_row, last_column = None, None
        else:
            last_row, last_column = point(parts[1])
        if (start_row is None and last_row is not None) or (start_column is None and last_column is not None):
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        end_row = last_row + 1 if last_row is not None else None
        end_column = last_column + 1 if last_column is not None else None
    if title is not None:
        quote_sheet_name(title)
    return title, CellRange(start_row or 0, start_column or 0, end_row, end_column)


def format_a1_range(title: str, region: CellRange) -> str:
    """Generate a fully qualified bounded A1 rectangle."""
    if not isinstance(region, CellRange) or region.end_row is None or region.end_column is None:
        raise SheetServiceError(Code.INVALID_RANGE, field="range")
    start = column_index_to_a1(region.start_column) + row_index_to_a1(region.start_row)
    end = column_index_to_a1(region.end_column - 1) + row_index_to_a1(region.end_row - 1)
    return quote_sheet_name(title) + "!" + (start if start == end else start + ":" + end)
