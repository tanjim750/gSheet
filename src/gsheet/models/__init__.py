"""Generic physical grid and operation contracts."""

from .operations import StructuralChange, WritePrecondition, WriteSafety
from .ranges import CellRange, ResolvedRange
from .results import CellValue, GridDataResult, RangeValues, ReadMode, WriteMode
from .cells import CellInfo
from .spreadsheet import WorksheetInfo

__all__ = ["CellInfo", "CellRange", "CellValue", "GridDataResult", "RangeValues", "ReadMode",
           "ResolvedRange", "StructuralChange", "WorksheetInfo", "WriteMode", "WritePrecondition", "WriteSafety"]
