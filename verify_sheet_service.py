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

from gsheet import (
    APIClientError, APIConfig, APIErrorCode, APIResponse, AuthorizationPolicy, CellRange,
    CredentialManager, GSheetConfig, OperationKind, OperationalConfig, ServiceErrorCode,
    SheetService, SheetServiceError, SheetsClient, StructuralChange,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
