"""Generic physical grid and operation contracts."""

from .operations import StructuralChange
from .ranges import CellRange
from .results import CellValue, RangeValues, ReadMode, WriteMode
from .spreadsheet import WorksheetInfo

__all__ = ["CellRange", "CellValue", "RangeValues", "ReadMode", "StructuralChange", "WorksheetInfo", "WriteMode"]
