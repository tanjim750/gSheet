"""Typed dimension changes for one ordered structural batch."""

from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Literal

from .ranges import CellRange


@dataclass(frozen=True)
class StructuralChange:
    operation: Literal["insert_rows", "delete_rows", "insert_columns", "delete_columns", "add_columns", "move_rows", "move_columns"]
    worksheet: str | int
    start: int = 0
    end: int | None = None
    count: int = 1
    destination: int | None = None
    inherit_from_before: bool = False


@dataclass(frozen=True)
class WritePrecondition:
    """Best-effort expected observations, never an atomic compare-and-swap."""

    worksheet: str | int | None = field(default=None, repr=False)
    region: str | CellRange | None = field(default=None, repr=False)
    expected_sheet_id: int | None = None
    expected_dimensions: tuple[int, int] | None = None
    expected_values: Sequence[Sequence[str | int | float | bool | None]] | None = field(default=None, repr=False)
    read_mode: Literal["FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"] = "UNFORMATTED_VALUE"
    expected_formula: str | None = field(default=None, repr=False)
    expected_empty: bool | None = None


@dataclass(frozen=True)
class WriteSafety:
    preconditions: tuple[WritePrecondition, ...] = field(default=(), repr=False)
    protect_formulas: bool = False
    dry_run: bool = False
