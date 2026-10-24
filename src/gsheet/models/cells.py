"""Detailed cell fields remain distinct; absence never invents an empty value."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CellInfo:
    spreadsheet_id: str
    sheet_id: int
    row: int
    column: int
    entry_present: bool
    requested_fields: tuple[str, ...]
    user_entered_value: dict[str, Any] | None = field(default=None, repr=False)
    effective_value: dict[str, Any] | None = field(default=None, repr=False)
    formatted_value: str | None = field(default=None, repr=False)
    user_entered_format: dict[str, Any] | None = field(default=None, repr=False)
    effective_format: dict[str, Any] | None = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def formula(self) -> str | None:
        return (self.user_entered_value or {}).get("formulaValue")

    @property
    def error(self) -> dict[str, Any] | None:
        return (self.effective_value or {}).get("errorValue")

    @property
    def number_format(self) -> dict[str, Any] | None:
        return (self.effective_format or {}).get("numberFormat", (self.user_entered_format or {}).get("numberFormat"))
