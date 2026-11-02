from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from urllib3.exceptions import MaxRetryError, NewConnectionError

from datev.clients.datevconnect import DATEVconnectError
from datev.connectors.base import ConnectorError
from datev.connectors.datevconnect import DATEVconnectExport, DATEVconnectMasterData, proposal_item
from datev.connectors.extf import ExtfDownloadExport, ExtfEmailExport, ExtfMasterData
from datev.tests.factories import make_booking, make_client, make_document, make_party_account
from datev.tests.test_datevconnect_client import fake_response

BASE = "https://host:58452/datev/api/accounting/v1"
CLIENT_ID = "78a11a29-2a32-4a5e-a73b-632f6aeae131"
API = "datev.connectors.datevconnect.DATEVconnect"


def dc_client(**overrides):
	values = {
		"export_connector": "DATEVconnect",
		"master_data_connector": "DATEVconnect",
		"dc_base_url": BASE,
		"dc_username": "user",
		"dc_password": "secret",
		"dc_client_id": CLIENT_ID,
	}
	values.update(overrides)
	return make_client(**values)


def make_batch(client):
	return frappe.get_doc({"doctype": "DATEV Batch", "client": client.name}).insert()


class TestProposalItem(IntegrationTestCase):
	def setUp(self):
		self.client = dc_client(account_mappings=[{"source_account": 8400, "datev_account": 4400}])
		self.party = make_party_account(self.client, account_number=10001, party_name="P" * 50)

	def test_proposal_item_outgoing(self):
		document = make_document(self.client, external_id="ab12-cd34", program_code="BEDI")
		booking = make_booking(
			document,
			self.party,
			bu_key="3",
			tax_rate=19,
			document_field_1="RE 2027#1",
			due_date="2027-02-04",
			posting_text="T" * 70,
		)
		self.assertEqual(
			proposal_item(booking, document, self.party, self.client),
			{
				"amount": 119.0,
				"date": "2027-01-05T00:00:00+01:00",
				"debitor_account_number": 100010000,
				"account_number": 44000000,
				"accounting_transaction_key": 3,
				"tax_rate": 19.0,
				"document_field1": "RE20271",
				"document_system": "BEDI",
				"document_link": "AB12-CD34",
				"due_date": "2027-02-04",
				"name": "P" * 40,
				"posting_description": "T" * 60,
				"currency_code": "EUR",
			},
		)

	def test_credit_note_sign(self):
		document = make_document(self.client)
		booking = make_booking(document, self.party, debit_credit="H")
		self.assertEqual(proposal_item(booking, document, self.party, self.client)["amount"], -119.0)

	def test_incoming_invoice_uses_creditor_and_sign(self):
		creditor = make_party_account(self.client, kind="Creditor", account_number=70001)
		document = make_document(self.client, direction="Incoming")
		booking = make_booking(
			document, creditor, kind="Incoming Invoice", contra_account=5400, debit_credit="H"
		)
		item = proposal_item(booking, document, creditor, self.client)
		self.assertEqual(item["amount"], 119.0)
		self.assertEqual(item["creditor_account_number"], 700010000)
		self.assertNotIn("debitor_account_number", item)
		booking.debit_credit = "S"
		self.assertEqual(proposal_item(booking, document, creditor, self.client)["amount"], -119.0)

	def test_no_link_fields_without_external_id(self):
		document = make_document(self.client)
		booking = make_booking(document, self.party)
		item = proposal_item(booking, document, self.party, self.client)
		for key in ("document_system", "document_link", "accounting_transaction_key", "due_date", "tax_rate"):
			self.assertNotIn(key, item)

	def test_technical_length_follows_account_length(self):
		client = dc_client(account_length=5)
		party = make_party_account(client, account_number=100001)
		document = make_document(client)
		booking = make_booking(document, party, contra_account=84000)
		item = proposal_item(booking, document, party, client)
		self.assertEqual((item["debitor_account_number"], item["account_number"]), (100001000, 84000000))


class TestDATEVconnectExport(IntegrationTestCase):
	def setUp(self):
		self.client = dc_client()
		self.debtor = make_party_account(self.client, account_number=10001)
		self.creditor = make_party_account(self.client, kind="Creditor", account_number=70001)
		self.batch = make_batch(self.client)

	def booking(self, date, kind="Outgoing Invoice"):
		party = self.debtor if kind == "Outgoing Invoice" else self.creditor
		direction = "Outgoing" if kind == "Outgoing Invoice" else "Incoming"
		document = make_document(self.client, document_date=date, direction=direction)
		return make_booking(document, party, kind=kind)

	@patch("datev.http.requests.request")
	def test_groups_by_fiscal_year_and_direction(self, request):
		request.return_value = fake_response(201, {"id": "b", "succeeded": [{"index": "0"}], "failed": []})
		bookings = [
			self.booking("2026-12-30"),
			self.booking("2027-01-05"),
			self.booking("2027-01-07", kind="Incoming Invoice"),
		]
		result = DATEVconnectExport(self.client).export(self.batch, bookings)
		urls = sorted(call.args[1] for call in request.call_args_list)
		year = BASE + f"/clients/{CLIENT_ID}/fiscal-years/"
		self.assertEqual(
			urls,
			[
				year + "20260101/posting-proposals-outgoing-invoices/batch",
				year + "20270101/posting-proposals-incoming-invoices/batch",
				year + "20270101/posting-proposals-outgoing-invoices/batch",
			],
		)
		self.assertTrue(all(item.ok for item in result.items))
		self.assertEqual([item.booking for item in result.items], [b.name for b in bookings])

	def test_two_fiscal_years_one_direction(self):
		bookings = [self.booking("2026-12-30"), self.booking("2027-01-05"), self.booking("2027-01-06")]
		calls = []

		def post(client_id, fiscal_year_id, direction, items):
			calls.append((client_id, fiscal_year_id, direction, len(items)))
			return {"succeeded": [{"index": str(i)} for i in range(len(items))], "failed": []}

		with patch(f"{API}.post_proposals", side_effect=post):
			DATEVconnectExport(self.client).export(self.batch, bookings)
		self.assertEqual(
			sorted(calls), [(CLIENT_ID, "20260101", "outgoing", 1), (CLIENT_ID, "20270101", "outgoing", 2)]
		)

	def test_partial_error_maps_to_items(self):
		bookings = [self.booking("2027-01-05"), self.booking("2027-01-06"), self.booking("2027-01-07")]
		response = {
			"id": "batch",
			"succeeded": [{"index": "2", "additional_messages": []}],
			"failed": [
				{
					"index": "0",
					"additional_messages": [
						{"id": "REW00544", "description": "Konto fehlt", "severity": "Error"},
						{"id": "X", "description": "Zweiter Grund", "severity": "Error"},
					],
				}
			],
		}
		with patch(f"{API}.post_proposals", return_value=response):
			result = DATEVconnectExport(self.client).export(self.batch, bookings)
		self.assertEqual(
			[(item.booking, item.ok) for item in result.items],
			[(bookings[0].name, False), (bookings[2].name, True)],
		)
		self.assertEqual(result.items[0].message, "REW00544: Konto fehlt; X: Zweiter Grund")
		self.assertEqual(result.uncertain, [bookings[1].name])

	def test_failed_call_marks_group(self):
		bookings = [self.booking("2026-12-30"), self.booking("2027-01-05")]

		def post(client_id, fiscal_year_id, direction, items):
			if fiscal_year_id == "20260101":
				raise DATEVconnectError("Fiscal year locked", status=409)
			return {"succeeded": [{"index": "0"}], "failed": []}

		with patch(f"{API}.post_proposals", side_effect=post):
			result = DATEVconnectExport(self.client).export(self.batch, bookings)
		self.assertEqual(
			[(i.ok, i.message) for i in result.items], [(False, "Fiscal year locked"), (True, "")]
		)

	def test_uncertain_group_does_not_stop_others(self):
		first, second, third = (self.booking(d) for d in ("2026-12-30", "2027-01-05", "2027-01-06"))
		later = self.booking("2027-01-07", kind="Incoming Invoice")
		for outcome in (requests.ReadTimeout("read timed out"), fake_response(502, None, "Bad Gateway")):

			def request(method, url, outcome=outcome, **kwargs):
				if "/20270101/posting-proposals-outgoing" in url:
					if isinstance(outcome, Exception):
						raise outcome
					return outcome
				return fake_response(201, {"succeeded": [{"index": "0"}], "failed": []})

			with self.subTest(outcome=outcome), patch("datev.http.requests.request", side_effect=request):
				result = DATEVconnectExport(self.client).export(self.batch, [first, second, third, later])
				self.assertEqual(sorted(result.uncertain), sorted([second.name, third.name]))
				self.assertEqual(
					[(item.booking, item.ok) for item in result.items],
					[(first.name, True), (later.name, True)],
				)

	@patch("datev.http.requests.request")
	def test_connection_error_marks_items_failed(self, request):
		bookings = [self.booking("2027-01-05")]
		refused = requests.ConnectionError(
			MaxRetryError(None, "/", reason=NewConnectionError(None, "refused"))
		)
		for error in (refused, requests.ConnectTimeout("connect timed out")):
			with self.subTest(error=error):
				request.side_effect = error
				result = DATEVconnectExport(self.client).export(self.batch, bookings)
				self.assertEqual([item.ok for item in result.items], [False])

	def test_unbuildable_booking_does_not_block_other_groups(self):
		broken = make_party_account(self.client, account_number=None)
		good = self.booking("2026-12-30")
		bad = make_booking(make_document(self.client, document_date="2027-01-05"), broken)
		other = self.booking("2027-01-06")
		calls = []

		def post(client_id, fiscal_year_id, direction, items):
			calls.append((fiscal_year_id, len(items)))
			return {"succeeded": [{"index": str(i)} for i in range(len(items))], "failed": []}

		with patch(f"{API}.post_proposals", side_effect=post):
			result = DATEVconnectExport(self.client).export(self.batch, [good, bad, other])
		self.assertEqual(sorted(calls), [("20260101", 1), ("20270101", 1)])
		self.assertEqual([item.ok for item in result.items], [True, False, True])
		self.assertIn("party account number", result.items[1].message)

	def test_resolves_and_caches_client_id(self):
		client = dc_client(dc_client_id=None)
		bookings = [make_booking(make_document(client), make_party_account(client, account_number=10002))]
		with (
			patch(f"{API}.client_id_for", return_value=CLIENT_ID) as lookup,
			patch(f"{API}.post_proposals", return_value={"succeeded": [{"index": "0"}]}),
		):
			DATEVconnectExport(client).export(make_batch(client), bookings)
		lookup.assert_called_once_with(55003, 29098)
		self.assertEqual(frappe.db.get_value("DATEV Client", client.name, "dc_client_id"), CLIENT_ID)

	def test_payments_fail_per_item(self):
		payment = self.booking("2027-01-05")
		payment.kind = "Payment"
		invoice = self.booking("2027-01-06")
		with patch(f"{API}.post_proposals", return_value={"succeeded": [{"index": "0"}]}) as post:
			result = DATEVconnectExport(self.client).export(self.batch, [payment, invoice])
		post.assert_called_once()
		self.assertEqual(
			[(item.booking, item.ok, item.message) for item in result.items],
			[(payment.name, False, "Payments are not supported yet"), (invoice.name, True, "")],
		)

	def test_unexpected_bodies_are_uncertain(self):
		bookings = [self.booking("2027-01-05"), self.booking("2027-01-06")]
		for body in (
			[{"index": "0"}],
			None,
			{"succeeded": [{"index": "0"}], "failed": []},
			{"succeeded": "x"},
		):
			with self.subTest(body=body), patch(f"{API}.post_proposals", return_value=body):
				result = DATEVconnectExport(self.client).export(self.batch, bookings)
				known = {item.booking for item in result.items}
				expected = (
					{bookings[0].name} if body == {"succeeded": [{"index": "0"}], "failed": []} else set()
				)
				self.assertEqual(known, expected)
				self.assertEqual(set(result.uncertain), {b.name for b in bookings} - expected)

	def test_empty_body_from_http_is_uncertain(self):
		bookings = [self.booking("2027-01-05")]
		with patch("datev.http.requests.request", return_value=fake_response(201, None, "")):
			result = DATEVconnectExport(self.client).export(self.batch, bookings)
		self.assertEqual((result.items, result.uncertain), ([], [bookings[0].name]))

	def test_exception_after_first_post(self):
		first, second, third = (self.booking(d) for d in ("2026-12-30", "2027-01-05", "2027-01-07"))
		third_kind = self.booking("2027-01-08", kind="Incoming Invoice")
		calls = []

		def post(client_id, fiscal_year_id, direction, items):
			calls.append((fiscal_year_id, direction))
			if len(calls) == 2:
				raise RuntimeError("boom")
			return {"succeeded": [{"index": str(i)} for i in range(len(items))], "failed": []}

		with patch(f"{API}.post_proposals", side_effect=post), patch("frappe.log_error"):
			result = DATEVconnectExport(self.client).export(self.batch, [first, second, third, third_kind])
		self.assertEqual(len(calls), 2)
		self.assertEqual(sorted(result.uncertain), sorted([second.name, third.name]))
		self.assertEqual(
			[(item.booking, item.ok) for item in result.items], [(first.name, True), (third_kind.name, False)]
		)
		self.assertEqual(result.items[1].message, "Not sent because an earlier request failed.")

	def test_failure_before_any_post_signals_nothing_sent(self):
		client = dc_client(dc_client_id=None)
		bookings = [make_booking(make_document(client), make_party_account(client, account_number=10002))]
		with (
			patch(f"{API}.client_id_for", side_effect=DATEVconnectError("down", retryable=True)),
			patch(f"{API}.post_proposals") as post,
		):
			with self.assertRaises(ConnectorError) as raised:
				DATEVconnectExport(client).export(make_batch(client), bookings)
		self.assertTrue(raised.exception.nothing_sent)
		post.assert_not_called()


class TestDATEVconnectMasterData(IntegrationTestCase):
	def setUp(self):
		self.client = dc_client()

	def test_fetch_parties(self):
		def parties(client_id, fiscal_year_id, kind):
			if kind == "debitors":
				return [
					{
						"account_number": 100010000,
						"caption": "Alpha GmbH",
						"eu_vat_id_country_code": "DE",
						"eu_vat_id_number": "123456789",
						"banks": [{"iban": "DE02120300000000202051"}],
					}
				]
			return [{"account_number": 700010000, "legal_person": {"legal_name": "Beta AG"}}]

		years = [{"id": "20260101", "is_locked": False}, {"id": "20270101", "is_locked": False}]
		with (
			patch(f"{API}.parties", side_effect=parties) as mocked,
			patch(f"{API}.fiscal_years", return_value=years),
		):
			result = DATEVconnectMasterData(self.client).fetch_parties()
		self.assertEqual({call.args[1] for call in mocked.call_args_list}, {"20270101"})
		self.assertEqual(
			[(p.kind, p.account_number, p.name, p.vat_id, p.iban) for p in result],
			[
				("Debtor", 10001, "Alpha GmbH", "DE123456789", "DE02120300000000202051"),
				("Creditor", 70001, "Beta AG", None, None),
			],
		)

	def test_next_number(self):
		with (
			patch(f"{API}.next_available", return_value=100020000) as mocked,
			patch(f"{API}.fiscal_years", return_value=[{"id": "20260101"}]),
		):
			self.assertEqual(DATEVconnectMasterData(self.client).next_number("Creditor"), 10002)
		self.assertEqual(mocked.call_args.args[1:3], ("20260101", "creditors"))

	def test_reads_fall_back_to_current_fiscal_year(self):
		locked = [{"id": "20280101", "is_locked": True}]
		with (
			patch(f"{API}.next_available", return_value=100020000) as mocked,
			patch(f"{API}.fiscal_years", return_value=locked),
			patch("datev.connectors.datevconnect.today", return_value="2027-03-01"),
		):
			DATEVconnectMasterData(self.client).next_number("Debtor")
		self.assertEqual(mocked.call_args.args[1], "20270101")

	def test_create_party_in_unlocked_years(self):
		party = make_party_account(self.client, account_number=10003, party_name="Gamma KG", vat_id="DE 999")
		years = [
			{"id": "20260101", "is_locked": True},
			{"id": "20270101", "is_locked": False},
			{"id": "20280101"},
		]
		with (
			patch(f"{API}.fiscal_years", return_value=years),
			patch(f"{API}.put_party", return_value={"succeeded": [{"index": "0"}], "failed": []}) as put,
		):
			DATEVconnectMasterData(self.client).create_party(party)
		self.assertEqual([call.args[1] for call in put.call_args_list], ["20270101", "20280101"])
		client_id, _, kind, payload = put.call_args.args
		self.assertEqual((client_id, kind), (CLIENT_ID, "debitors"))
		self.assertEqual(
			payload,
			[
				{
					"id": "100030000",
					"legal_entity_type": "legal_person",
					"legal_person": {"legal_name": "Gamma KG"},
					"short_name": "Gamma KG",
					"eu_vat_id_country_code": "DE",
					"eu_vat_id_number": "999",
				}
			],
		)

	def test_create_party_payload_with_iban_and_long_name(self):
		party = make_party_account(
			self.client,
			account_number=10005,
			party_name="Delta Handels GmbH",
			iban="de02 1203 0000 0000 2020 51",
		)
		with (
			patch(f"{API}.fiscal_years", return_value=[{"id": "20270101", "is_locked": False}]),
			patch(f"{API}.put_party", return_value={"succeeded": [{"index": "0"}]}) as put,
		):
			DATEVconnectMasterData(self.client).create_party(party)
		payload = put.call_args.args[3][0]
		self.assertEqual(payload["short_name"], "Delta Handels G")
		self.assertEqual(payload["banks"], [{"iban": "DE02120300000000202051", "country_code": "DE"}])
		self.assertNotIn("eu_vat_id_country_code", payload)

	def test_create_party_failure_raises(self):
		party = make_party_account(self.client, account_number=10004)
		failed = {"failed": [{"index": "0", "additional_messages": [{"id": "E1", "description": "Nope"}]}]}
		with (
			patch(f"{API}.fiscal_years", return_value=[{"id": "20270101", "is_locked": False}]),
			patch(f"{API}.put_party", return_value=failed),
		):
			with self.assertRaises(ConnectorError) as raised:
				DATEVconnectMasterData(self.client).create_party(party)
		self.assertIn("Nope", str(raised.exception))


class TestExtfConnectors(IntegrationTestCase):
	def setUp(self):
		self.client = make_client(export_connector="EXTF e-mail", extf_email="datev@example.test")
		self.new_party = make_party_account(self.client, account_number=10001, party_name="Neu GmbH")
		self.known_party = make_party_account(self.client, account_number=10002, sync_status="Imported")
		self.batch = make_batch(self.client)
		self.bookings = [
			make_booking(make_document(self.client), self.new_party),
			make_booking(make_document(self.client), self.known_party),
		]

	def attached_files(self):
		return frappe.get_all(
			"File",
			filters={"attached_to_doctype": "DATEV Batch", "attached_to_name": self.batch.name},
			fields=["file_name", "file_url", "is_private"],
			order_by="creation asc",
		)

	def test_extf_download_attaches_files(self):
		connector = ExtfDownloadExport(self.client)
		self.assertEqual(connector.final_status, "Exported")
		result = connector.export(self.batch, self.bookings)
		files = self.attached_files()
		self.assertEqual(len(files), 2)
		self.assertTrue(all(f.is_private for f in files))
		self.assertTrue(files[0].file_name.startswith("EXTF_Buchungsstapel_55003_"))
		self.assertTrue(files[1].file_name.startswith("EXTF_Debitoren_Kreditoren_55003_"))
		self.assertEqual(result.files, [f.file_url for f in files])
		self.assertEqual(frappe.db.get_value("DATEV Batch", self.batch.name, "file"), files[0].file_url)
		self.assertTrue(all(item.ok for item in result.items))
		master = frappe.get_doc("File", {"file_url": files[1].file_url}).get_content()
		master = master if isinstance(master, bytes) else master.encode("cp1252")
		self.assertIn(b"10001;", master)
		self.assertNotIn(b"10002;", master)

	def test_extf_download_without_new_parties(self):
		frappe.db.set_value("DATEV Party Account", self.new_party.name, "sync_status", "Created")
		result = ExtfDownloadExport(self.client).export(self.batch, self.bookings)
		self.assertEqual(len(result.files), 1)

	@patch("frappe.sendmail")
	def test_extf_email_sends(self, sendmail):
		result = ExtfEmailExport(self.client).export(self.batch, self.bookings)
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["datev@example.test"])
		self.assertEqual(kwargs["subject"], f"DATEV booking batch {self.batch.name}")
		names = [a["fname"] for a in kwargs["attachments"]]
		self.assertTrue(names[0].startswith("EXTF_Buchungsstapel_55003_"))
		self.assertTrue(names[1].startswith("EXTF_Debitoren_Kreditoren_55003_"))
		self.assertTrue(all(a["fcontent"] for a in kwargs["attachments"]))
		self.assertEqual(len(result.files), 2)

	@patch("frappe.sendmail", side_effect=frappe.OutgoingEmailError("down"))
	def test_extf_email_failure_leaves_batch_file_empty(self, sendmail):
		with self.assertRaises(frappe.OutgoingEmailError):
			ExtfEmailExport(self.client).export(self.batch, self.bookings)
		self.assertFalse(frappe.db.get_value("DATEV Batch", self.batch.name, "file"))

	def test_extf_master_data(self):
		connector = ExtfMasterData(self.client)
		self.assertTrue(connector.creates_via_export)
		self.assertFalse(connector.can_fetch)
		with self.assertRaises(ConnectorError):
			connector.fetch_parties()
		with self.assertRaises(ConnectorError):
			connector.next_number("Debtor")
		self.assertIsNone(connector.create_party(self.new_party))
