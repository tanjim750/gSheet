"""Offline service verification: run with the project's installed Python environment.

Uses only synthetic data, autospecced clients, and mocked HTTP execution. No local
environment/credential files, browsers, or real spreadsheets are accessed.
"""

import copy
import itertools
import unittest
from unittest.mock import create_autospec, patch

from google.oauth2.credentials import Credentials
from requests import Response
from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout, Timeout

from gsheet import (
    APIClientError, APIConfig, APIErrorCode, APIResponse, AuthorizationPolicy, CellRange,
    CredentialManager, GSheetConfig, OperationKind, OperationalConfig, ServiceErrorCode,
    SheetService, SheetServiceError, SheetsClient, StructuralChange,
    RequestPreview, SheetsEndpoint, WritePrecondition, WriteSafety,
    column_a1_to_index, column_index_to_a1, format_a1_range, parse_a1_range,
    quote_sheet_name, row_a1_to_index, row_index_to_a1,
)

TITLE = "O'Brien ! 漢字"
METADATA = {"spreadsheetId": "offline", "sheets": [
    {"properties": {"sheetId": 17, "title": TITLE, "index": 0,
                    "gridProperties": {"rowCount": 10, "columnCount": 6}},
     "merges": [{"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 3}]},
    {"properties": {"sheetId": 42, "title": "Other", "index": 1,
                    "gridProperties": {"rowCount": 8, "columnCount": 4}}},
]}


def response(data):
    return APIResponse(copy.deepcopy(data), 200, "offline", 1, 0.0)


class ServiceChecks(unittest.TestCase):
    def setUp(self):
        self.client = create_autospec(SheetsClient, instance=True)
        self.client.get_spreadsheet.return_value = response(METADATA)
        self.client.get_values.return_value = response({"values": [["Name", None, "Name", "Value"]]})
        self.client.batch_update.return_value = response({"replies": []})
        self.client.update_values.return_value = response({"updatedCells": 1})
        self.client.batch_update_values.return_value = response({"totalUpdatedCells": 2})
        self.service = SheetService(self.client, "offline")

    def rejects(self, code, function):
        with self.assertRaises(SheetServiceError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def test_constructor_is_inert(self):
        self.client.get_spreadsheet.assert_not_called()
        self.assertNotIn("offline", repr(self.service))

    def test_a1_roundtrips_and_boundaries(self):
        for number in range(2000):
            self.assertEqual(column_a1_to_index(column_index_to_a1(number)), number)
            self.assertEqual(row_a1_to_index(row_index_to_a1(number)), number)
        for row, column in itertools.product((0, 1, 9), (0, 25, 26, 702)):
            region = CellRange(row, column, row + 3, column + 2)
            self.assertEqual(parse_a1_range(format_a1_range(TITLE, region)), (TITLE, region))
        self.assertEqual(quote_sheet_name(TITLE), "'O''Brien ! 漢字'")
        for bad in (-1, True, 1.5):
            self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: column_index_to_a1(bad))
        for bad in ("A0", "B2:A1", "A1::B2", "NamedRange", "'broken!A1", "A1!B2!C3", "A1:B0"):
            self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: parse_a1_range(bad))
        self.assertEqual(parse_a1_range("$A$2:B")[1].bounded(10, 6), CellRange(1, 0, 10, 2))
        self.assertEqual(parse_a1_range("2:4")[1].bounded(10, 6), CellRange(1, 0, 4, 6))
        self.assertEqual(parse_a1_range("A:C")[1].bounded(10, 6), CellRange(0, 0, 10, 3))
        self.assertEqual(parse_a1_range("A5:")[1], CellRange(4, 0, None, None))
        self.assertEqual(parse_a1_range("A5:")[1].bounded(10, 6), CellRange(4, 0, 10, 6))

    def test_worksheet_metadata_and_missing(self):
        self.assertEqual([s.sheet_id for s in self.service.list_worksheets()], [17, 42])
        self.assertEqual(self.service.get_worksheet(TITLE).sheet_id, 17)
        self.assertEqual(self.service.get_worksheet(17).merged_ranges, (CellRange(0, 1, 1, 3),))
        self.assertFalse(self.service.worksheet_exists("missing"))
        self.rejects(ServiceErrorCode.WORKSHEET_NOT_FOUND, lambda: self.service.get_worksheet("missing"))
        self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: self.service.get_worksheet(True))
        self.client.get_spreadsheet.side_effect = APIClientError(APIErrorCode.PERMISSION_DENIED, status_code=403)
        with self.assertRaises(APIClientError):
            self.service.worksheet_exists("missing")

    def test_tab_payloads_resolve_numeric_ids(self):
        self.service.create_worksheet("New", rows=4, columns=3)
        self.assertEqual(self.client.batch_update.call_args.args[1], {
            "requests": [{"addSheet": {"properties": {"title": "New", "gridProperties": {"rowCount": 4, "columnCount": 3}}}}]})
        self.service.rename_worksheet(TITLE, "Renamed")
        self.assertEqual(self.client.batch_update.call_args.args[1]["requests"][0], {
            "updateSheetProperties": {"properties": {"sheetId": 17, "title": "Renamed"}, "fields": "title"}})
        self.service.duplicate_worksheet(17, title="Copy")
        self.assertEqual(self.client.batch_update.call_args.args[1]["requests"][0], {
            "duplicateSheet": {"sourceSheetId": 17, "newSheetName": "Copy"}})
        self.service.delete_worksheet(TITLE)
        self.assertEqual(self.client.batch_update.call_args.args[1]["requests"], [{"deleteSheet": {"sheetId": 17}}])
        self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: self.service.create_worksheet(TITLE))

    def test_sparse_and_padded_reads_preserve_types(self):
        data = {"values": [["", 1, False], [], ["=A1"]]}
        self.client.get_values.return_value = response(data)
        sparse = self.service.read_range(17, "A2:D5", read_mode="FORMULA")
        self.assertEqual(sparse.values, data["values"])
        dense = self.service.read_range(17, "A2:D5", pad=True)
        self.assertEqual(dense.values, [["", 1, False, None], [None] * 4, ["=A1", None, None, None], [None] * 4])
        self.assertEqual(dense.region, CellRange(1, 0, 5, 4))
        self.assertEqual(self.client.get_values.return_value.data, data)
        self.assertNotIn("=A1", repr(dense))
        self.client.get_values.return_value = response({})
        self.assertIsNone(self.service.read_cell(17, 2, 2))

    def test_read_limits_and_response_validation(self):
        service = SheetService(self.client, "offline", limits=OperationalConfig(max_read_cells=4))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: service.read_range(17, "A:B"))
        self.client.get_values.assert_not_called()
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: service.read_range(17, "'Other'!A1"))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: service.read_range(17, "A11"))
        self.client.get_values.return_value = response({"values": [[1, 2]]})
        self.rejects(ServiceErrorCode.MALFORMED_RESPONSE, lambda: service.read_cell(17, 1, 1))
        self.client.get_spreadsheet.return_value = response({"sheets": [{"properties": {}}]})
        self.rejects(ServiceErrorCode.MALFORMED_RESPONSE, self.service.list_worksheets)
        for partial in ({}, {"spreadsheetId": "offline"}, {**METADATA, "spreadsheetId": "foreign"}):
            self.client.get_spreadsheet.return_value = response(partial)
            self.rejects(ServiceErrorCode.MALFORMED_RESPONSE, self.service.list_worksheets)

    def test_noncontiguous_reads_and_writes(self):
        self.client.batch_get_values.return_value = response({"valueRanges": [{"values": [[1]]}, {}]})
        result = self.service.read_ranges(17, ["A2", "C4:D4"], pad=True)
        self.assertEqual([item.values for item in result], [[[1]], [[None, None]]])
        self.client.get_spreadsheet.assert_called_once()
        self.service.update_cells(17, [(1, 0, 2), (2, 3, "=SUM(A1:A2)")])
        payload = self.client.batch_update_values.call_args.args[1]
        self.assertEqual(payload["valueInputOption"], "RAW")
        self.assertEqual([item["range"] for item in payload["data"]], [quote_sheet_name(TITLE) + "!A2", quote_sheet_name(TITLE) + "!D3"])
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.update_ranges(17, [("A2:B3", [[1]]), ("B3", [[2]])]))
        self.client.batch_get_values.return_value = response({"valueRanges": []})
        self.rejects(ServiceErrorCode.MALFORMED_RESPONSE, lambda: self.service.read_ranges(17, ["A1"]))

    def test_headers_are_explicit_and_duplicates_never_guessed(self):
        self.assertEqual(self.service.find_columns(17, "Name", header_row=2), [0, 2])
        self.rejects(ServiceErrorCode.AMBIGUOUS_HEADER, lambda: self.service.find_column(17, "Name", header_row=2))
        self.rejects(ServiceErrorCode.AMBIGUOUS_HEADER, lambda: self.service.rename_header(17, "Name", "Wrong", header_row=2))
        self.client.update_values.assert_not_called()
        self.assertEqual(self.service.find_column(17, "Name", header_row=2, occurrence=1), 2)
        self.rejects(ServiceErrorCode.HEADER_NOT_FOUND, lambda: self.service.find_column(17, "Missing", header_row=2))
        self.service.rename_header(17, "Name", "Label", header_row=2, occurrence=1)
        self.assertEqual(self.client.update_values.call_args.args[1:], (quote_sheet_name(TITLE) + "!C3", {"values": [["Label"]]}))
        self.service.rename_header(17, "Name", "=A1", header_row=2, occurrence=1, write_mode="USER_ENTERED")
        self.assertEqual(self.client.update_values.call_args.kwargs["params"], {"valueInputOption": "USER_ENTERED"})
        self.service.set_headers(17, ["A", "B"], header_row=3, start_column=1)
        self.assertEqual(self.client.update_values.call_args.args[1], quote_sheet_name(TITLE) + "!B4:C4")
        self.client.get_values.return_value = response({"values": [["Group"], ["Leaf"]]})
        self.assertEqual(len(self.service.read_headers(17, header_row=2, depth=2).values), 2)

    def test_rows_append_and_clear_are_different_operations(self):
        self.service.update_rows(17, 3, [[1, None, 2], [], [False]], start_column=1)
        self.assertEqual(self.client.update_values.call_args.args[2], {"values": [[1, None, 2], [], [False]]})
        self.service.append_rows(17, [[1], [2]], table_range="A2:C10", write_mode="USER_ENTERED")
        self.assertEqual(self.client.append_values.call_args.kwargs["params"]["valueInputOption"], "USER_ENTERED")
        self.service.clear_row(17, 3)
        self.client.clear_values.assert_called_once_with("offline", quote_sheet_name(TITLE) + "!A4:F4")
        self.service.delete_rows(17, 3, 4)
        self.assertIn("deleteDimension", self.client.batch_update.call_args.args[1]["requests"][0])
        self.client.get_values.return_value = response({"values": [[0], [False], [1], []]})
        self.assertEqual(self.service.find_rows(17, 0, 0, start_row=1, end_row=5), [1])

    def test_merge_guard_does_not_unmerge_or_flatten(self):
        self.rejects(ServiceErrorCode.UNSUPPORTED_LAYOUT, lambda: self.service.update_cell(17, 0, 2, "covered"))
        self.client.update_values.assert_not_called()
        self.service.update_cell(17, 0, 1, "anchor")
        self.service.update_range(17, "B1:C1", [["anchor", None]])
        self.client.batch_update.assert_not_called()

    def test_structural_order_and_current_dimensions(self):
        self.service.batch_structure([
            StructuralChange("insert_rows", TITLE, start=1, count=2),
            StructuralChange("delete_rows", 17, start=10, end=12),
            StructuralChange("add_columns", 17, count=2),
            StructuralChange("insert_columns", 17, start=1, count=1),
            StructuralChange("move_columns", 17, start=1, end=3, destination=6),
        ])
        self.client.get_spreadsheet.assert_called_once()
        requests = self.client.batch_update.call_args.args[1]["requests"]
        self.assertEqual([next(iter(item)) for item in requests], ["insertDimension", "deleteDimension", "appendDimension", "insertDimension", "moveDimension"])
        self.assertEqual(requests[1]["deleteDimension"]["range"]["endIndex"], 12)
        self.assertEqual(requests[-1]["moveDimension"]["destinationIndex"], 6)
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.insert_rows(17, 0, inherit_from_before=True))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.delete_rows(17, 0, 10))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.move_columns(17, 1, 3, 2))

    def test_reordering_all_permutations_preserves_other_columns(self):
        for permutation in itertools.permutations(range(1, 5)):
            self.client.batch_update.reset_mock()
            returned = self.service.reorder_columns(17, list(permutation), start_column=1)
            current = list(range(6))
            if returned is not None:
                for item in self.client.batch_update.call_args.args[1]["requests"]:
                    move = item["moveDimension"]
                    start, end, destination = move["source"]["startIndex"], move["source"]["endIndex"], move["destinationIndex"]
                    block = current[start:end]
                    del current[start:end]
                    if destination > end:
                        destination -= end - start
                    current[destination:destination] = block
            self.assertEqual(current, [0, *permutation, 5])

    def test_client_failures_propagate_without_partial_replay(self):
        error = APIClientError(APIErrorCode.UNCERTAIN_WRITE, outcome="uncertain")
        self.client.update_values.side_effect = error
        with self.assertRaises(APIClientError) as caught:
            self.service.update_cell(17, 2, 1, "literal")
        self.assertIs(caught.exception, error)
        self.client.update_values.assert_called_once()

    def test_bad_batch_is_fully_validated_before_mutation(self):
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.batch_structure([
            StructuralChange("insert_rows", 17, start=1),
            StructuralChange("delete_rows", 17, start=1, end=100),
        ]))
        self.client.batch_update.assert_not_called()
        self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: self.service.update_ranges(17, [("A1", [[float("nan")]])]))
        self.client.batch_update_values.assert_not_called()


class AuthorizationChecks(unittest.TestCase):
    def test_real_transport_still_authorizes_every_operation(self):
        config = GSheetConfig(api=APIConfig(access_mode="read_write"),
                             operations=OperationalConfig(logging_level="CRITICAL"), environ={})
        manager = CredentialManager(config, connection_id="synthetic-only")
        policy = AuthorizationPolicy({"offline"}, {OperationKind.READ, OperationKind.WRITE, OperationKind.STRUCTURAL},
                                     {"insertDimension", "deleteDimension", "updateSheetProperties"})
        with SheetsClient(config, manager, authorization=policy) as client:
            def execute(method, url, **kwargs):
                reply = Response()
                reply.status_code = 200
                import json
                reply._content = json.dumps(METADATA if method == "GET" else {}).encode()
                reply._content_consumed = True
                return reply
            with patch.object(manager, "get_credentials", return_value=Credentials("synthetic")), \
                    patch.object(client._transport._session, "request", side_effect=execute) as network:
                service = SheetService(client, "offline")
                service.insert_rows(17, 1)
                self.assertEqual(network.call_args.args[0], "POST")
                for action in (lambda: service.delete_rows(17, 1, 2), lambda: service.clear_cell(17, 1, 1),
                               lambda: service.rename_worksheet(17, "Renamed")):
                    before = network.call_count
                    with self.assertRaises(APIClientError) as caught:
                        action()
                    self.assertEqual(caught.exception.code, APIErrorCode.PERMISSION_DENIED)
                    self.assertIn("destructive", caught.exception.missing_operations)
                    self.assertEqual(network.call_count, before + 1)  # Metadata GET only.
                before = network.call_count
                with self.assertRaises(APIClientError) as caught:
                    service.add_columns(17)
                self.assertEqual(caught.exception.reason.value, "batch_request_not_allowed")
                self.assertEqual(network.call_count, before + 1)
                before = network.call_count
                with self.assertRaises(APIClientError):
                    SheetService(client, "foreign").get_worksheet(17)
                self.assertEqual(network.call_count, before)


class P0Checks(unittest.TestCase):
    def test_leading_interior_trailing_and_entirely_missing_positions(self):
        data = {"values": [[None, "x"], [], ["y", None, None]]}
        self.client.get_values.return_value = response(data)
        result = self.service.read_range(17, "A4:D7", pad=True)
        self.assertEqual(result.values, [[None, "x", None, None], [None] * 4,
                                         ["y", None, None, None], [None] * 4])
        self.assertEqual(result.region, CellRange(3, 0, 7, 4))
        self.assertEqual(self.client.get_values.return_value.data, data)

    rejects = ServiceChecks.rejects

    def setUp(self):
        ServiceChecks.setUp(self)
        self.grid = {
            (1, 0): {"userEnteredValue": {"formulaValue": "=1+1"}, "effectiveValue": {"numberValue": 2},
                     "formattedValue": "2", "effectiveFormat": {"numberFormat": {"type": "NUMBER"}}},
            (1, 1): {"userEnteredValue": {"stringValue": ""}, "effectiveValue": {"stringValue": ""}, "formattedValue": ""},
            (1, 2): {"userEnteredValue": {"numberValue": 45200}, "effectiveValue": {"numberValue": 45200},
                     "effectiveFormat": {"numberFormat": {"type": "DATE"}}},
            (2, 0): {"userEnteredValue": {"boolValue": False}, "effectiveValue": {"boolValue": False}},
            (2, 1): {},
            (2, 2): {"userEnteredValue": {"formulaValue": "=1/0"},
                     "effectiveValue": {"errorValue": {"type": "DIVIDE_BY_ZERO", "message": "SECRET_MARKER"}}},
        }

        def spreadsheet(identifier, *, params=None):
            if not params or not params.get("includeGridData"):
                return response(METADATA)
            title, region = parse_a1_range(params["ranges"][0])
            props = next(item["properties"] for item in METADATA["sheets"] if item["properties"]["title"] == title)
            observed = {position: data for position, data in self.grid.items()
                        if region.start_row <= position[0] < region.end_row and region.start_column <= position[1] < region.end_column}
            rows = []
            if observed:
                for row in range(region.start_row, max(p[0] for p in observed) + 1):
                    columns = [col for r, col in observed if r == row]
                    rows.append({"values": [observed.get((row, col), {}) for col in
                        range(region.start_column, max(columns) + 1)]} if columns else {})
            return response({"spreadsheetId": identifier, "properties": {"locale": "en_US", "timeZone": "UTC"},
                "sheets": [{"properties": props, "data": [{"startRow": region.start_row,
                    "startColumn": region.start_column, "rowData": rows}]}]})
        self.client.get_spreadsheet.side_effect = spreadsheet
        self.client.validate_request.side_effect = lambda endpoint, identifier, **kwargs: RequestPreview(
            endpoint, "PUT" if endpoint == SheetsEndpoint.VALUES_UPDATE else "POST", identifier,
            kwargs.get("range_name"), kwargs.get("params") or {}, copy.deepcopy(kwargs.get("body")))

    def test_resolution_preserves_unknown_bounds_and_identity(self):
        result = self.service.resolve_range(17, "B2:C")
        self.assertEqual((result.sheet_id, result.spreadsheet_id), (17, "offline"))
        self.assertIsNone(result.region.end_row)
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: result.a1)
        resolved = self.service.resolve_range(TITLE, "B2:C", bounded=True)
        self.assertEqual(resolved.region, CellRange(1, 1, 10, 3))
        self.assertEqual(parse_a1_range(resolved.a1), (TITLE, resolved.region))
        merged = self.service.get_merged_ranges(17)
        self.assertEqual(merged[0].sheet_id, 17)
        self.assertEqual(merged[0].region, CellRange(0, 1, 1, 3))
        self.assertEqual(self.service.get_merged_ranges(42), ())

    def test_grid_fields_coordinates_formulas_errors_and_dates(self):
        result = self.service.get_grid_data(17, "A2:C4", pad=True)
        self.assertEqual((result.locale, result.timezone), ("en_US", "UTC"))
        self.assertEqual(len(result.cells), 9)
        cells = {(cell.row, cell.column): cell for cell in result.cells}
        self.assertEqual(cells[1, 0].formula, "=1+1")
        self.assertEqual(cells[1, 0].effective_value, {"numberValue": 2})
        self.assertEqual(cells[1, 0].formatted_value, "2")
        self.assertEqual(cells[1, 2].effective_value["numberValue"], 45200)
        self.assertEqual(cells[1, 2].number_format["type"], "DATE")
        self.assertEqual(cells[2, 2].error["type"], "DIVIDE_BY_ZERO")
        self.assertIs(cells[2, 0].effective_value["boolValue"], False)
        self.assertEqual(cells[1, 1].user_entered_value, {"stringValue": ""})
        self.assertTrue(cells[2, 1].entry_present)
        self.assertFalse(cells[3, 1].entry_present)
        self.assertNotIn("SECRET_MARKER", repr(result) + repr(cells[2, 2]))
        params = self.client.get_spreadsheet.call_args.kwargs["params"]
        self.assertTrue(params["includeGridData"])
        self.assertEqual(params["fields"].count("("), params["fields"].count(")"))
        self.assertEqual(params["ranges"], [quote_sheet_name(TITLE) + "!A2:C4"])
        sparse = self.service.get_grid_data(17, "A2:C4", fields=("userEnteredValue",))
        self.assertEqual(len(sparse.cells), 6)
        self.assertEqual(sparse.cells[0].requested_fields, ("userEnteredValue",))

    def test_grid_validation_and_limits(self):
        self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: self.service.get_grid_data(17, "A1", fields=("values),bad(",)))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.get_grid_data(17, "Z1"))
        self.grid[1, 0]["effectiveValue"] = {"numberValue": "not numeric"}
        self.rejects(ServiceErrorCode.MALFORMED_RESPONSE, lambda: self.service.get_grid_data(17, "A2"))
        limited = SheetService(self.client, "offline", limits=OperationalConfig(max_read_cells=1))
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: limited.get_grid_data(17, "A2:C4"))

    def test_precondition_values_identity_dimensions_and_failure(self):
        self.client.get_values.return_value = response({"values": [[10]]})
        safety = WriteSafety(preconditions=(WritePrecondition(expected_values=[[10]], expected_sheet_id=17,
                                                             expected_dimensions=(10, 6)),))
        self.service.update_cell(17, 3, 0, 11, safety=safety)
        self.client.update_values.assert_called_once()
        self.client.update_values.reset_mock()
        for condition in (WritePrecondition(expected_values=[[12]]), WritePrecondition(expected_sheet_id=42),
                          WritePrecondition(expected_dimensions=(11, 6)), WritePrecondition(expected_values=[[False]])):
            self.rejects(ServiceErrorCode.PRECONDITION_FAILED, lambda: self.service.update_cell(
                17, 3, 0, 11, safety=WriteSafety(preconditions=(condition,))))
        self.client.update_values.assert_not_called()
        self.rejects(ServiceErrorCode.INVALID_RANGE, lambda: self.service.update_cell(17, 3, 0, 1,
            safety=WriteSafety(preconditions=(WritePrecondition(expected_values=[[1, 2]]),))))

    def test_formula_and_observable_empty_preconditions(self):
        self.service.update_cell(17, 1, 0, 7, safety=WriteSafety(preconditions=(WritePrecondition(expected_formula="=1+1"),)))
        self.service.update_cell(17, 2, 1, 7, safety=WriteSafety(preconditions=(WritePrecondition(expected_empty=True),)))
        self.service.update_cell(17, 1, 1, 7, safety=WriteSafety(preconditions=(WritePrecondition(expected_empty=False),)))
        self.client.update_values.reset_mock()
        for region, condition in (("A2", WritePrecondition(expected_formula="=9")),
                                  ("B2", WritePrecondition(expected_empty=True)),
                                  ("D5", WritePrecondition(expected_empty=True))):
            self.rejects(ServiceErrorCode.PRECONDITION_FAILED, lambda: self.service.update_range(
                17, region, [[1]], safety=WriteSafety(preconditions=(condition,))))
        self.client.update_values.assert_not_called()

    def test_formula_protection_only_checks_touched_cells(self):
        self.rejects(ServiceErrorCode.FORMULA_PROTECTED, lambda: self.service.update_cell(17, 1, 0, "text", safety=WriteSafety(protect_formulas=True)))
        self.rejects(ServiceErrorCode.FORMULA_PROTECTED, lambda: self.service.clear_range(17, "A2", safety=WriteSafety(protect_formulas=True)))
        self.client.update_values.assert_not_called()
        self.service.update_cell(17, 1, 0, None, safety=WriteSafety(protect_formulas=True))
        self.service.update_cells(17, [(1, 0, None), (2, 1, "literal")], safety=WriteSafety(protect_formulas=True))
        self.client.batch_update_values.assert_called_once()
        self.rejects(ServiceErrorCode.UNSUPPORTED_LAYOUT, lambda: self.service.append_rows(
            17, [[1]], table_range="A2:C5", safety=WriteSafety(protect_formulas=True)))

    def test_dry_run_checks_reads_and_never_submits_mutation(self):
        self.client.get_values.return_value = response({"values": [[10]]})
        plan = self.service.update_cell(17, 3, 0, "SECRET_MARKER", safety=WriteSafety(
            dry_run=True, preconditions=(WritePrecondition(expected_values=[[10]]),), protect_formulas=True))
        self.assertEqual(plan.outcome, "validation_only")
        self.assertIn("authorization", plan.checks)
        self.assertIn("preconditions", plan.checks)
        self.assertIn("formula_protection", plan.checks)
        self.assertEqual(plan.body, {"values": [["SECRET_MARKER"]]})
        self.assertNotIn("SECRET_MARKER", repr(plan))
        self.client.update_values.assert_not_called()
        self.service.update_ranges(17, [("A4", [[1]]), ("C4", [[2]])], safety=WriteSafety(dry_run=True))
        self.service.append_rows(17, [[1]], table_range="A2:C5", safety=WriteSafety(dry_run=True))
        self.client.batch_update_values.assert_not_called()
        self.client.append_values.assert_not_called()

    def test_structural_preview_and_precondition_without_replay(self):
        plan = self.service.batch_structure([StructuralChange("delete_rows", 17, start=2, end=3)],
            safety=WriteSafety(dry_run=True, preconditions=(WritePrecondition(worksheet=17, expected_dimensions=(10, 6)),)))
        self.assertEqual(plan.body["requests"][0]["deleteDimension"]["range"]["sheetId"], 17)
        self.assertIn("worksheet_ids", plan.checks)
        self.client.batch_update.assert_not_called()
        self.rejects(ServiceErrorCode.WORKSHEET_NOT_FOUND, lambda: self.service.batch_update(
            [{"deleteSheet": {"sheetId": 999}}], safety=WriteSafety(dry_run=True)))

    def test_literal_strings_and_original_service_methods(self):
        literals = ["=1+1", "+1", "-1", "@value"]
        self.service.update_range(17, "A5:D5", [literals])
        self.assertEqual(self.client.update_values.call_args.args[2], {"values": [literals]})
        self.assertEqual(self.client.update_values.call_args.kwargs["params"], {"valueInputOption": "RAW"})
        self.service.update_range(17, "A5:D5", [literals], write_mode="USER_ENTERED")
        self.assertEqual(self.client.update_values.call_args.kwargs["params"], {"valueInputOption": "USER_ENTERED"})
        original = """get_spreadsheet list_worksheets get_worksheet worksheet_exists create_worksheet
            rename_worksheet delete_worksheet duplicate_worksheet read_range read_ranges update_range update_ranges
            clear_range read_cell update_cell clear_cell update_cells read_rows read_row update_rows update_row
            append_rows clear_row find_rows read_headers set_headers find_columns find_column rename_header
            batch_structure insert_rows delete_rows add_columns insert_columns remove_columns move_columns reorder_columns""".split()
        self.assertEqual(len(original), 37)
        self.assertTrue(all(callable(getattr(self.service, name)) for name in original))

    def test_failed_preconditions_block_all_supported_mutation_paths(self):
        self.client.get_values.return_value = response({"values": [[10]]})
        safety = WriteSafety(preconditions=(WritePrecondition(worksheet=17, region="A4", expected_values=[[99]]),))
        operations = (
            lambda: self.service.update_range(17, "A4", [[1]], safety=safety),
            lambda: self.service.update_ranges(17, [("A4", [[1]]), ("C4", [[2]])], safety=safety),
            lambda: self.service.append_rows(17, [[1]], table_range="A2:C5", safety=safety),
            lambda: self.service.clear_range(17, "A4", safety=safety),
            lambda: self.service.batch_update([{"deleteDimension": {"range": {"sheetId": 17,
                "dimension": "ROWS", "startIndex": 2, "endIndex": 3}}}], safety=safety),
        )
        for operation in operations:
            self.rejects(ServiceErrorCode.PRECONDITION_FAILED, operation)
        for name in ("update_values", "batch_update_values", "append_values", "clear_values", "batch_update"):
            getattr(self.client, name).assert_not_called()

    def test_permission_rejection_precedes_precondition_reads(self):
        self.client.validate_request.side_effect = APIClientError(APIErrorCode.PERMISSION_DENIED)
        with self.assertRaises(APIClientError):
            self.service.update_cell(17, 3, 0, 1,
                safety=WriteSafety(preconditions=(WritePrecondition(expected_values=[[1]]),), dry_run=True))
        self.client.get_values.assert_not_called()
        self.client.update_values.assert_not_called()

    def test_malformed_structural_and_grid_payloads_fail_safely(self):
        for requests in ([{"deleteDimension": {"range": {"sheetId": 17, "startIndex": -1, "endIndex": 2}}}],
                         [{"addSheet": {"properties": None}}]):
            with self.assertRaises(SheetServiceError):
                self.service.batch_update(requests, safety=WriteSafety(dry_run=True))
        cycle = {}
        cycle["cycle"] = cycle
        self.rejects(ServiceErrorCode.INVALID_INPUT, lambda: self.service.batch_update([{"updateCells": cycle}]))
        self.client.batch_update.assert_not_called()
        metadata = copy.deepcopy(METADATA)
        metadata["sheets"][0]["properties"]["sheetType"] = "DATA_SOURCE"
        self.client.get_spreadsheet.side_effect = None
        self.client.get_spreadsheet.return_value = response(metadata)
        before = self.client.get_spreadsheet.call_count
        self.rejects(ServiceErrorCode.UNSUPPORTED_LAYOUT, lambda: self.service.get_grid_data(17, "A1"))
        self.assertEqual(self.client.get_spreadsheet.call_count, before + 1)

    def test_stale_dimensions_and_sorted_targets_are_not_guessed(self):
        changed = copy.deepcopy(METADATA)
        changed["sheets"][0]["properties"]["gridProperties"]["rowCount"] = 11
        self.client.get_spreadsheet.side_effect = lambda *args, **kwargs: response(changed)
        self.rejects(ServiceErrorCode.PRECONDITION_FAILED, lambda: self.service.update_cell(17, 3, 0, 1,
            safety=WriteSafety(preconditions=(WritePrecondition(expected_dimensions=(10, 6)),))))
        self.client.get_values.return_value = response({"values": [["different-row-after-sort"]]})
        self.rejects(ServiceErrorCode.PRECONDITION_FAILED, lambda: self.service.update_cell(17, 3, 0, 1,
            safety=WriteSafety(preconditions=(WritePrecondition(expected_values=[["original-row"]]),))))
        self.client.update_values.assert_not_called()


class P0TransportChecks(unittest.TestCase):
    def test_before_and_after_submission_failures_remain_distinct(self):
        config = GSheetConfig(api=APIConfig(access_mode="read_write"), operations=OperationalConfig(logging_level="CRITICAL"), environ={})
        manager = CredentialManager(config, connection_id="synthetic")
        policy = AuthorizationPolicy({"offline"}, set(OperationKind), {"insertDimension"})
        with SheetsClient(config, manager, authorization=policy) as client, patch.object(manager, "get_credentials", return_value=Credentials("synthetic")):
            for error, code, outcome in ((ConnectTimeout("before submission"), APIErrorCode.TIMEOUT, "confirmed_failure"),
                                         (ReadTimeout("possibly submitted"), APIErrorCode.UNCERTAIN_WRITE, "uncertain"),
                                         (ConnectionError("interrupted"), APIErrorCode.UNCERTAIN_WRITE, "uncertain")):
                for operation in (lambda: client.append_values("offline", "A1", {"values": [[1]]}),
                                  lambda: client.batch_update("offline", {"requests": [{"insertDimension": {}}]})):
                    with patch.object(client._transport._session, "request", side_effect=error) as send, patch("gsheet.client.transport.time.sleep") as sleep:
                        with self.assertRaises(APIClientError) as caught:
                            operation()
                        self.assertEqual((caught.exception.code, caught.exception.outcome), (code, outcome))
                        self.assertEqual(send.call_count, 1)
                        sleep.assert_not_called()

    def test_read_retries_and_confirmed_api_rejection(self):
        config = GSheetConfig(api=APIConfig(access_mode="read_write"), operations=OperationalConfig(logging_level="CRITICAL"), environ={})
        manager = CredentialManager(config, connection_id="synthetic")
        policy = AuthorizationPolicy({"offline"}, {OperationKind.READ, OperationKind.WRITE})
        def reply(status):
            result = Response()
            result.status_code = status
            result._content = b'{}'
            result._content_consumed = True
            result.headers["Retry-After"] = "2"
            return result
        with SheetsClient(config, manager, authorization=policy) as client, patch.object(manager, "get_credentials", return_value=Credentials("synthetic")):
            with patch.object(client._transport._session, "request", side_effect=[reply(429), reply(200)]) as send, patch("gsheet.client.transport.time.sleep") as sleep:
                self.assertEqual(client.get_spreadsheet("offline").attempts, 2)
                self.assertGreaterEqual(sleep.call_args.args[0], 2)
            with patch.object(client._transport._session, "request", side_effect=lambda *a, **k: reply(503)) as send, patch("gsheet.client.transport.time.sleep") as sleep:
                with self.assertRaises(APIClientError) as caught:
                    client.get_spreadsheet("offline")
                self.assertEqual(caught.exception.code, APIErrorCode.UPSTREAM)
                self.assertEqual(send.call_count, config.retry.max_attempts)
                self.assertEqual(sleep.call_count, config.retry.max_attempts - 1)
            with patch.object(client._transport._session, "request", return_value=reply(400)) as send:
                with self.assertRaises(APIClientError) as caught:
                    client.update_values("offline", "A1", {"values": [[1]]})
                self.assertEqual((caught.exception.code, caught.exception.outcome, caught.exception.status_code),
                                 (APIErrorCode.INVALID_REQUEST, "confirmed_failure", 400))
                self.assertEqual(send.call_count, 1)
    def test_authorized_preflight_never_acquires_credentials_or_submits(self):
        config = GSheetConfig(api=APIConfig(access_mode="read_write"), environ={})
        manager = CredentialManager(config, connection_id="synthetic")
        policy = AuthorizationPolicy({"offline"}, {OperationKind.READ, OperationKind.WRITE})
        with SheetsClient(config, manager, authorization=policy) as client, \
                patch.object(manager, "get_credentials", side_effect=AssertionError("Unexpected auth")), \
                patch.object(client._transport._session, "request", side_effect=AssertionError("Unexpected network")):
            plan = client.validate_request(SheetsEndpoint.VALUES_UPDATE, "offline", range_name="A1", body={"values": [[1]]})
            self.assertEqual(plan.params["valueInputOption"], "RAW")
            self.assertEqual(plan.outcome, "validation_only")
            for endpoint, identifier, body, range_name in (
                (SheetsEndpoint.VALUES_UPDATE, "foreign", {"values": [[1]]}, "A1"),
                (SheetsEndpoint.VALUES_CLEAR, "offline", {}, "A1"),
                (SheetsEndpoint.SPREADSHEET_BATCH_UPDATE, "offline", {"requests": [{"deleteSheet": {"sheetId": 17}}]}, None),
            ):
                with self.assertRaises(APIClientError) as caught:
                    client.validate_request(endpoint, identifier, body=body, range_name=range_name)
                self.assertEqual(caught.exception.code, APIErrorCode.PERMISSION_DENIED)

    def test_non_idempotent_timeouts_are_uncertain_and_never_replayed(self):
        config = GSheetConfig(api=APIConfig(access_mode="read_write"), operations=OperationalConfig(logging_level="CRITICAL"), environ={})
        manager = CredentialManager(config, connection_id="synthetic")
        policy = AuthorizationPolicy({"offline"}, set(OperationKind), {"insertDimension", "deleteDimension", "moveDimension"})
        with SheetsClient(config, manager, authorization=policy) as client, \
                patch.object(manager, "get_credentials", return_value=Credentials("synthetic")):
            operations = [lambda: client.append_values("offline", "A1", {"values": [[1]]})]
            for name in ("insertDimension", "deleteDimension", "moveDimension"):
                operations.append(lambda name=name: client.batch_update("offline", {"requests": [{name: {}}]}))
            for operation in operations:
                with patch.object(client._transport._session, "request", side_effect=Timeout("SECRET_MARKER")) as send, \
                        patch("gsheet.client.transport.time.sleep") as sleep:
                    with self.assertRaises(APIClientError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, APIErrorCode.UNCERTAIN_WRITE)
                    self.assertEqual(caught.exception.outcome, "uncertain")
                    self.assertEqual(send.call_count, 1)
                    sleep.assert_not_called()
                    self.assertNotIn("SECRET_MARKER", str(caught.exception))
        self.assertEqual(response({}).outcome, "confirmed_success")
        self.assertEqual(APIClientError(APIErrorCode.HTTP_ERROR).outcome, "confirmed_failure")
        self.assertEqual(SheetServiceError(ServiceErrorCode.PRECONDITION_FAILED).outcome, "validation_rejection")


if __name__ == "__main__":
    unittest.main(verbosity=2)
