"""Generic spreadsheet service over SheetsClient, with physical grid semantics."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
import json
import re
from typing import Any

from ..client import APIResponse, RequestPreview, SheetsClient, SheetsEndpoint
from ..config import OperationalConfig
from ..core.exceptions import ServiceErrorCode as Code, SheetServiceError
from ..core.range_resolver import format_a1_range, index, parse_a1_range, quote_sheet_name
from ..core.value_normalizer import value_matrix
from ..models.operations import StructuralChange, WritePrecondition, WriteSafety
from ..models.ranges import CellRange, ResolvedRange
from ..models.cells import CellInfo
from ..models.results import CellValue, GridDataResult, RangeValues, ReadMode, WriteMode
from ..models.spreadsheet import WorksheetInfo
from ..models.metadata import DeveloperMetadataResult, DimensionInfo, DimensionsResult, WorksheetMetadata

Worksheet = str | int
Range = str | CellRange
_FIELDS = "spreadsheetId,properties,sheets(properties,merges)"
_CELL_FIELDS = ("userEnteredValue", "effectiveValue", "formattedValue", "userEnteredFormat", "effectiveFormat")
_GRID_CELL_FIELDS = _CELL_FIELDS + ("dataValidation",)


class SheetService:
    """One spreadsheet, injected client, no construction-time I/O.

    All numeric indexes are zero-based, end-exclusive. The caller owns the client
    lifecycle and authorization policy. Metadata caching is client-owned and
    disabled by default. Mutation resolution always bypasses it, but a
    metadata read followed by mutation is not atomic against concurrent editors.
    """

    def __init__(self, client: SheetsClient, spreadsheet_id: str, *,
                 limits: OperationalConfig | None = None) -> None:
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
            raise SheetServiceError(Code.INVALID_INPUT, field="spreadsheet_id")
        if limits is not None and not isinstance(limits, OperationalConfig):
            raise SheetServiceError(Code.INVALID_INPUT, field="limits")
        self._client = client
        self._spreadsheet_id = spreadsheet_id
        self._limits = limits if limits is not None else OperationalConfig()

    def __repr__(self) -> str:
        return "SheetService(<spreadsheet>)"

    def get_spreadsheet(self, *, fields: str = _FIELDS, force_refresh: bool = False) -> APIResponse:
        return self._client.get_spreadsheet(self._spreadsheet_id, params={"fields": fields},
                                            force_refresh=force_refresh)

    def list_worksheets(self, *, force_refresh: bool = False) -> list[WorksheetInfo]:
        response = self.get_spreadsheet(force_refresh=force_refresh)
        try:
            if not isinstance(response.data, dict) or response.data.get("spreadsheetId") != self._spreadsheet_id:
                raise ValueError
            sheets = response.data["sheets"]
            if not isinstance(sheets, list):
                raise ValueError
            result = []
            for sheet in sheets:
                props = sheet["properties"]
                grid = props.get("gridProperties", {})
                identifier, title = props["sheetId"], props["title"]
                position, rows, columns = props.get("index", 0), grid.get("rowCount", 0), grid.get("columnCount", 0)
                if type(identifier) is not int or identifier < 0 or not isinstance(title, str) or not title:
                    raise ValueError
                if any(type(v) is not int or v < 0 for v in (position, rows, columns)):
                    raise ValueError
                merges = tuple(CellRange(m.get("startRowIndex", 0), m.get("startColumnIndex", 0),
                                         m["endRowIndex"], m["endColumnIndex"]) for m in sheet.get("merges", []))
                result.append(WorksheetInfo(identifier, title, position, rows, columns, merges, sheet))
            if len({s.sheet_id for s in result}) != len(result) or len({s.title for s in result}) != len(result):
                raise ValueError
            return sorted(result, key=lambda sheet: sheet.index)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        raise SheetServiceError(Code.MALFORMED_RESPONSE, field="worksheets")

    @staticmethod
    def _select(worksheets: Sequence[WorksheetInfo], worksheet: Worksheet) -> WorksheetInfo:
        if type(worksheet) is int:
            index(worksheet, "worksheet")
            matches = [sheet for sheet in worksheets if sheet.sheet_id == worksheet]
        elif isinstance(worksheet, str) and worksheet.strip():
            matches = [sheet for sheet in worksheets if sheet.title == worksheet]
        else:
            raise SheetServiceError(Code.INVALID_INPUT, field="worksheet")
        if not matches:
            raise SheetServiceError(Code.WORKSHEET_NOT_FOUND, field="worksheet")
        return matches[0]

    def get_worksheet(self, worksheet: Worksheet, *, force_refresh: bool = False) -> WorksheetInfo:
        return self._select(self.list_worksheets(force_refresh=force_refresh), worksheet)

    def resolve_range(self, worksheet: Worksheet, region: Range, *, bounded: bool = False) -> ResolvedRange:
        """Keep unknown ends unless bounded=True explicitly requests fresh dimensions."""
        if type(bounded) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="bounded")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        original = region
        if isinstance(region, str):
            _, original = parse_a1_range(region)
        resolved = self._region(sheet, region)  # Also validates known bounds/title.
        return ResolvedRange(self._spreadsheet_id, sheet.sheet_id, sheet.title, resolved if bounded else original)

    def get_merged_ranges(self, worksheet: Worksheet, *, force_refresh: bool = False) -> tuple[ResolvedRange, ...]:
        sheet = self.get_worksheet(worksheet, force_refresh=force_refresh)
        return tuple(ResolvedRange(self._spreadsheet_id, sheet.sheet_id, sheet.title, merged)
                     for merged in sheet.merged_ranges)

    def get_grid_data(self, worksheet: Worksheet, region: Range, *,
                      fields: Sequence[str] = _CELL_FIELDS, pad: bool = False,
                      force_refresh: bool = False) -> GridDataResult:
        """Bounded CellData read; requested_fields and entry_present retain absence."""
        if (type(pad) is not bool or not isinstance(fields, (tuple, list)) or not fields
                or any(not isinstance(f, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*", f)
                       or f.split(".")[0] not in _GRID_CELL_FIELDS for f in fields)
                or len(set(fields)) != len(fields)):
            raise SheetServiceError(Code.INVALID_INPUT, field="fields")
        # Cell data and its coordinate bounds always use fresh metadata.
        if type(force_refresh) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="force_refresh")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        if sheet.metadata.get("properties", {}).get("sheetType", "GRID") != "GRID":
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        bounded = self._region(sheet, region)
        self._read_limit([bounded])
        mask = ("spreadsheetId,properties(locale,timeZone),sheets(properties(sheetId,title),"
                "data(startRow,startColumn,rowData(values(" + ",".join(f.replace(".", "/") for f in fields) + "))))")
        response = self._client.get_spreadsheet(self._spreadsheet_id, params={
            "ranges": [format_a1_range(sheet.title, bounded)], "includeGridData": True, "fields": mask}, force_refresh=True)
        cells = {}
        try:
            payload = response.data
            if payload.get("spreadsheetId") != self._spreadsheet_id:
                raise ValueError
            sheets = payload["sheets"]
            if not isinstance(sheets, list) or len(sheets) != 1 or sheets[0]["properties"]["sheetId"] != sheet.sheet_id:
                raise ValueError
            data = sheets[0].get("data", [])
            if not isinstance(data, list):
                raise ValueError
            for block in data:
                start_row, start_column = block.get("startRow", 0), block.get("startColumn", 0)
                if any(type(v) is not int or v < 0 for v in (start_row, start_column)):
                    raise ValueError
                rows = block.get("rowData", [])
                if not isinstance(rows, list):
                    raise ValueError
                for r, row in enumerate(rows, start_row):
                    values = row.get("values", [])
                    if not isinstance(values, list):
                        raise ValueError
                    for c, value in enumerate(values, start_column):
                        if (not isinstance(value, dict) or (r, c) in cells
                                or not bounded.start_row <= r < bounded.end_row
                                or not bounded.start_column <= c < bounded.end_column):
                            raise ValueError
                        for key in ("userEnteredValue", "effectiveValue", "userEnteredFormat", "effectiveFormat", "dataValidation"):
                            if key in value and not isinstance(value[key], dict):
                                raise ValueError
                        if "formattedValue" in value and not isinstance(value["formattedValue"], str):
                            raise ValueError
                        for key in ("userEnteredValue", "effectiveValue"):
                            extended = value.get(key, {})
                            if "stringValue" in extended and not isinstance(extended["stringValue"], str):
                                raise ValueError
                            if "boolValue" in extended and type(extended["boolValue"]) is not bool:
                                raise ValueError
                            if "numberValue" in extended:
                                value_matrix([[extended["numberValue"]]], response=True)
                                if type(extended["numberValue"]) not in (int, float):
                                    raise ValueError
                            if "formulaValue" in extended and not isinstance(extended["formulaValue"], str):
                                raise ValueError
                            if "errorValue" in extended and not isinstance(extended["errorValue"], dict):
                                raise ValueError
                        for key in ("userEnteredFormat", "effectiveFormat"):
                            if "numberFormat" in value.get(key, {}) and not isinstance(value[key]["numberFormat"], dict):
                                raise ValueError
                        cells[r, c] = CellInfo(self._spreadsheet_id, sheet.sheet_id, r, c, True, tuple(fields),
                            value.get("userEnteredValue"), value.get("effectiveValue"), value.get("formattedValue"),
                            value.get("userEnteredFormat"), value.get("effectiveFormat"), value)
            properties = payload.get("properties", {})
            locale, timezone = properties.get("locale"), properties.get("timeZone")
            if any(v is not None and not isinstance(v, str) for v in (locale, timezone)):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="grid_data") from None
        if pad:
            for r in range(bounded.start_row, bounded.end_row):
                for c in range(bounded.start_column, bounded.end_column):
                    cells.setdefault((r, c), CellInfo(self._spreadsheet_id, sheet.sheet_id, r, c, False, tuple(fields)))
        return GridDataResult(self._spreadsheet_id, sheet, bounded, tuple(cells[key] for key in sorted(cells)),
                              locale, timezone, response)

    @staticmethod
    def _metadata_fields(fields: Sequence[str], *, roots: set[str] | None = None) -> tuple[str, ...]:
        if (not isinstance(fields, (tuple, list)) or not fields
                or any(not isinstance(f, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*", f)
                       or roots is not None and f.split(".")[0] not in roots for f in fields)
                or len(set(fields)) != len(fields)):
            raise SheetServiceError(Code.INVALID_INPUT, field="fields")
        return tuple(fields)

    def _metadata_sheet(self, response: APIResponse, sheet: WorksheetInfo) -> dict[str, Any]:
        try:
            if response.data["spreadsheetId"] != self._spreadsheet_id:
                raise ValueError
            sheets = response.data["sheets"]
            if not isinstance(sheets, list):
                raise ValueError
            matches = [s for s in sheets if type(s["properties"]["sheetId"]) is int
                       and s["properties"]["sheetId"] == sheet.sheet_id]
            if len(matches) != 1 or matches[0]["properties"]["title"] != sheet.title:
                raise ValueError
            return matches[0]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="metadata") from None

    def get_spreadsheet_properties(self, *, fields: Sequence[str] | None = None,
                                   force_refresh: bool = False) -> APIResponse:
        """Native spreadsheet properties, including locale/timeZone when available."""
        selected = "properties" if fields is None else "properties(" + ",".join(
            f.replace(".", "/") for f in self._metadata_fields(fields)) + ")"
        response = self.get_spreadsheet(fields="spreadsheetId," + selected, force_refresh=force_refresh)
        if (not isinstance(response.data, dict) or response.data.get("spreadsheetId") != self._spreadsheet_id
                or not isinstance(response.data.get("properties", {}), dict)):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="properties")
        return response

    def get_worksheet_metadata(self, worksheet: Worksheet, *,
                               fields: Sequence[str] = ("properties", "merges", "protectedRanges", "basicFilter", "filterViews"),
                               force_refresh: bool = False) -> WorksheetMetadata:
        """Target native tab metadata without interpreting filter/header semantics."""
        selected = self._metadata_fields(fields, roots={"properties", "merges", "protectedRanges", "basicFilter", "filterViews", "rowGroups", "columnGroups", "developerMetadata"})
        sheet = self.get_worksheet(worksheet, force_refresh=force_refresh)
        mask = "spreadsheetId,sheets(properties(sheetId,title)," + ",".join(f.replace(".", "/") for f in selected) + ")"
        response = self._client.get_spreadsheet(self._spreadsheet_id, params={
            "ranges": [quote_sheet_name(sheet.title)], "includeGridData": False, "fields": mask},
            force_refresh=force_refresh)
        metadata = self._metadata_sheet(response, sheet)
        for name in ("merges", "protectedRanges", "filterViews", "rowGroups", "columnGroups", "developerMetadata"):
            if name in metadata and (not isinstance(metadata[name], list) or any(not isinstance(v, dict) for v in metadata[name])):
                raise SheetServiceError(Code.MALFORMED_RESPONSE, field="metadata")
        if "basicFilter" in metadata and not isinstance(metadata["basicFilter"], dict):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="metadata")
        return WorksheetMetadata(self._spreadsheet_id, sheet, selected, metadata, response)

    def get_protected_ranges(self, worksheet: Worksheet, *, force_refresh: bool = False) -> WorksheetMetadata:
        """Available native protection metadata; Google may omit editor details."""
        return self.get_worksheet_metadata(worksheet, fields=("protectedRanges",), force_refresh=force_refresh)

    def get_basic_filter(self, worksheet: Worksheet, *, force_refresh: bool = False) -> WorksheetMetadata:
        """Native basicFilter when present; never applies or removes a filter."""
        return self.get_worksheet_metadata(worksheet, fields=("basicFilter",), force_refresh=force_refresh)

    def get_filter_views(self, worksheet: Worksheet, *, force_refresh: bool = False) -> WorksheetMetadata:
        """Native saved filterViews; cell reads remain physical-grid reads."""
        return self.get_worksheet_metadata(worksheet, fields=("filterViews",), force_refresh=force_refresh)

    def get_named_ranges(self, *, force_refresh: bool = False) -> APIResponse:
        """Read native definitions without resolving names as A1 or inferring schemas."""
        response = self.get_spreadsheet(fields="spreadsheetId,namedRanges", force_refresh=force_refresh)
        try:
            definitions = response.data.get("namedRanges", [])
            if (response.data.get("spreadsheetId") != self._spreadsheet_id or not isinstance(definitions, list)
                    or any(not isinstance(item, dict) or not isinstance(item.get("namedRangeId"), str)
                           or not isinstance(item.get("name"), str) or not isinstance(item.get("range"), dict)
                           for item in definitions)):
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="named_ranges") from None
        return response

    def get_developer_metadata(self, worksheet: Worksheet | None = None, *, region: Range | None = None,
                               force_refresh: bool = False) -> DeveloperMetadataResult:
        """Read file/tab/dimension metadata using targeted existing GET operations.

        A region selects metadata on its intersecting rows and columns, not a
        cell-attached identity scheme. No API search or metadata mutation occurs.
        """
        if worksheet is None:
            if region is not None:
                raise SheetServiceError(Code.INVALID_INPUT, field="worksheet")
            response = self.get_spreadsheet(fields="spreadsheetId,developerMetadata", force_refresh=force_refresh)
            if not isinstance(response.data, dict) or response.data.get("spreadsheetId") != self._spreadsheet_id:
                raise SheetServiceError(Code.MALFORMED_RESPONSE, field="developer_metadata")
            entries = response.data.get("developerMetadata", [])
            sheet, bounded = None, None
        elif region is None:
            selected = self.get_worksheet_metadata(worksheet, fields=("developerMetadata",), force_refresh=force_refresh)
            response, sheet, bounded = selected.response, selected.worksheet, None
            entries = selected.metadata.get("developerMetadata", [])
        else:
            dimensions = self.get_dimensions(worksheet, region, fields=("developerMetadata",), force_refresh=force_refresh)
            response, sheet, bounded = dimensions.response, dimensions.worksheet, dimensions.region
            entries = []
            for dimension in dimensions.row_metadata + dimensions.column_metadata:
                found = dimension.metadata.get("developerMetadata", [])
                if not isinstance(found, list):
                    raise SheetServiceError(Code.MALFORMED_RESPONSE, field="developer_metadata")
                entries.extend(found)
        if (not isinstance(entries, list) or any(not isinstance(item, dict)
                or type(item.get("metadataId")) is not int or item["metadataId"] < 0 for item in entries)):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="developer_metadata")
        unique = {}
        for entry in entries:
            identifier = entry["metadataId"]
            if identifier in unique and unique[identifier] != entry:
                raise SheetServiceError(Code.MALFORMED_RESPONSE, field="developer_metadata")
            unique[identifier] = entry
        return DeveloperMetadataResult(self._spreadsheet_id, sheet, bounded, tuple(unique.values()), response)

    def get_effective_cell(self, worksheet: Worksheet, row: int, column: int, *,
                           force_refresh: bool = False) -> CellInfo:
        bounded = CellRange(row, column, index(row, "row") + 1, index(column, "column") + 1)
        return self.get_grid_data(worksheet, bounded, pad=True, force_refresh=force_refresh).cells[0]

    def get_data_validations(self, worksheet: Worksheet, region: Range, *, pad: bool = False,
                             force_refresh: bool = False) -> GridDataResult:
        """Bounded rules read; CellInfo.data_validation exposes native returned rules."""
        return self.get_grid_data(worksheet, region, fields=("dataValidation",), pad=pad, force_refresh=force_refresh)

    def get_dimensions(self, worksheet: Worksheet, region: Range | None = None, *,
                        fields: Sequence[str] = ("hiddenByUser", "hiddenByFilter", "pixelSize"),
                        force_refresh: bool = False) -> DimensionsResult:
        """Counts only by default; an explicit region opts into dimension metadata."""
        selected = self._metadata_fields(fields, roots={"hiddenByUser", "hiddenByFilter", "pixelSize", "developerMetadata", "dataSourceColumnReference"})
        sheet = self.get_worksheet(worksheet, force_refresh=force_refresh)
        if sheet.metadata.get("properties", {}).get("sheetType", "GRID") not in ("GRID", "DATA_SOURCE"):
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        bounded = self._region(sheet, region) if region is not None else None
        if bounded is not None:
            if sheet.metadata.get("properties", {}).get("sheetType", "GRID") != "GRID":
                raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
            if (bounded.end_row - bounded.start_row) + (bounded.end_column - bounded.start_column) > self._limits.max_read_cells:
                raise SheetServiceError(Code.INVALID_RANGE, field="dimension_limits")
        mask = "spreadsheetId,sheets(properties(sheetId,title,gridProperties)"
        if bounded is not None:
            props = ",".join(f.replace(".", "/") for f in selected)
            mask += ",data(startRow,startColumn,rowMetadata(" + props + "),columnMetadata(" + props + "))"
        mask += ")"
        response = self._client.get_spreadsheet(self._spreadsheet_id, params={
            "ranges": [format_a1_range(sheet.title, bounded) if bounded is not None else quote_sheet_name(sheet.title)],
            "includeGridData": bounded is not None, "fields": mask}, force_refresh=force_refresh)
        metadata = self._metadata_sheet(response, sheet)
        try:
            grid = metadata["properties"]["gridProperties"]
            rows, columns = grid["rowCount"], grid["columnCount"]
            if any(type(v) is not int or v <= 0 for v in (rows, columns)):
                raise ValueError
            if bounded is not None:
                bounded.bounded(rows, columns)
            dimensions: dict[str, dict[int, DimensionInfo]] = {"row": {}, "column": {}}
            blocks = metadata.get("data", [])
            if not isinstance(blocks, list):
                raise ValueError
            for block in blocks:
                for axis, key, offset, start, end in (
                        ("row", "rowMetadata", "startRow", bounded.start_row if bounded else 0, bounded.end_row if bounded else 0),
                        ("column", "columnMetadata", "startColumn", bounded.start_column if bounded else 0, bounded.end_column if bounded else 0)):
                    origin = block.get(offset, 0)
                    entries = block.get(key, [])
                    if type(origin) is not int or origin < 0 or not isinstance(entries, list):
                        raise ValueError
                    for position, entry in enumerate(entries, origin):
                        if not start <= position < end or position in dimensions[axis] or not isinstance(entry, dict):
                            raise ValueError
                        for flag in ("hiddenByUser", "hiddenByFilter"):
                            if flag in entry and type(entry[flag]) is not bool:
                                raise ValueError
                        if "pixelSize" in entry and (type(entry["pixelSize"]) is not int or entry["pixelSize"] < 0):
                            raise ValueError
                        dimensions[axis][position] = DimensionInfo(position, selected, entry)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="dimensions") from None
        return DimensionsResult(self._spreadsheet_id, replace(sheet, row_count=rows, column_count=columns),
            rows, columns, bounded, tuple(dimensions["row"][k] for k in sorted(dimensions["row"])),
            tuple(dimensions["column"][k] for k in sorted(dimensions["column"])), response)

    def _chunk_regions(self, worksheet: Worksheet, region: Range, chunk_cells: int | None) -> Iterator[tuple[int, CellRange]]:
        size = min(self._limits.read_chunk_cells, self._limits.max_read_cells) if chunk_cells is None else index(chunk_cells, "chunk_cells", positive=True)
        if size > self._limits.max_read_cells:
            raise SheetServiceError(Code.INVALID_RANGE, field="chunk_cells")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = self._region(sheet, region)
        width = min(bounded.end_column - bounded.start_column, size)
        height = max(1, size // width)
        for r in range(bounded.start_row, bounded.end_row, height):
            for c in range(bounded.start_column, bounded.end_column, width):
                yield sheet.sheet_id, CellRange(r, c, min(r + height, bounded.end_row), min(c + width, bounded.end_column))

    def iter_range_chunks(self, worksheet: Worksheet, region: Range, *, chunk_cells: int | None = None,
                          read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> Iterator[RangeValues]:
        """Lazy bounded physical tiles, never a snapshot or page-token traversal."""
        self._read_mode(read_mode)
        if type(pad) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="pad")
        for sheet_id, chunk in self._chunk_regions(worksheet, region, chunk_cells):
            yield self.read_range(sheet_id, chunk, read_mode=read_mode, pad=pad)

    def iter_grid_data_chunks(self, worksheet: Worksheet, region: Range, *, chunk_cells: int | None = None,
                              fields: Sequence[str] = _CELL_FIELDS, pad: bool = False) -> Iterator[GridDataResult]:
        """Lazy detailed-cell tiles with per-request cell and response byte bounds."""
        for sheet_id, chunk in self._chunk_regions(worksheet, region, chunk_cells):
            yield self.get_grid_data(sheet_id, chunk, fields=fields, pad=pad, force_refresh=True)

    def _write_guard(self, endpoint: SheetsEndpoint, *, sheet: WorksheetInfo | None,
                     regions: Sequence[CellRange], body: dict[str, Any], range_name: str | None = None,
                     params: dict[str, Any] | None = None, matrices: Sequence[list[list[CellValue]]] | None = None,
                     clear: bool = False, safety: WriteSafety | None = None) -> RequestPreview | None:
        if safety is None:
            return None
        if (not isinstance(safety, WriteSafety) or type(safety.dry_run) is not bool
                or type(safety.protect_formulas) is not bool or not isinstance(safety.preconditions, tuple)
                or not all(isinstance(p, WritePrecondition) for p in safety.preconditions)):
            raise SheetServiceError(Code.INVALID_INPUT, field="safety")
        preview = self._client.validate_request(endpoint, self._spreadsheet_id,
            range_name=range_name, params=params, body=body)
        checks = list(preview.checks) + (["target_resolution"] if sheet is not None else [])
        if safety.protect_formulas and (sheet is None or matrices is None and not clear):
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="protect_formulas")
        if safety.protect_formulas:
            self._read_limit(regions)
        for condition in safety.preconditions:
            target = self.get_worksheet(condition.worksheet, force_refresh=True) if condition.worksheet is not None else sheet
            if target is None:
                raise SheetServiceError(Code.INVALID_INPUT, field="precondition_worksheet")
            self._read_mode(condition.read_mode)
            if condition.expected_sheet_id is not None:
                index(condition.expected_sheet_id, "expected_sheet_id")
                if condition.expected_sheet_id != target.sheet_id:
                    raise SheetServiceError(Code.PRECONDITION_FAILED, field="worksheet_identity")
            if condition.expected_dimensions is not None:
                dimensions = condition.expected_dimensions
                if not isinstance(dimensions, tuple) or len(dimensions) != 2:
                    raise SheetServiceError(Code.INVALID_INPUT, field="expected_dimensions")
                for size in dimensions:
                    index(size, "expected_dimensions", positive=True)
                if dimensions != (target.row_count, target.column_count):
                    raise SheetServiceError(Code.PRECONDITION_FAILED, field="dimensions")
            wants_values = condition.expected_values is not None
            wants_grid = condition.expected_formula is not None or condition.expected_empty is not None
            selected = condition.region
            if selected is None and len(regions) == 1 and target.sheet_id == (sheet.sheet_id if sheet else None):
                selected = regions[0]
            if (wants_values or wants_grid) and selected is None:
                raise SheetServiceError(Code.INVALID_INPUT, field="precondition_region")
            if not (wants_values or wants_grid):
                if condition.expected_sheet_id is None and condition.expected_dimensions is None:
                    raise SheetServiceError(Code.INVALID_INPUT, field="precondition")
                continue
            bounded = self._region(target, selected)
            if wants_values:
                expected = value_matrix(condition.expected_values)
                if len(expected) != bounded.end_row - bounded.start_row or any(
                        len(row) != bounded.end_column - bounded.start_column for row in expected):
                    raise SheetServiceError(Code.INVALID_RANGE, field="expected_values")
                observed = self.read_range(target.sheet_id, bounded, read_mode=condition.read_mode, pad=True).values
                if any(a != b or isinstance(a, bool) != isinstance(b, bool)
                       for first, second in zip(expected, observed) for a, b in zip(first, second)):
                    raise SheetServiceError(Code.PRECONDITION_FAILED, field="values")
            if wants_grid:
                if bounded.cell_count != 1 or (condition.expected_formula is not None and (
                        not isinstance(condition.expected_formula, str) or not condition.expected_formula.startswith("="))) \
                        or condition.expected_empty is not None and type(condition.expected_empty) is not bool:
                    raise SheetServiceError(Code.INVALID_INPUT, field="cell_precondition")
                observed = self.get_grid_data(target.sheet_id, bounded,
                    fields=("userEnteredValue", "effectiveValue"), pad=True).cells[0]
                if not observed.entry_present or (condition.expected_formula is not None and observed.formula != condition.expected_formula):
                    raise SheetServiceError(Code.PRECONDITION_FAILED, field="cell_state")
                empty = observed.user_entered_value is None and observed.effective_value is None
                if condition.expected_empty is not None and condition.expected_empty != empty:
                    raise SheetServiceError(Code.PRECONDITION_FAILED, field="empty_state")
        if safety.preconditions:
            checks.append("preconditions")
        if safety.protect_formulas:
            for offset, region in enumerate(regions):
                reported = self.get_grid_data(sheet.sheet_id, region, fields=("userEnteredValue",)).cells
                for cell in reported:
                    r, c = cell.row - region.start_row, cell.column - region.start_column
                    matrix = matrices[offset] if matrices is not None else []
                    touched = clear or r < len(matrix) and c < len(matrix[r]) and matrix[r][c] is not None
                    if touched and cell.formula is not None:
                        raise SheetServiceError(Code.FORMULA_PROTECTED, field="formula")
            checks.append("formula_protection")
        return replace(preview, checks=tuple(checks)) if safety.dry_run else None

    def batch_update(self, requests: Sequence[dict[str, Any]], *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Restricted raw structural foundation; client validates grants and types.

        Preconditions need explicit worksheet selectors. Formula protection is
        unsupported for raw structural mutations whose effects cannot be resolved.
        """
        if not isinstance(requests, (tuple, list)) or not requests:
            raise SheetServiceError(Code.INVALID_INPUT, field="requests")
        if any(not isinstance(request, dict) or len(request) != 1
               or not isinstance(next(iter(request.values())), dict) for request in requests):
            raise SheetServiceError(Code.INVALID_INPUT, field="requests")

        def validate_positions(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("sheetId", "sourceSheetId", "newSheetId", "startRowIndex", "startColumnIndex", "startIndex", "destinationIndex"):
                        index(item, key)
                    if key in ("endRowIndex", "endColumnIndex", "endIndex", "rowCount", "columnCount", "length"):
                        index(item, key, positive=True)
                    validate_positions(item)
                for start, end in (("startRowIndex", "endRowIndex"), ("startColumnIndex", "endColumnIndex"), ("startIndex", "endIndex")):
                    if end in value and value.get(start, 0) >= value[end]:
                        raise SheetServiceError(Code.INVALID_RANGE, field="requests")
            elif isinstance(value, (list, tuple)):
                for item in value:
                    validate_positions(item)
        try:
            validate_positions(requests)
        except RecursionError:
            raise SheetServiceError(Code.INVALID_INPUT, field="requests") from None
        body = {"requests": list(requests)}
        preview = self._write_guard(SheetsEndpoint.SPREADSHEET_BATCH_UPDATE, sheet=None, regions=[], body=body, safety=safety)
        if preview is not None:
            known = {sheet.sheet_id for sheet in self.list_worksheets(force_refresh=True)}
            declared = set()
            for request in preview.body["requests"]:
                properties = request.get("addSheet", {}).get("properties", {})
                if not isinstance(properties, dict):
                    raise SheetServiceError(Code.INVALID_INPUT, field="properties")
                new_id = properties.get("sheetId")
                if new_id is None:
                    new_id = request.get("duplicateSheet", {}).get("newSheetId")
                if new_id is not None:
                    index(new_id, "new_sheet_id")
                    if new_id in known or new_id in declared:
                        raise SheetServiceError(Code.INVALID_INPUT, field="new_sheet_id")
                    declared.add(new_id)

            def verify_ids(value: Any) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ("sheetId", "sourceSheetId"):
                            index(item, "sheet_id")
                            if item not in known | declared:
                                raise SheetServiceError(Code.WORKSHEET_NOT_FOUND, field="sheet_id")
                        verify_ids(item)
                elif isinstance(value, list):
                    for item in value:
                        verify_ids(item)
            verify_ids(preview.body)
            return replace(preview, checks=preview.checks + ("worksheet_ids",))
        return self._client.batch_update(self._spreadsheet_id, body)

    def worksheet_exists(self, worksheet: Worksheet) -> bool:
        try:
            self.get_worksheet(worksheet)
            return True
        except SheetServiceError as exc:
            if exc.code != Code.WORKSHEET_NOT_FOUND:
                raise
        return False

    @staticmethod
    def _new_title(title: str, worksheets: Sequence[WorksheetInfo], *, except_id: int | None = None) -> None:
        quote_sheet_name(title)
        if any(sheet.title == title and sheet.sheet_id != except_id for sheet in worksheets):
            raise SheetServiceError(Code.INVALID_INPUT, field="title")

    def _structural(self, requests: list[dict[str, Any]], *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_update(requests, safety=safety)

    def create_worksheet(self, title: str, *, rows: int | None = None, columns: int | None = None) -> APIResponse:
        self._new_title(title, self.list_worksheets(force_refresh=True))
        props: dict[str, Any] = {"title": title}
        grid = {}
        for key, value in (("rowCount", rows), ("columnCount", columns)):
            if value is not None:
                grid[key] = index(value, key, positive=True)
        if grid:
            props["gridProperties"] = grid
        return self._structural([{"addSheet": {"properties": props}}])

    def rename_worksheet(self, worksheet: Worksheet, title: str) -> APIResponse:
        sheets = self.list_worksheets(force_refresh=True)
        sheet = self._select(sheets, worksheet)
        self._new_title(title, sheets, except_id=sheet.sheet_id)
        return self._structural([{"updateSheetProperties": {
            "properties": {"sheetId": sheet.sheet_id, "title": title}, "fields": "title"}}])

    def delete_worksheet(self, worksheet: Worksheet) -> APIResponse:
        return self._structural([{"deleteSheet": {"sheetId": self.get_worksheet(worksheet, force_refresh=True).sheet_id}}])

    def duplicate_worksheet(self, worksheet: Worksheet, *, title: str | None = None,
                            safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        sheets = self.list_worksheets(force_refresh=True)
        request: dict[str, Any] = {"sourceSheetId": self._select(sheets, worksheet).sheet_id}
        if title is not None:
            self._new_title(title, sheets)
            request["newSheetName"] = title
        return self._structural([{"duplicateSheet": request}], safety=safety)

    def copy_sheet(self, worksheet: Worksheet, *, title: str | None = None,
                   safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Duplicate within this spreadsheet; reuse the existing duplicateSheet path."""
        return self.duplicate_worksheet(worksheet, title=title, safety=safety)

    @staticmethod
    def _region(sheet: WorksheetInfo, region: Range) -> CellRange:
        if sheet.row_count <= 0 or sheet.column_count <= 0:
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        if isinstance(region, str):
            title, region = parse_a1_range(region)
            if title is not None and title != sheet.title:
                raise SheetServiceError(Code.INVALID_RANGE, field="worksheet")
        if not isinstance(region, CellRange):
            raise SheetServiceError(Code.INVALID_RANGE, field="range")
        return region.bounded(sheet.row_count, sheet.column_count)

    @staticmethod
    def _read_mode(mode: ReadMode) -> None:
        if mode not in ("FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"):
            raise SheetServiceError(Code.INVALID_INPUT, field="read_mode")

    @staticmethod
    def _write_mode(mode: WriteMode) -> None:
        if mode not in ("RAW", "USER_ENTERED"):
            raise SheetServiceError(Code.INVALID_INPUT, field="write_mode")

    def _read_limit(self, regions: Sequence[CellRange]) -> None:
        if len(regions) > self._limits.max_read_batch_ranges or sum(r.cell_count for r in regions) > self._limits.max_read_cells:
            raise SheetServiceError(Code.INVALID_RANGE, field="read_limits")

    def _decode(self, sheet: WorksheetInfo, region: CellRange, payload: dict[str, Any],
                response: APIResponse, mode: ReadMode, pad: bool) -> RangeValues:
        if not isinstance(payload, dict) or payload.get("majorDimension", "ROWS") != "ROWS":
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="values")
        values = value_matrix(payload.get("values", []), response=True)
        height, width = region.end_row - region.start_row, region.end_column - region.start_column
        if len(values) > height or any(len(row) > width for row in values):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="values")
        if type(pad) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="pad")
        if pad:
            values = [row + [None] * (width - len(row)) for row in values]
            values += [[None] * width for _ in range(height - len(values))]
        return RangeValues(self._spreadsheet_id, sheet, region, values, mode, response)

    def read_range(self, worksheet: Worksheet, region: Range, *, read_mode: ReadMode = "FORMATTED_VALUE",
                   pad: bool = False) -> RangeValues:
        self._read_mode(read_mode)
        if type(pad) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="pad")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = self._region(sheet, region)
        self._read_limit([bounded])
        response = self._client.get_values(self._spreadsheet_id, format_a1_range(sheet.title, bounded),
                                          params={"majorDimension": "ROWS", "valueRenderOption": read_mode})
        return self._decode(sheet, bounded, response.data, response, read_mode, pad)

    def read_ranges(self, worksheet: Worksheet, regions: Sequence[Range], *,
                    read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> list[RangeValues]:
        self._read_mode(read_mode)
        if type(pad) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="pad")
        if not isinstance(regions, (list, tuple)) or not regions:
            raise SheetServiceError(Code.INVALID_INPUT, field="regions")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = [self._region(sheet, region) for region in regions]
        self._read_limit(bounded)
        response = self._client.batch_get_values(self._spreadsheet_id,
            [format_a1_range(sheet.title, region) for region in bounded],
            params={"majorDimension": "ROWS", "valueRenderOption": read_mode})
        payloads = response.data.get("valueRanges")
        if not isinstance(payloads, list) or len(payloads) != len(bounded):
            raise SheetServiceError(Code.MALFORMED_RESPONSE, field="valueRanges")
        return [self._decode(sheet, region, payload, response, read_mode, pad)
                for region, payload in zip(bounded, payloads)]

    @staticmethod
    def _write_values(sheet: WorksheetInfo, region: CellRange, values: Sequence[Sequence[CellValue]]) -> list[list[CellValue]]:
        matrix = value_matrix(values)
        if len(matrix) > region.end_row - region.start_row or any(
                len(row) > region.end_column - region.start_column for row in matrix):
            raise SheetServiceError(Code.INVALID_RANGE, field="values")
        for row_index, row in enumerate(matrix, region.start_row):
            for column, value in enumerate(row, region.start_column):
                if value is not None and any(
                    merged.start_row <= row_index < merged.end_row and merged.start_column <= column < merged.end_column
                    and (row_index, column) != (merged.start_row, merged.start_column) for merged in sheet.merged_ranges):
                    raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="merged_cells")
        return matrix

    def update_range(self, worksheet: Worksheet, region: Range, values: Sequence[Sequence[CellValue]], *,
                     write_mode: WriteMode = "RAW", safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        self._write_mode(write_mode)
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = self._region(sheet, region)
        matrix = self._write_values(sheet, bounded, values)
        preview = self._write_guard(SheetsEndpoint.VALUES_UPDATE, sheet=sheet, regions=[bounded],
            range_name=format_a1_range(sheet.title, bounded), body={"values": matrix},
            params={"valueInputOption": write_mode}, matrices=[matrix], safety=safety)
        if preview is not None:
            return preview
        return self._client.update_values(self._spreadsheet_id, format_a1_range(sheet.title, bounded),
                                         {"values": matrix}, params={"valueInputOption": write_mode})

    def update_ranges(self, worksheet: Worksheet, updates: Sequence[tuple[Range, Sequence[Sequence[CellValue]]]], *,
                      write_mode: WriteMode = "RAW", safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        self._write_mode(write_mode)
        if not isinstance(updates, (list, tuple)) or not updates or len(updates) > self._limits.max_write_batch_ranges:
            raise SheetServiceError(Code.INVALID_INPUT, field="updates")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        data = []
        regions = []
        for update in updates:
            if not isinstance(update, (list, tuple)) or len(update) != 2:
                raise SheetServiceError(Code.INVALID_INPUT, field="updates")
            bounded = self._region(sheet, update[0])
            if any(bounded.start_row < other.end_row and other.start_row < bounded.end_row
                   and bounded.start_column < other.end_column and other.start_column < bounded.end_column
                   for other in regions):
                raise SheetServiceError(Code.INVALID_RANGE, field="overlapping_updates")
            regions.append(bounded)
            data.append({"range": format_a1_range(sheet.title, bounded),
                         "values": self._write_values(sheet, bounded, update[1])})
        body = {"valueInputOption": write_mode, "data": data}
        preview = self._write_guard(SheetsEndpoint.VALUES_BATCH_UPDATE, sheet=sheet, regions=regions,
            body=body, matrices=[item["values"] for item in data], safety=safety)
        return preview if preview is not None else self._client.batch_update_values(self._spreadsheet_id, body)

    def clear_range(self, worksheet: Worksheet, region: Range, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = self._region(sheet, region)
        target = format_a1_range(sheet.title, bounded)
        preview = self._write_guard(SheetsEndpoint.VALUES_CLEAR, sheet=sheet, regions=[bounded],
                                    range_name=target, body={}, clear=True, safety=safety)
        return preview if preview is not None else self._client.clear_values(self._spreadsheet_id, target)

    def read_cell(self, worksheet: Worksheet, row: int, column: int, *, read_mode: ReadMode = "FORMATTED_VALUE") -> CellValue:
        return self.read_range(worksheet, CellRange(row, column, index(row, "row") + 1,
                               index(column, "column") + 1), read_mode=read_mode, pad=True).values[0][0]

    def update_cell(self, worksheet: Worksheet, row: int, column: int, value: CellValue, *,
                    write_mode: WriteMode = "RAW", safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.update_range(worksheet, CellRange(row, column, index(row, "row") + 1,
                                 index(column, "column") + 1), [[value]], write_mode=write_mode, safety=safety)

    def clear_cell(self, worksheet: Worksheet, row: int, column: int, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.clear_range(worksheet, CellRange(row, column, index(row, "row") + 1, index(column, "column") + 1), safety=safety)

    def update_cells(self, worksheet: Worksheet, updates: Sequence[tuple[int, int, CellValue]], *,
                     write_mode: WriteMode = "RAW", safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        if not isinstance(updates, (list, tuple)) or any(not isinstance(item, (list, tuple)) or len(item) != 3 for item in updates):
            raise SheetServiceError(Code.INVALID_INPUT, field="updates")
        return self.update_ranges(worksheet, [(CellRange(r, c, index(r, "row") + 1, index(c, "column") + 1), [[value]])
                                              for r, c, value in updates], write_mode=write_mode, safety=safety)

    def read_rows(self, worksheet: Worksheet, start_row: int, end_row: int | None = None, *,
                  start_column: int = 0, end_column: int | None = None,
                  read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> RangeValues:
        return self.read_range(worksheet, CellRange(start_row, start_column, end_row, end_column), read_mode=read_mode, pad=pad)

    def read_row(self, worksheet: Worksheet, row: int, *, start_column: int = 0,
                 end_column: int | None = None, read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> RangeValues:
        return self.read_rows(worksheet, row, index(row, "row") + 1, start_column=start_column,
                              end_column=end_column, read_mode=read_mode, pad=pad)

    def get_cell(self, worksheet: Worksheet, row: int, column: int, *, read_mode: ReadMode = "FORMATTED_VALUE") -> CellValue:
        """Specification convenience name for the existing scalar read."""
        return self.read_cell(worksheet, row, column, read_mode=read_mode)

    def get_row(self, worksheet: Worksheet, row: int, *, start_column: int = 0,
                end_column: int | None = None, read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> RangeValues:
        return self.read_row(worksheet, row, start_column=start_column, end_column=end_column, read_mode=read_mode, pad=pad)

    def read_column(self, worksheet: Worksheet, column: int, *, start_row: int = 0,
                    end_row: int | None = None, read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> RangeValues:
        return self.read_range(worksheet, CellRange(start_row, column, end_row, index(column, "column") + 1),
                               read_mode=read_mode, pad=pad)

    def get_column(self, worksheet: Worksheet, column: int, *, start_row: int = 0,
                   end_row: int | None = None, read_mode: ReadMode = "FORMATTED_VALUE", pad: bool = False) -> RangeValues:
        return self.read_column(worksheet, column, start_row=start_row, end_row=end_row, read_mode=read_mode, pad=pad)

    def find_cells(self, worksheet: Worksheet, region: Range, value: CellValue, *,
                   read_mode: ReadMode = "FORMATTED_VALUE") -> tuple[ResolvedRange, ...]:
        """Exact row-major matches within a caller-selected, read-limited region."""
        value_matrix([[value]])
        observed = self.read_range(worksheet, region, read_mode=read_mode, pad=True)
        return tuple(ResolvedRange(self._spreadsheet_id, observed.worksheet.sheet_id, observed.worksheet.title,
            CellRange(observed.region.start_row + r, observed.region.start_column + c,
                      observed.region.start_row + r + 1, observed.region.start_column + c + 1))
            for r, row in enumerate(observed.values) for c, actual in enumerate(row)
            if actual == value and isinstance(actual, bool) == isinstance(value, bool))

    def update_rows(self, worksheet: Worksheet, start_row: int, values: Sequence[Sequence[CellValue]], *,
                    start_column: int = 0, write_mode: WriteMode = "RAW",
                    safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        matrix = value_matrix(values)
        return self.update_range(worksheet, CellRange(start_row, start_column,
            index(start_row, "row") + len(matrix), index(start_column, "column") + max(map(len, matrix))), matrix, write_mode=write_mode, safety=safety)

    def update_row(self, worksheet: Worksheet, row: int, values: Sequence[CellValue], *,
                   start_column: int = 0, write_mode: WriteMode = "RAW",
                   safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.update_rows(worksheet, row, [values], start_column=start_column, write_mode=write_mode, safety=safety)

    def append_rows(self, worksheet: Worksheet, values: Sequence[Sequence[CellValue]], *,
                    table_range: Range, write_mode: WriteMode = "RAW",
                    safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        self._write_mode(write_mode)
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        bounded = self._region(sheet, table_range)
        matrix = value_matrix(values)
        if any(len(row) > bounded.end_column - bounded.start_column for row in matrix):
            raise SheetServiceError(Code.INVALID_RANGE, field="values")
        preview = self._write_guard(SheetsEndpoint.VALUES_APPEND, sheet=sheet, regions=[bounded],
            range_name=format_a1_range(sheet.title, bounded), body={"values": matrix},
            params={"valueInputOption": write_mode}, safety=safety)
        if preview is not None:
            return preview
        return self._client.append_values(self._spreadsheet_id, format_a1_range(sheet.title, bounded),
                                         {"values": matrix}, params={"valueInputOption": write_mode})

    def clear_row(self, worksheet: Worksheet, row: int, *, start_column: int = 0,
                  end_column: int | None = None, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.clear_range(worksheet, CellRange(row, start_column, index(row, "row") + 1, end_column), safety=safety)

    def find_rows(self, worksheet: Worksheet, column: int, value: CellValue, *, start_row: int,
                  end_row: int, read_mode: ReadMode = "UNFORMATTED_VALUE") -> list[int]:
        value_matrix([[value]])
        result = self.read_rows(worksheet, start_row, end_row, start_column=column,
                               end_column=index(column, "column") + 1, read_mode=read_mode, pad=True)
        return [start_row + offset for offset, row in enumerate(result.values)
                if isinstance(row[0], bool) == isinstance(value, bool) and row[0] == value]

    def read_headers(self, worksheet: Worksheet, *, header_row: int, depth: int = 1,
                     start_column: int = 0, end_column: int | None = None,
                     read_mode: ReadMode = "FORMATTED_VALUE") -> RangeValues:
        return self.read_rows(worksheet, header_row, index(header_row, "header_row") + index(depth, "depth", positive=True),
                              start_column=start_column, end_column=end_column, read_mode=read_mode, pad=True)

    def set_headers(self, worksheet: Worksheet, headers: Sequence[CellValue], *, header_row: int,
                    start_column: int = 0, write_mode: WriteMode = "RAW",
                    safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.update_row(worksheet, header_row, headers, start_column=start_column, write_mode=write_mode, safety=safety)

    def find_columns(self, worksheet: Worksheet, header: str, *, header_row: int,
                     start_column: int = 0, end_column: int | None = None) -> list[int]:
        if not isinstance(header, str):
            raise SheetServiceError(Code.INVALID_INPUT, field="header")
        result = self.read_headers(worksheet, header_row=header_row, start_column=start_column, end_column=end_column)
        return [start_column + offset for offset, value in enumerate(result.values[0]) if value == header]

    def find_column(self, worksheet: Worksheet, header: str, *, header_row: int,
                    occurrence: int | None = None, start_column: int = 0, end_column: int | None = None) -> int:
        matches = self.find_columns(worksheet, header, header_row=header_row,
                                    start_column=start_column, end_column=end_column)
        if occurrence is not None:
            index(occurrence, "occurrence")
        if not matches or occurrence is not None and occurrence >= len(matches):
            raise SheetServiceError(Code.HEADER_NOT_FOUND, field="header")
        if len(matches) > 1 and occurrence is None:
            raise SheetServiceError(Code.AMBIGUOUS_HEADER, field="header")
        return matches[occurrence or 0]

    def rename_header(self, worksheet: Worksheet, header: str, new_header: CellValue, *,
                      header_row: int, occurrence: int | None = None,
                      write_mode: WriteMode = "RAW", safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        column = self.find_column(worksheet, header, header_row=header_row, occurrence=occurrence)
        return self.update_cell(worksheet, header_row, column, new_header, write_mode=write_mode, safety=safety)

    def batch_structure(self, changes: Sequence[StructuralChange], *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        if not isinstance(changes, (list, tuple)) or not changes:
            raise SheetServiceError(Code.INVALID_INPUT, field="changes")
        sheets = self.list_worksheets(force_refresh=True)
        requests = []
        for change in changes:
            if not isinstance(change, StructuralChange):
                raise SheetServiceError(Code.INVALID_INPUT, field="changes")
            sheet = self._select(sheets, change.worksheet)
            if sheet.row_count <= 0 or sheet.column_count <= 0:
                raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
            operation = change.operation
            if operation not in ("insert_rows", "delete_rows", "insert_columns", "delete_columns", "add_columns", "move_rows", "move_columns"):
                raise SheetServiceError(Code.INVALID_INPUT, field="operation")
            dimension = "ROWS" if operation.endswith("rows") else "COLUMNS"
            size = sheet.row_count if dimension == "ROWS" else sheet.column_count
            start = index(change.start, "start")
            count = index(change.count, "count", positive=True)
            if operation == "add_columns":
                requests.append({"appendDimension": {"sheetId": sheet.sheet_id, "dimension": dimension, "length": count}})
                size += count
            elif operation.startswith("insert"):
                if type(change.inherit_from_before) is not bool or start >= size or change.inherit_from_before and start == 0:
                    raise SheetServiceError(Code.INVALID_RANGE, field="start")
                requests.append({"insertDimension": {"range": {"sheetId": sheet.sheet_id, "dimension": dimension,
                    "startIndex": start, "endIndex": start + count}, "inheritFromBefore": change.inherit_from_before}})
                size += count
            else:
                end = change.end
                if type(end) is not int or not start < end <= size:
                    raise SheetServiceError(Code.INVALID_RANGE, field="end")
                span = {"sheetId": sheet.sheet_id, "dimension": dimension, "startIndex": start, "endIndex": end}
                if operation.startswith("delete"):
                    if end - start == size:
                        raise SheetServiceError(Code.INVALID_RANGE, field="range")
                    requests.append({"deleteDimension": {"range": span}})
                    size -= end - start
                else:
                    destination = index(change.destination, "destination")
                    if destination > size or start <= destination <= end:
                        raise SheetServiceError(Code.INVALID_RANGE, field="destination")
                    requests.append({"moveDimension": {"source": span, "destinationIndex": destination}})
            updated = replace(sheet, **({"row_count": size} if dimension == "ROWS" else {"column_count": size}))
            sheets = [updated if value.sheet_id == sheet.sheet_id else value for value in sheets]
        return self._structural(requests, safety=safety)

    def insert_rows(self, worksheet: Worksheet, start: int, count: int = 1, *, inherit_from_before: bool = False,
                    safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("insert_rows", worksheet, start=start, count=count, inherit_from_before=inherit_from_before)], safety=safety)

    def delete_rows(self, worksheet: Worksheet, start: int, end: int, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("delete_rows", worksheet, start=start, end=end)], safety=safety)

    def add_columns(self, worksheet: Worksheet, count: int = 1) -> APIResponse:
        return self.batch_structure([StructuralChange("add_columns", worksheet, count=count)])

    def insert_columns(self, worksheet: Worksheet, start: int, count: int = 1, *, inherit_from_before: bool = False,
                       safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("insert_columns", worksheet, start=start, count=count, inherit_from_before=inherit_from_before)], safety=safety)

    def remove_columns(self, worksheet: Worksheet, start: int, end: int, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("delete_columns", worksheet, start=start, end=end)], safety=safety)

    def delete_columns(self, worksheet: Worksheet, start: int, end: int, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Specification name; remove_columns remains backward compatible."""
        return self.remove_columns(worksheet, start, end, safety=safety)

    @staticmethod
    def _grid_range(sheet: WorksheetInfo, region: CellRange) -> dict[str, int]:
        return {"sheetId": sheet.sheet_id, "startRowIndex": region.start_row, "endRowIndex": region.end_row,
                "startColumnIndex": region.start_column, "endColumnIndex": region.end_column}

    def _merge_region(self, worksheet: Worksheet, region: Range) -> tuple[WorksheetInfo, CellRange]:
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        if sheet.metadata.get("properties", {}).get("sheetType", "GRID") != "GRID":
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        bounded = self._region(sheet, region)
        for merged in sheet.merged_ranges:
            intersects = (merged.start_row < bounded.end_row and bounded.start_row < merged.end_row
                          and merged.start_column < bounded.end_column and bounded.start_column < merged.end_column)
            contained = (bounded.start_row <= merged.start_row and merged.end_row <= bounded.end_row
                         and bounded.start_column <= merged.start_column and merged.end_column <= bounded.end_column)
            if intersects and not contained:
                raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="partial_merge")
        return sheet, bounded

    def merge_cells(self, worksheet: Worksheet, region: Range, *, merge_type: str = "MERGE_ALL",
                    safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Merge explicitly; covered-cell values may be lost, so elevated grants apply."""
        if merge_type not in ("MERGE_ALL", "MERGE_ROWS", "MERGE_COLUMNS"):
            raise SheetServiceError(Code.INVALID_INPUT, field="merge_type")
        sheet, bounded = self._merge_region(worksheet, region)
        if (bounded.cell_count < 2 or merge_type == "MERGE_ROWS" and bounded.end_column - bounded.start_column < 2
                or merge_type == "MERGE_COLUMNS" and bounded.end_row - bounded.start_row < 2):
            raise SheetServiceError(Code.INVALID_RANGE, field="merge_range")
        return self.batch_update([{"mergeCells": {"range": self._grid_range(sheet, bounded), "mergeType": merge_type}}], safety=safety)

    def unmerge_cells(self, worksheet: Worksheet, region: Range, *,
                      safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Unmerge wholly contained merges without extending the requested rectangle."""
        sheet, bounded = self._merge_region(worksheet, region)
        return self.batch_update([{"unmergeCells": {"range": self._grid_range(sheet, bounded)}}], safety=safety)

    def resize_sheet(self, worksheet: Worksheet, *, rows: int | None = None, columns: int | None = None,
                     safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Change only requested grid counts; shrinking can permanently remove data."""
        if rows is None and columns is None:
            raise SheetServiceError(Code.INVALID_INPUT, field="dimensions")
        requested = {}
        for key, value in (("rowCount", rows), ("columnCount", columns)):
            if value is not None:
                requested[key] = index(value, key, positive=True)
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        if sheet.metadata.get("properties", {}).get("sheetType", "GRID") != "GRID":
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        target_rows = requested.get("rowCount", sheet.row_count)
        target_columns = requested.get("columnCount", sheet.column_count)
        grid = sheet.metadata.get("properties", {}).get("gridProperties", {})
        for key, size in (("frozenRowCount", target_rows), ("frozenColumnCount", target_columns)):
            frozen = grid.get(key, 0)
            if type(frozen) is not int or frozen < 0:
                raise SheetServiceError(Code.MALFORMED_RESPONSE, field="gridProperties")
            if frozen > size:
                raise SheetServiceError(Code.INVALID_RANGE, field="frozen_dimensions")
        for merged in sheet.merged_ranges:
            if (merged.start_row < target_rows and merged.start_column < target_columns
                    and (merged.end_row > target_rows or merged.end_column > target_columns)):
                raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="partial_merge")
        return self.batch_update([{"updateSheetProperties": {
            "properties": {"sheetId": sheet.sheet_id, "gridProperties": requested},
            "fields": ",".join("gridProperties." + key for key in requested)}}], safety=safety)

    def move_rows(self, worksheet: Worksheet, start: int, end: int, destination: int, *,
                  safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("move_rows", worksheet, start=start, end=end, destination=destination)], safety=safety)

    def move_columns(self, worksheet: Worksheet, start: int, end: int, destination: int, *,
                     safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self.batch_structure([StructuralChange("move_columns", worksheet, start=start, end=end, destination=destination)], safety=safety)

    def _grid_worksheet(self, worksheet: Worksheet) -> WorksheetInfo:
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        if sheet.metadata.get("properties", {}).get("sheetType", "GRID") != "GRID":
            raise SheetServiceError(Code.UNSUPPORTED_LAYOUT, field="worksheet")
        return sheet

    @staticmethod
    def _native_mapping(value: dict[str, Any], field: str) -> dict[str, Any]:
        """Snapshot a caller's native JSON object without coercing keys or secrets."""
        def check(item: Any) -> None:
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError
                for child in item.values():
                    check(child)
            elif isinstance(item, (tuple, list)):
                for child in item:
                    check(child)
            elif item is not None and type(item) not in (str, int, float, bool):
                raise ValueError
        try:
            if not isinstance(value, dict):
                raise ValueError
            check(value)
            return json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise SheetServiceError(Code.INVALID_INPUT, field=field) from None

    def format_range(self, worksheet: Worksheet, region: Range, formatting: dict[str, Any], *,
                     fields: Sequence[str] | None = None, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Apply only selected relative CellFormat fields; no wildcard replacement."""
        formatting = self._native_mapping(formatting, "formatting")
        if fields is None:
            paths = []
            def leaves(value: Any, prefix: str = "") -> None:
                if isinstance(value, dict):
                    if not value:
                        raise ValueError  # Clearing a whole object needs an explicit mask.
                    for key, child in value.items():
                        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key):
                            raise ValueError
                        leaves(child, prefix + "." + key if prefix else key)
                else:
                    paths.append(prefix)
            try:
                leaves(formatting)
            except (ValueError, RecursionError):
                raise SheetServiceError(Code.INVALID_INPUT, field="format_fields") from None
            fields = paths
        selected = self._metadata_fields(fields, roots={"numberFormat", "backgroundColor", "backgroundColorStyle",
            "borders", "padding", "horizontalAlignment", "verticalAlignment", "wrapStrategy", "textDirection",
            "textFormat", "hyperlinkDisplayType", "textRotation"})
        sheet = self._grid_worksheet(worksheet)
        bounded = self._region(sheet, region)
        return self.batch_update([{"repeatCell": {"range": self._grid_range(sheet, bounded),
            "cell": {"userEnteredFormat": formatting},
            "fields": ",".join("userEnteredFormat." + name for name in selected)}}], safety=safety)

    def set_data_validation(self, worksheet: Worksheet, region: Range, rule: dict[str, Any] | None, *,
                            filtered_rows_included: bool = False,
                            safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Set a native rule; an explicit None clears rules, never a missing argument."""
        if type(filtered_rows_included) is not bool:
            raise SheetServiceError(Code.INVALID_INPUT, field="filtered_rows_included")
        if rule is not None:
            rule = self._native_mapping(rule, "rule")
            condition = rule.get("condition")
            if (not set(rule) <= {"condition", "inputMessage", "strict", "showCustomUi"}
                    or not isinstance(condition, dict) or not set(condition) <= {"type", "values"}
                    or not isinstance(condition.get("type"), str) or not condition["type"].strip()
                    or condition["type"] == "CONDITION_TYPE_UNSPECIFIED"
                    or any(name in rule and type(rule[name]) is not bool for name in ("strict", "showCustomUi"))
                    or "inputMessage" in rule and not isinstance(rule["inputMessage"], str)):
                raise SheetServiceError(Code.INVALID_INPUT, field="rule")
            values = condition.get("values", [])
            if (not isinstance(values, list) or any(not isinstance(item, dict) or len(item) != 1
                    or next(iter(item)) not in ("userEnteredValue", "relativeDate")
                    or not isinstance(next(iter(item.values())), str) for item in values)):
                raise SheetServiceError(Code.INVALID_INPUT, field="rule")
        sheet = self._grid_worksheet(worksheet)
        bounded = self._region(sheet, region)
        request = {"range": self._grid_range(sheet, bounded), "filteredRowsIncluded": filtered_rows_included}
        if rule is not None:
            request["rule"] = rule
        return self.batch_update([{"setDataValidation": request}], safety=safety)

    def _dimension_size(self, worksheet: Worksheet, dimension: str, start: int, end: int, pixels: int,
                        safety: WriteSafety | None) -> APIResponse | RequestPreview:
        index(start, "start")
        index(end, "end", positive=True)
        index(pixels, "pixel_size", positive=True)
        sheet = self._grid_worksheet(worksheet)
        size = sheet.row_count if dimension == "ROWS" else sheet.column_count
        if not start < end <= size:
            raise SheetServiceError(Code.INVALID_RANGE, field="dimension_range")
        return self.batch_update([{"updateDimensionProperties": {
            "range": {"sheetId": sheet.sheet_id, "dimension": dimension, "startIndex": start, "endIndex": end},
            "properties": {"pixelSize": pixels}, "fields": "pixelSize"}}], safety=safety)

    def set_column_width(self, worksheet: Worksheet, start: int, end: int, width: int, *,
                         safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self._dimension_size(worksheet, "COLUMNS", start, end, width, safety)

    def set_row_height(self, worksheet: Worksheet, start: int, end: int, height: int, *,
                       safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        return self._dimension_size(worksheet, "ROWS", start, end, height, safety)

    def freeze_rows(self, worksheet: Worksheet, count: int, *, safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Set the top frozen prefix only; count=0 explicitly unfreezes rows."""
        index(count, "count")
        sheet = self._grid_worksheet(worksheet)
        if count > sheet.row_count:
            raise SheetServiceError(Code.INVALID_RANGE, field="count")
        return self.batch_update([{"updateSheetProperties": {
            "properties": {"sheetId": sheet.sheet_id, "gridProperties": {"frozenRowCount": count}},
            "fields": "gridProperties.frozenRowCount"}}], safety=safety)

    def copy_paste_range(self, source_worksheet: Worksheet, source_region: Range,
                         destination_worksheet: Worksheet, destination_region: Range, *,
                         paste_type: str, paste_orientation: str = "NORMAL",
                         safety: WriteSafety | None = None) -> APIResponse | RequestPreview:
        """Same-spreadsheet native copy with an explicit aspect policy and exact bounds.

        All destination positions are treated as potentially touched for optional
        formula protection. Native copy interpretation is not USER_ENTERED parsing.
        """
        if paste_type not in ("PASTE_NORMAL", "PASTE_VALUES", "PASTE_FORMAT", "PASTE_NO_BORDERS",
                              "PASTE_FORMULA", "PASTE_DATA_VALIDATION", "PASTE_CONDITIONAL_FORMATTING"):
            raise SheetServiceError(Code.INVALID_INPUT, field="paste_type")
        if paste_orientation not in ("NORMAL", "TRANSPOSE"):
            raise SheetServiceError(Code.INVALID_INPUT, field="paste_orientation")
        source, source_bounds = self._merge_region(source_worksheet, source_region)
        destination, destination_bounds = self._merge_region(destination_worksheet, destination_region)
        height = source_bounds.end_row - source_bounds.start_row
        width = source_bounds.end_column - source_bounds.start_column
        if paste_orientation == "TRANSPOSE":
            height, width = width, height
        if ((destination_bounds.end_row - destination_bounds.start_row) % height
                or (destination_bounds.end_column - destination_bounds.start_column) % width):
            # Google may expand a too-small destination; never permit that silently.
            raise SheetServiceError(Code.INVALID_RANGE, field="copy_dimensions")
        requests = [{"copyPaste": {"source": self._grid_range(source, source_bounds),
            "destination": self._grid_range(destination, destination_bounds),
            "pasteType": paste_type, "pasteOrientation": paste_orientation}}]
        preview = self._write_guard(SheetsEndpoint.SPREADSHEET_BATCH_UPDATE, sheet=destination,
            regions=[destination_bounds], body={"requests": requests}, clear=True, safety=safety)
        if preview is not None:
            return replace(preview, checks=preview.checks + ("worksheet_ids", "copy_dimensions"))
        return self.batch_update(requests)

    def reorder_columns(self, worksheet: Worksheet, order: Sequence[int], *, start_column: int = 0) -> APIResponse | None:
        index(start_column, "start_column")
        sheet = self.get_worksheet(worksheet, force_refresh=True)
        if not isinstance(order, (list, tuple)) or not order or any(type(value) is not int for value in order):
            raise SheetServiceError(Code.INVALID_INPUT, field="order")
        current = list(range(start_column, start_column + len(order)))
        if sorted(order) != current or current[-1] >= sheet.column_count:
            raise SheetServiceError(Code.INVALID_RANGE, field="order")
        requests = []
        for offset, desired in enumerate(order):
            source = current.index(desired)
            if source != offset:
                requests.append({"moveDimension": {"source": {"sheetId": sheet.sheet_id, "dimension": "COLUMNS",
                    "startIndex": start_column + source, "endIndex": start_column + source + 1},
                    "destinationIndex": start_column + offset}})
                current.insert(offset, current.pop(source))
        return self._structural(requests) if requests else None
