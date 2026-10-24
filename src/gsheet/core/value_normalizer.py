"""Validate value matrices without coercion, densification, or formula parsing."""

import math
from collections.abc import Sequence

from ..models.results import CellValue
from .exceptions import ServiceErrorCode as Code, SheetServiceError


def value_matrix(values: Sequence[Sequence[CellValue]], *, response: bool = False) -> list[list[CellValue]]:
    code = Code.MALFORMED_RESPONSE if response else Code.INVALID_INPUT
    if not isinstance(values, (list, tuple)) or (not values and not response):
        raise SheetServiceError(code, field="values")
    result = []
    for row in values:
        if not isinstance(row, (list, tuple)):
            raise SheetServiceError(code, field="values")
        if any(value is not None and (type(value) not in (str, int, float, bool)
               or type(value) is float and not math.isfinite(value)) for value in row):
            raise SheetServiceError(code, field="values")
        result.append(list(row))
    if not response and not any(result):
        raise SheetServiceError(code, field="values")
    return result
