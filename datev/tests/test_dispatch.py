from datetime import datetime
from typing import ClassVar
from unittest.mock import call, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from datev import dispatch, intake, notify
from datev.clients.datevconnect import DATEVconnectError
from datev.connectors.base import ConnectorError, ExportConnector, ExportItemResult, ExportResult
from datev.tests.factories import make_client as make_any_client
from datev.tests.factories import make_document, make_party_account
from datev.tests.test_datevconnect_client import fake_response
from datev.tests.test_export_connectors import dc_client


class FakeExport(ExportConnector):
	final_status = "Exported"
	calls: ClassVar[list] = []
	rejected: ClassVar[set] = set()
	uncertain: ClassVar[set] = set()
	error: ClassVar[Exception | None] = None

	def export(self, batch, bookings):
		FakeExport.calls.append([booking.name for booking in bookings])
		if FakeExport.error:
			raise FakeExport.error
		return ExportResult(
			items=[
				ExportItemResult(booking.name, booking.name not in FakeExport.rejected, "rejected")
				for booking in bookings
				if booking.name not in FakeExport.uncertain
			],
			response={"accepted": len(bookings)},
			uncertain=[booking.name for booking in bookings if booking.name in FakeExport.uncertain],
		)


real_get_hooks = frappe.get_hooks


def make_client(**overrides):
	values = {"last_party_import": "[]", "last_party_import_on": frappe.utils.now_datetime()}
	return make_any_client(**{**values, **overrides})


def fake_hooks(name=None, *args, **kwargs):
	if name == "datev_export_connectors":
		return [
			{
				"EXTF download": "datev.tests.test_dispatch.FakeExport",
				"DATEVconnect": "datev.connectors.datevconnect.DATEVconnectExport",
			}
		]
	return real_get_hooks(name, *args, **kwargs)


class TestDispatch(IntegrationTestCase):
	def setUp(self):
		FakeExport.calls = []
		FakeExport.rejected = set()
		FakeExport.uncertain = set()
		FakeExport.error = None
		for patcher in (patch("frappe.get_hooks", fake_hooks), patch("frappe.db.commit")):
			patcher.start()
			self.addCleanup(patcher.stop)
		enqueue = patch("frappe.enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)
		self.client_doc = make_client(approval_required=0, dispatch_frequency="Daily")
		self.party = make_party_account(self.client_doc, preferred_number=10001)

	def document(self, client=None, **overrides):
		values = {"status": "Uploaded", "external_id": "ID-1", "program_code": "BEDI"}
		values.update(overrides)
		return make_document(client or self.client_doc, **values)

	def booking(self, document=None, line_key="1", **overrides):
		document = document or self.document()
		values = {
			"kind": "Outgoing Invoice",
			"party_account": self.party.name,
			"contra_account": 4400,
			"amount": 119,
			"debit_credit": "S",
		}
		values.update(overrides)
		return intake.add_booking(document.name, line_key, **values)

	def status(self, booking):
		return frappe.db.get_value("DATEV Booking", booking, "status")

	def test_add_booking_idempotent(self):
		document = self.document(document_number="RE-7")
		name = self.booking(document, amount=100)
		again = self.booking(document, amount=119, posting_text="Rent")
		self.assertEqual(again, name)
		booking = frappe.get_doc("DATEV Booking", name)
		self.assertEqual(
			(booking.amount, booking.posting_text, booking.client, booking.currency),
			(119, "Rent", self.client_doc.name, "EUR"),
		)
		self.assertEqual((str(booking.document_date), booking.document_field_1), ("2027-01-05", "RE-7"))
		self.assertEqual(frappe.db.count("DATEV Booking", {"document": document.name}), 1)

		booking.db_set("status", "Exported")
		self.assertEqual(self.booking(document, amount=50), name)
		self.assertEqual(frappe.db.get_value("DATEV Booking", name, "amount"), 119)

	def test_document_field_1_per_booking(self):
		document = self.document(document_number="ZE-7")
		default = self.booking(document)
		explicit = self.booking(document, "2", document_field_1="RE-2027-0042")
		self.assertEqual(frappe.db.get_value("DATEV Booking", default, "document_field_1"), "ZE-7")
		self.assertEqual(frappe.db.get_value("DATEV Booking", explicit, "document_field_1"), "RE-2027-0042")

		self.assertEqual(self.booking(document, "2", document_field_1="RE-2027-0043"), explicit)
		self.assertEqual(frappe.db.get_value("DATEV Booking", explicit, "document_field_1"), "RE-2027-0043")
		frappe.db.set_value("DATEV Booking", explicit, "status", "Transferred")
		self.assertEqual(self.booking(document, "2", document_field_1="RE-2027-0099"), explicit)
		self.assertEqual(frappe.db.get_value("DATEV Booking", explicit, "document_field_1"), "RE-2027-0043")

	def test_tax_code_maps_bu_key(self):
		client = make_client(tax_keys=[{"tax_code": "DE19", "bu_key": "3"}])
		party = make_party_account(client, preferred_number=10001)
		document = self.document(client)

		def add(line_key, **values):
			name = intake.add_booking(
				document.name,
				line_key,
				kind="Outgoing Invoice",
				party_account=party.name,
				contra_account=4400,
				amount=119,
				debit_credit="S",
				**values,
			)
			return frappe.db.get_value("DATEV Booking", name, "bu_key") or ""

		self.assertEqual(add("1", tax_code="DE19"), "3")
		self.assertEqual(add("3", tax_code="DE19", bu_key="9"), "9")
		self.assertEqual(add("4"), "")

	def tax_key_booking(self, tax_keys, tax_code):
		client = make_client(tax_keys=tax_keys)
		party = make_party_account(client, preferred_number=10001)
		return intake.add_booking(
			self.document(client).name,
			"1",
			kind="Outgoing Invoice",
			party_account=party.name,
			contra_account=4400,
			amount=119,
			debit_credit="S",
			tax_code=tax_code,
		)

	def test_tax_key_prefers_direction(self):
		client = make_client(
			tax_keys=[
				{"tax_code": "VAT19", "direction": "Incoming", "bu_key": "9"},
				{"tax_code": "VAT19", "direction": "Outgoing", "bu_key": ""},
				{"tax_code": "VAT7", "bu_key": "8"},
				{"tax_code": "VAT7", "direction": "Incoming", "bu_key": "2"},
				{"tax_code": "OUT", "direction": "Outgoing", "bu_key": "3"},
			]
		).name
		self.assertEqual(intake.tax_key(client, "VAT19", "Incoming"), "9")
		self.assertEqual(intake.tax_key(client, "VAT19", "Outgoing"), "")
		self.assertIsNone(intake.tax_key(client, "VAT19", "Any"))
		self.assertEqual(intake.tax_key(client, "VAT7", "Outgoing"), "8")
		self.assertEqual(intake.tax_key(client, "VAT7", "Incoming"), "2")
		self.assertEqual(intake.tax_key(client, "VAT7", "Any"), "8")
		self.assertIsNone(intake.tax_key(client, "OUT", "Incoming"))
		self.assertIsNone(intake.tax_key(client, "MISSING", "Outgoing"))

	def test_booking_kind_selects_tax_key_direction(self):
		client = make_client(
			tax_keys=[
				{"tax_code": "VAT19", "direction": "Outgoing", "bu_key": ""},
				{"tax_code": "VAT19", "direction": "Incoming", "bu_key": "9"},
			]
		)
		debtor = make_party_account(client, preferred_number=10001)
		creditor = make_party_account(client, "Creditor", preferred_number=70001)
		document = self.document(client)

		def add(line_key, kind, **values):
			name = intake.add_booking(
				document.name,
				line_key,
				kind=kind,
				contra_account=4400,
				amount=119,
				debit_credit="S",
				tax_code="VAT19",
				**values,
			)
			return frappe.db.get_value("DATEV Booking", name, "bu_key") or ""

		self.assertEqual(add("1", "Outgoing Invoice", party_account=debtor.name), "")
		self.assertEqual(add("2", "Incoming Invoice", party_account=creditor.name), "9")
		with self.assertRaises(frappe.ValidationError):
			add("3", "Payment", account=1200)

	def test_missing_tax_key_blocks(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self.tax_key_booking([{"tax_code": "DE19", "bu_key": "3"}], "DE7")
		self.assertIn("DE7", str(raised.exception))

	def test_empty_bu_key_row_allowed(self):
		name = self.tax_key_booking([{"tax_code": "DE19", "bu_key": ""}], "DE19")
		self.assertEqual(frappe.db.get_value("DATEV Booking", name, "bu_key") or "", "")

	def test_preflight_requires_link_when_strict(self):
		client = make_client(require_document_link=1)
		party = make_party_account(client, preferred_number=10001)

		def check(**document_values):
			document = self.document(client, **document_values)
			name = intake.add_booking(
				document.name,
				"1",
				kind="Outgoing Invoice",
				party_account=party.name,
				contra_account=4400,
				amount=119,
				debit_credit="S",
			)
			return dispatch.preflight(frappe.get_doc("DATEV Booking", name))

		self.assertTrue(check(status="No Link", external_id=None))
		self.assertTrue(check(status="Uploaded", external_id=None))
		self.assertTrue(check(status="Pending", external_id=None))
		self.assertIsNone(check())

	def test_preflight_allows_no_link_when_lenient(self):
		client = make_client(require_document_link=0)
		party = make_party_account(client, preferred_number=10001)
		blocked = make_party_account(client, sync_status="To Clarify")

		def check(party_account=party, document_number="RE-1", **document_values):
			document = self.document(client, document_number=document_number, **document_values)
			name = intake.add_booking(
				document.name,
				"1",
				kind="Outgoing Invoice",
				party_account=party_account.name,
				contra_account=4400,
				amount=119,
				debit_credit="S",
			)
			return dispatch.preflight(frappe.get_doc("DATEV Booking", name))

		self.assertIsNone(check(status="No Link", external_id=None))
		self.assertTrue(check(status="Stuck"))
		self.assertTrue(check(status="Pending"))
		self.assertTrue(check(party_account=blocked))
		self.assertTrue(check(document_number=""))

	def test_payment_ignores_link_requirement(self):
		client = make_client(require_document_link=1)
		party = make_party_account(client, preferred_number=10001)

		def check(kind="Payment", status="Not Required", **values):
			document = self.document(client, status=status, external_id=None, direction="Other")
			name = intake.add_booking(
				document.name,
				"1",
				kind=kind,
				contra_account=1800,
				amount=50,
				debit_credit="H",
				**(values or {"account": 6855}),
			)
			return dispatch.preflight(frappe.get_doc("DATEV Booking", name))

		self.assertIsNone(check())
		self.assertIsNone(check(party_account=party.name))
		self.assertIsNone(check(status="No Link"))
		self.assertTrue(check(status="Pending"))
		self.assertTrue(check(kind="Outgoing Invoice", status="No Link", party_account=party.name))

	def test_collect_creates_batch_awaiting_approval(self):
		self.client_doc.db_set("approval_required", 1)
		ready = self.booking()
		waiting = self.booking(self.document(status="Pending", external_id=None))

		batch = dispatch.collect(self.client_doc.name)

		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Awaiting Approval")
		self.assertEqual(frappe.db.get_value("DATEV Booking", ready, ["status", "batch"]), ("Batched", batch))
		stuck = frappe.db.get_value("DATEV Booking", waiting, ["status", "batch", "message"], as_dict=True)
		self.assertEqual((stuck.status, stuck.batch), ("Stuck", None))
		self.assertTrue(stuck.message)
		self.assertTrue(frappe.db.get_value("DATEV Client", self.client_doc.name, "last_dispatch_on"))

		dispatch.send(batch)
		self.assertEqual(FakeExport.calls, [])

		dispatch.approve(batch)
		self.enqueue.assert_called_once_with(
			"datev.dispatch.send", **send_job(batch), enqueue_after_commit=True, batch=batch
		)
		self.assertEqual(FakeExport.calls, [])
		dispatch.send(batch)
		approved = frappe.get_doc("DATEV Batch", batch)
		self.assertEqual((approved.status, approved.approved_by), ("Sent", frappe.session.user))
		self.assertTrue(approved.approved_on)
		self.assertEqual(FakeExport.calls, [[ready]])

	def test_run_enqueues_per_client(self):
		booking = self.booking()
		self.client_doc.db_set("approval_required", 1)
		approved = dispatch.collect(self.client_doc.name)
		frappe.db.set_value("DATEV Batch", approved, "status", "Approved")
		self.client_doc.db_set("last_dispatch_on", None)
		with patch("frappe.get_all", side_effect=only_own_client(self.client_doc.name)):
			dispatch.run()
		self.assertEqual(FakeExport.calls, [])
		self.assertEqual(self.status(booking), "Batched")
		self.assertEqual(
			self.enqueue.call_args_list,
			[
				call(
					"datev.dispatch.process_client",
					**client_job(self.client_doc.name),
					client=self.client_doc.name,
				),
				call("datev.dispatch.send", **send_job(approved), batch=approved),
			],
		)

	def test_process_client_collects_and_sends(self):
		booking = self.booking()
		dispatch.process_client(self.client_doc.name)

		batch = frappe.db.get_value("DATEV Booking", booking, "batch")
		self.assertEqual(self.status(booking), "Exported")
		batch_doc = frappe.get_doc("DATEV Batch", batch)
		self.assertEqual(batch_doc.status, "Sent")
		self.assertTrue(batch_doc.sent_on)
		self.assertEqual(
			frappe.db.get_value("DATEV Party Account", self.party.name, "sync_status"), "Created"
		)
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": batch, "event": "sent"}))

		dispatch.process_client(self.client_doc.name)
		self.assertEqual(len(FakeExport.calls), 1)

	def test_is_due_weekly_and_monthly(self):
		client = frappe.get_doc(
			{"doctype": "DATEV Client", "dispatch_frequency": "Weekly", "dispatch_weekday": "Monday"}
		)
		monday = datetime(2027, 1, 4, 9)
		self.assertTrue(dispatch.is_due(client, monday))
		self.assertFalse(dispatch.is_due(client, datetime(2027, 1, 5, 9)))
		client.last_dispatch_on = datetime(2027, 1, 4, 1)
		self.assertFalse(dispatch.is_due(client, monday))
		client.last_dispatch_on = datetime(2026, 12, 28, 1)
		self.assertTrue(dispatch.is_due(client, monday))

		client.update({"dispatch_frequency": "Monthly", "dispatch_day_of_month": 10})
		client.last_dispatch_on = datetime(2026, 12, 10, 1)
		self.assertFalse(dispatch.is_due(client, datetime(2027, 1, 9, 9)))
		self.assertTrue(dispatch.is_due(client, datetime(2027, 1, 12, 9)))
		client.last_dispatch_on = datetime(2027, 1, 10, 1)
		self.assertFalse(dispatch.is_due(client, datetime(2027, 1, 28, 9)))

		client.update({"dispatch_frequency": "Daily", "last_dispatch_on": datetime(2027, 1, 4, 1)})
		self.assertFalse(dispatch.is_due(client, datetime(2027, 1, 4, 23)))
		self.assertTrue(dispatch.is_due(client, datetime(2027, 1, 5, 0)))

	def test_hold_moves_booking_out_of_batch(self):
		self.client_doc.db_set("approval_required", 1)
		held = self.booking()
		kept = self.booking()
		batch = dispatch.collect(self.client_doc.name)

		dispatch.hold(held)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", held, ["status", "held", "batch"]), ("Pending", 1, None)
		)
		self.assertEqual(frappe.db.get_value("DATEV Booking", kept, "batch"), batch)
		self.assertIsNone(dispatch.collect(self.client_doc.name))

		dispatch.hold(held, held=False)
		second = dispatch.collect(self.client_doc.name)
		self.assertEqual(frappe.db.get_value("DATEV Booking", held, "batch"), second)

		dispatch.approve(batch)
		dispatch.send(batch)
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(kept)
		self.assertEqual(self.status(kept), "Exported")

	def test_hold_removes_empty_batch(self):
		self.client_doc.db_set("approval_required", 1)
		booking = self.booking()
		batch = dispatch.collect(self.client_doc.name)
		dispatch.hold(booking)
		self.assertFalse(frappe.db.exists("DATEV Batch", batch))

	def test_collect_skips_bookings_batched_meanwhile(self):
		first = self.booking()
		second = self.booking()

		def preflight(booking):
			if booking.name == second:
				frappe.db.set_value("DATEV Booking", first, {"status": "Batched", "batch": "OTHER"})
			return None

		with patch("datev.dispatch.preflight", side_effect=preflight):
			batch = dispatch.collect(self.client_doc.name)
		self.assertEqual(frappe.db.get_value("DATEV Booking", first, "batch"), "OTHER")
		self.assertEqual(frappe.db.get_value("DATEV Booking", second, "batch"), batch)

	def test_disabled_client_is_not_collected(self):
		self.booking()
		self.client_doc.db_set("enabled", 0)
		self.assertIsNone(dispatch.collect(self.client_doc.name))

	def test_send_twice_sends_once(self):
		self.booking()
		batch = dispatch.collect(self.client_doc.name)
		dispatch.send(batch)
		dispatch.send(batch)
		self.assertEqual(len(FakeExport.calls), 1)

	def test_send_skips_batch_left_sending(self):
		self.booking()
		batch = dispatch.collect(self.client_doc.name)
		frappe.db.set_value("DATEV Batch", batch, "status", "Sending")
		dispatch.send(batch)
		self.assertEqual(FakeExport.calls, [])

	def test_partial_failure_resends_only_failed(self):
		rejected = self.booking()
		accepted = self.booking()
		FakeExport.rejected = {rejected}
		first = dispatch.collect(self.client_doc.name)
		dispatch.send(first)

		self.assertEqual(frappe.db.get_value("DATEV Batch", first, "status"), "Partially Sent")
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", rejected, ["status", "batch", "message"]),
			("Failed", None, "rejected"),
		)
		self.assertEqual(self.status(accepted), "Exported")

		FakeExport.rejected = set()
		second = dispatch.collect(self.client_doc.name)
		dispatch.send(second)
		self.assertEqual(FakeExport.calls[1], [rejected])
		self.assertEqual(self.status(rejected), "Exported")
		self.assertEqual(frappe.db.get_value("DATEV Batch", second, "status"), "Sent")

	def test_all_rejected_fails_batch(self):
		booking = self.booking()
		FakeExport.rejected = {booking}
		batch = dispatch.collect(self.client_doc.name)
		dispatch.send(batch)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Failed")
		self.assertEqual(frappe.db.get_value("DATEV Party Account", self.party.name, "sync_status"), "New")

	def test_exported_bookings_never_recollected(self):
		document = self.document()
		booking = self.booking(document)
		dispatch.send(dispatch.collect(self.client_doc.name))
		self.assertEqual(self.status(booking), "Exported")

		self.assertEqual(self.booking(document, amount=200), booking)
		self.assertIsNone(dispatch.collect(self.client_doc.name))
		self.assertEqual(len(FakeExport.calls), 1)
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(booking)

	def test_connector_exception_resets_bookings(self):
		booking = self.booking()
		FakeExport.error = ConnectorError("DATEV unavailable", response={"error": "down"}, nothing_sent=True)
		batch = dispatch.collect(self.client_doc.name)
		with patch("frappe.db.rollback"):
			dispatch.send(batch)

		self.assertEqual(
			frappe.db.get_value("DATEV Batch", batch, ["status", "message"]), ("Failed", "DATEV unavailable")
		)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", booking, ["status", "batch"]), ("Pending", None)
		)
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": batch, "event": "failed"}))

		FakeExport.error = None
		dispatch.send(dispatch.collect(self.client_doc.name))
		self.assertEqual(self.status(booking), "Exported")

	def test_connector_exception_after_sending_keeps_batch_sending(self):
		booking = self.booking()
		batch = dispatch.collect(self.client_doc.name)
		for error in (ConnectorError("lost"), RuntimeError("crash")):
			with self.subTest(error=error):
				frappe.db.set_value("DATEV Batch", batch, "status", "Approved")
				FakeExport.error = error
				with patch("frappe.db.rollback"), patch("frappe.log_error"):
					dispatch.send(batch)
				status, message = frappe.db.get_value("DATEV Batch", batch, ["status", "message"])
				self.assertEqual(status, "Sending")
				self.assertIn("uncertain", message)
				self.assertIn(str(error), message)
				self.assertEqual(
					frappe.db.get_value("DATEV Booking", booking, ["status", "batch"]), ("Batched", batch)
				)
		FakeExport.error = None
		self.assertIsNone(dispatch.collect(self.client_doc.name))

	def test_send_uses_connector_recorded_on_batch(self):
		self.booking()
		batch = dispatch.collect(self.client_doc.name)
		self.client_doc.db_set("export_connector", "DATEVconnect")
		dispatch.send(batch)
		self.assertEqual(len(FakeExport.calls), 1)

	def test_send_now_enqueues(self):
		self.client_doc.db_set({"dispatch_frequency": "Monthly", "last_dispatch_on": frappe.utils.now()})
		booking = self.booking()
		dispatch.send_now(self.client_doc.name)
		self.assertEqual(self.status(booking), "Pending")
		self.assertEqual(FakeExport.calls, [])
		self.enqueue.assert_called_once_with(
			"datev.dispatch.process_client",
			**client_job(self.client_doc.name),
			enqueue_after_commit=True,
			client=self.client_doc.name,
		)
		dispatch.process_client(self.client_doc.name)
		self.assertEqual(self.status(booking), "Exported")

	def test_document_all_or_nothing(self):
		blocked = make_party_account(self.client_doc, sync_status="To Clarify")
		document = self.document()
		ready = self.booking(document, "1")
		stuck = self.booking(document, "2", party_account=blocked.name)
		other = self.booking()

		batch = dispatch.collect(self.client_doc.name)

		self.assertEqual(frappe.db.get_value("DATEV Booking", other, "batch"), batch)
		self.assertEqual(frappe.db.get_value("DATEV Booking", stuck, ["status", "batch"]), ("Stuck", None))
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", ready, ["status", "batch", "message"]),
			("Pending", None, "Waiting for line 2 of this document"),
		)

	def test_document_waits_for_held_line(self):
		document = self.document()
		held = self.booking(document, "1")
		dispatch.hold(held)
		waiting = self.booking(document, "2")
		self.assertIsNone(dispatch.collect(self.client_doc.name))
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", waiting, ["status", "message"]),
			("Pending", "Waiting for line 1 of this document"),
		)

	def test_hold_holds_whole_document(self):
		self.client_doc.db_set("approval_required", 1)
		document = self.document()
		first = self.booking(document, "1")
		second = self.booking(document, "2")
		other = self.booking()
		batch = dispatch.collect(self.client_doc.name)

		dispatch.hold(first)
		for name in (first, second):
			self.assertEqual(
				frappe.db.get_value("DATEV Booking", name, ["status", "held", "batch"]), ("Pending", 1, None)
			)
		self.assertEqual(frappe.db.get_value("DATEV Booking", other, "batch"), batch)

		dispatch.hold(second, held=False)
		self.assertEqual(
			[frappe.db.get_value("DATEV Booking", name, "held") for name in (first, second)], [0, 0]
		)

	def test_hold_rejects_document_in_sent_batch(self):
		document = self.document()
		sent = self.booking(document, "1")
		batch = dispatch.collect(self.client_doc.name)
		frappe.db.set_value("DATEV Batch", batch, "status", "Sending")
		pending = self.booking(document, "2")
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(pending)
		self.assertEqual(frappe.db.get_value("DATEV Booking", pending, "held"), 0)
		self.assertEqual(frappe.db.get_value("DATEV Booking", sent, "batch"), batch)

	def test_post_only(self):
		from datev import documents, parties
		from datev.datev.doctype.datev_client.datev_client import DATEVClient

		for function in (
			dispatch.send_now,
			dispatch.approve,
			dispatch.hold,
			dispatch.resolve_batch,
			dispatch.reset_booking,
			dispatch.confirm_import,
			dispatch.resend_extf_email,
			documents.retry,
			documents.mark_uploaded,
			parties.retry_party,
			parties.mark_resolved,
			DATEVClient.import_parties,
			DATEVClient.import_parties_from_file,
			DATEVClient.refresh_document_types,
		):
			with self.subTest(function=function.__qualname__):
				self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])

	def test_approve_requires_permission(self):
		self.client_doc.db_set({"approval_required": 1, "responsible_user": "approver@example.com"})
		self.booking()
		batch = dispatch.collect(self.client_doc.name)
		with patch("frappe.get_roles", return_value=["Guest"]):
			with self.assertRaises(frappe.PermissionError):
				dispatch.approve(batch)
			with patch.dict(frappe.session, {"user": "approver@example.com"}):
				dispatch.approve(batch)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Approved")

	def uncertain_batch(self):
		booking = self.booking()
		FakeExport.uncertain = {booking}
		batch = dispatch.collect(self.client_doc.name)
		dispatch.send(batch)
		FakeExport.uncertain = set()
		return batch, booking

	def test_uncertain_delivery_keeps_batch_sending(self):
		batch, booking = self.uncertain_batch()
		self.assertEqual(
			frappe.db.get_value("DATEV Batch", batch, ["status", "message"]),
			("Sending", "Delivery to DATEV is uncertain. Check DATEV, then resolve the batch."),
		)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", booking, ["status", "batch"]), ("Batched", batch)
		)
		self.assertTrue(
			frappe.db.exists("DATEV Event Log", {"reference_name": batch, "event": "delivery_uncertain"})
		)

		dispatch.send(batch)
		with patch("frappe.get_all", side_effect=only_own_client(self.client_doc.name)):
			dispatch.run()
		self.assertEqual(len(FakeExport.calls), 1)
		self.assertEqual(self.status(booking), "Batched")

	def test_resolve_batch_delivered(self):
		batch, booking = self.uncertain_batch()
		dispatch.resolve_batch(batch, delivered=True)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Sent")
		self.assertEqual(self.status(booking), "Exported")
		self.assertEqual(
			frappe.db.get_value("DATEV Party Account", self.party.name, "sync_status"), "Created"
		)
		self.assertIsNone(dispatch.collect(self.client_doc.name))
		with self.assertRaises(frappe.ValidationError):
			dispatch.resolve_batch(batch, delivered=False)

	def test_resolve_delivered_keeps_failures_visible(self):
		failed = self.booking()
		unsure = self.booking()
		FakeExport.rejected = {failed}
		FakeExport.uncertain = {unsure}
		batch = dispatch.collect(self.client_doc.name)
		dispatch.send(batch)
		self.assertEqual(
			frappe.db.get_value("DATEV Batch", batch, ["status", "failed_count"]), ("Sending", 1)
		)
		dispatch.resolve_batch(batch, delivered=True)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Partially Sent")
		self.assertEqual((self.status(failed), self.status(unsure)), ("Failed", "Exported"))

	def test_resolve_batch_not_delivered(self):
		batch, booking = self.uncertain_batch()
		dispatch.resolve_batch(batch, delivered=False)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Failed")
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", booking, ["status", "batch"]), ("Pending", None)
		)

		dispatch.send(dispatch.collect(self.client_doc.name))
		self.assertEqual(len(FakeExport.calls), 2)
		self.assertEqual(self.status(booking), "Exported")

	def test_resolve_uses_connector_recorded_on_batch(self):
		batch, booking = self.uncertain_batch()
		self.client_doc.db_set("export_connector", "DATEVconnect")
		dispatch.resolve_batch(batch, delivered=True)
		self.assertEqual(self.status(booking), "Exported")

	def test_resolve_batch_requires_manager(self):
		batch, _booking = self.uncertain_batch()
		with patch("frappe.only_for", side_effect=frappe.PermissionError) as only_for:
			with self.assertRaises(frappe.PermissionError):
				dispatch.resolve_batch(batch, delivered=True)
		only_for.assert_called_once_with("DATEV Manager")

	def test_send_empty_batch_marks_sent(self):
		booking = self.booking()
		batch = dispatch.collect(self.client_doc.name)
		frappe.db.set_value("DATEV Booking", booking, {"status": "Pending", "batch": None})
		dispatch.send(batch)
		self.assertEqual(
			frappe.db.get_value("DATEV Batch", batch, ["status", "message"]),
			("Sent", "No bookings left to send."),
		)
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": batch, "event": "sent"}))
		self.assertEqual(FakeExport.calls, [])

	def test_approve_missing_batch_is_quiet(self):
		self.assertIsNone(dispatch.approve("DATEV-BATCH-MISSING"))

	def test_discard_removes_open_bookings(self):
		document = self.document(status="Pending", external_id=None)
		stuck = self.booking(document)
		failed = self.booking(document, line_key="2")
		frappe.db.set_value("DATEV Booking", stuck, "status", "Stuck")
		frappe.db.set_value("DATEV Booking", failed, "status", "Failed")
		intake.discard_document(document.source_key)
		self.assertFalse(frappe.db.exists("DATEV Booking", {"document": document.name}))

	def payment(self, depends_on, line_key="1", document=None, **values):
		document = document or self.document(status="Not Required", external_id=None, direction="Other")
		return self.booking(
			document,
			line_key,
			kind="Payment",
			contra_account=1800,
			debit_credit="H",
			depends_on_document=depends_on.name if depends_on else None,
			**values,
		)

	def test_dependency_waits(self):
		invoice = self.document()
		self.booking(invoice)
		document = self.document(status="Not Required", external_id=None, direction="Other")
		payment = self.payment(invoice, document=document)
		fee = self.payment(None, "2", document=document, party_account=None, account=6855)

		batch = dispatch.collect(self.client_doc.name)

		self.assertTrue(batch)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", payment, ["status", "batch", "message"]),
			("Pending", None, f"Waiting for document {invoice.name} to reach DATEV"),
		)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", fee, ["status", "batch", "message"]),
			("Pending", None, "Waiting for line 1 of this document"),
		)

	def test_dependency_released_after_transfer(self):
		invoice = self.document()
		self.booking(invoice)
		payment = self.payment(invoice)
		self.enqueue.reset_mock()
		dispatch.send(dispatch.collect(self.client_doc.name))
		self.assertEqual(self.status(payment), "Pending")
		self.enqueue.assert_called_once_with(
			"datev.dispatch.process_client",
			**client_job(self.client_doc.name),
			enqueue_after_commit=True,
			client=self.client_doc.name,
		)

		batch = dispatch.collect(self.client_doc.name)
		self.assertEqual(frappe.db.get_value("DATEV Booking", payment, "batch"), batch)
		dispatch.send(batch)
		self.assertEqual(self.status(payment), "Exported")

	def test_process_client_sends_released_payments_at_once(self):
		invoice = self.document()
		invoice_line = self.booking(invoice)
		payment = self.payment(invoice)
		dispatch.process_client(self.client_doc.name)
		self.assertEqual((self.status(invoice_line), self.status(payment)), ("Exported", "Exported"))
		self.assertEqual(len(FakeExport.calls), 2)

	def test_no_release_without_dependents(self):
		self.booking()
		self.enqueue.reset_mock()
		dispatch.send(dispatch.collect(self.client_doc.name))
		self.enqueue.assert_not_called()

	def test_waiting_payment_not_overdue(self):
		invoice = self.document(status="Stuck", external_id=None)
		payment = self.payment(invoice)
		self.client_doc.db_set("last_dispatch_on", frappe.utils.add_days(frappe.utils.now_datetime(), -5))
		self.assertTrue(dispatch.is_overdue(self.client_doc, frappe.utils.now_datetime()))
		dispatch.collect(self.client_doc.name)
		self.assertEqual(frappe.db.get_value("DATEV Booking", payment, "waiting"), 1)
		self.client_doc.db_set("last_dispatch_on", frappe.utils.add_days(frappe.utils.now_datetime(), -5))
		self.assertFalse(dispatch.is_overdue(self.client_doc, frappe.utils.now_datetime()))
		counts = notify.status_counts(self.client_doc.name, frappe.utils.now_datetime())
		self.assertEqual(counts["waiting_bookings"], 1)

	def test_account_length_checked_for_every_export(self):
		booking = self.booking(contra_account=44000)
		dispatch.collect(self.client_doc.name)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", booking, ["status", "message"]),
			("Stuck", "Account 44000 has more digits than the client's account length 4."),
		)

	def test_dependency_on_discarded_waits_with_message(self):
		for status in ("Discarded", "Stuck"):
			with self.subTest(status=status):
				invoice = self.document(status=status, external_id=None)
				payment = self.payment(invoice)
				self.assertIsNone(dispatch.collect(self.client_doc.name))
				self.assertEqual(
					frappe.db.get_value("DATEV Booking", payment, ["status", "message"]),
					(
						"Pending",
						f"Waiting for document {invoice.name} to reach DATEV (document status {status})",
					),
				)
				frappe.delete_doc("DATEV Booking", payment)

	def test_reversal_lines_once(self):
		document = self.document(status="Not Required", external_id=None, direction="Other")
		original = self.payment(None, document=document)
		dispatch.send(dispatch.collect(self.client_doc.name))
		self.assertEqual(self.status(original), "Exported")

		reversal = self.payment(None, "storno:1", document=document, general_reversal=1)
		self.assertNotEqual(reversal, original)
		self.assertEqual(self.payment(None, "storno:1", document=document, general_reversal=1), reversal)
		batch = dispatch.collect(self.client_doc.name)
		self.assertEqual(frappe.get_all("DATEV Booking", {"batch": batch}, pluck="name"), [reversal])
		dispatch.send(batch)
		self.assertEqual(self.payment(None, "storno:1", document=document, general_reversal=1), reversal)
		self.assertEqual(frappe.db.count("DATEV Booking", {"document": document.name}), 2)
		self.assertEqual(frappe.db.get_value("DATEV Booking", reversal, "general_reversal"), 1)
		self.assertEqual(FakeExport.calls, [[original], [reversal]])
		self.assertIsNone(dispatch.collect(self.client_doc.name))

	def test_discard_open_bookings_blocks_batched(self):
		self.client_doc.db_set("approval_required", 1)
		document = self.document(status="Not Required", external_id=None, direction="Other")
		sent = self.payment(None, document=document)
		first = dispatch.collect(self.client_doc.name)
		dispatch.approve(first)
		dispatch.send(first)
		self.assertEqual(self.status(sent), "Exported")
		awaiting = self.payment(None, "2", document=document)
		batch = dispatch.collect(self.client_doc.name)
		pending = self.payment(None, "3", document=document)

		with self.assertRaises(frappe.ValidationError):
			intake.discard_document(document.source_key)

		frappe.db.set_value("DATEV Batch", batch, "status", "Sending")
		with self.assertRaises(frappe.ValidationError):
			intake.discard_open_bookings(document.source_key)
		self.assertTrue(frappe.db.exists("DATEV Booking", pending))

		frappe.db.set_value("DATEV Batch", batch, "status", "Awaiting Approval")
		intake.discard_open_bookings(document.source_key)
		self.assertEqual(frappe.get_all("DATEV Booking", {"document": document.name}, pluck="name"), [sent])
		self.assertFalse(frappe.db.exists("DATEV Batch", batch))
		self.assertFalse(frappe.db.exists("DATEV Booking", awaiting))
		self.assertNotEqual(frappe.db.get_value("DATEV Document", document.name, "status"), "Discarded")

	def test_hold_rejects_stuck_booking(self):
		booking = self.booking(self.document(status="Pending", external_id=None))
		dispatch.collect(self.client_doc.name)
		self.assertEqual(self.status(booking), "Stuck")
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(booking)


DC_API = "datev.connectors.datevconnect.DATEVconnect"
OPEN_YEARS = [{"id": "20260101", "is_locked": False}, {"id": "20270101", "is_locked": False}]


class TestDATEVconnectPreflight(IntegrationTestCase):
	def setUp(self):
		for patcher in (patch("frappe.get_hooks", fake_hooks), patch("frappe.db.commit")):
			patcher.start()
			self.addCleanup(patcher.stop)
		self.client_doc = dc_client(approval_required=0)
		self.party = make_party_account(self.client_doc, account_number=10001, sync_status="Imported")

	def add(self, date, line_key="1", **values):
		document = make_document(
			self.client_doc, document_date=date, status="Uploaded", external_id="ID-1", program_code="BEDI"
		)
		values = {
			"kind": "Outgoing Invoice",
			"party_account": self.party.name,
			"contra_account": 4400,
			"amount": 119,
			"debit_credit": "S",
			**values,
		}
		return intake.add_booking(document.name, line_key, **values)

	def message(self, booking):
		return frappe.db.get_value("DATEV Booking", booking, ["status", "message"])

	def test_fiscal_year_must_exist_and_be_open(self):
		open_year = self.add("2027-01-05")
		locked = self.add("2026-12-30")
		missing = self.add("2028-01-05")
		years = [{"id": "20260101", "is_locked": True}, {"id": "20270101"}]
		with patch(f"{DC_API}.fiscal_years", return_value=years) as fiscal_years:
			batch = dispatch.collect(self.client_doc.name)
		fiscal_years.assert_called_once()
		self.assertEqual(frappe.db.get_value("DATEV Booking", open_year, "batch"), batch)
		self.assertEqual(self.message(locked), ("Stuck", "Fiscal year 20260101 is locked in DATEV."))
		self.assertEqual(self.message(missing), ("Stuck", "Fiscal year 20280101 does not exist in DATEV."))

	def test_unreadable_fiscal_years_mark_stuck(self):
		booking = self.add("2027-01-05")
		with patch(f"{DC_API}.fiscal_years", side_effect=DATEVconnectError("down", retryable=True)):
			self.assertIsNone(dispatch.collect(self.client_doc.name))
		status, message = self.message(booking)
		self.assertEqual(status, "Stuck")
		self.assertIn("down", message)

	def test_payments_checked_strictly_for_datevconnect(self):
		document = make_document(
			self.client_doc, document_date="2027-01-05", status="Not Required", direction="Other"
		)

		def add(line_key, **values):
			values = {"kind": "Payment", "contra_account": 1800, "amount": 10, "debit_credit": "S", **values}
			if "party_account" not in values:
				values["account"] = values.get("account", 6855)
			return intake.add_booking(document.name, line_key, **values)

		ready = add("1")
		party = add("2", party_account=self.party.name)
		too_long = add("3", account=68550)
		contra_too_long = add("4", contra_account=18000)
		bad_key = add("5", bu_key="X")
		invoice_reversal = self.add("2027-01-05", general_reversal=1)
		with patch(f"{DC_API}.fiscal_years", return_value=OPEN_YEARS):
			dispatch.collect(self.client_doc.name)
		self.assertEqual(self.message(ready), ("Pending", "Waiting for line 3 of this document"))
		self.assertEqual(self.message(party), ("Pending", "Waiting for line 3 of this document"))
		self.assertEqual(
			self.message(too_long),
			("Stuck", "Account 68550 has more digits than the client's account length 4."),
		)
		self.assertEqual(
			self.message(contra_too_long),
			("Stuck", "Account 18000 has more digits than the client's account length 4."),
		)
		self.assertEqual(self.message(bad_key), ("Stuck", "The BU key X is not a number."))
		self.assertEqual(
			self.message(invoice_reversal),
			(
				"Stuck",
				"DATEVconnect accepts general reversals only for payments. Send a cancellation invoice instead.",
			),
		)


class TestDATEVconnectPayments(IntegrationTestCase):
	def setUp(self):
		for patcher in (patch("frappe.get_hooks", fake_hooks), patch("frappe.db.commit")):
			patcher.start()
			self.addCleanup(patcher.stop)
		self.client_doc = dc_client(approval_required=0)
		document = make_document(
			self.client_doc, document_date="2027-01-05", status="Not Required", direction="Other"
		)
		self.bookings = [
			intake.add_booking(
				document.name,
				str(line),
				kind="Payment",
				account=6855,
				contra_account=1800,
				amount=10,
				debit_credit="S",
			)
			for line in (1, 2)
		]
		with patch(f"{DC_API}.fiscal_years", return_value=OPEN_YEARS):
			self.batch = dispatch.collect(self.client_doc.name)

	def states(self):
		return [frappe.db.get_value("DATEV Booking", name, ["status", "batch"]) for name in self.bookings]

	def test_rejected_sequence_fails_all_its_bookings(self):
		body = {"error": "DCO01001", "error_description": "Invalid accounting sequence"}
		with patch("datev.http.requests.request", return_value=fake_response(400, body, "raw")):
			dispatch.send(self.batch)
		self.assertEqual(self.states(), [("Failed", None), ("Failed", None)])
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", self.bookings[0], "message"),
			"DCO01001: Invalid accounting sequence",
		)
		self.assertEqual(frappe.db.get_value("DATEV Batch", self.batch, "status"), "Failed")

	def test_timed_out_sequence_stays_uncertain(self):
		with patch("datev.http.requests.request", side_effect=requests.ReadTimeout("read timed out")):
			dispatch.send(self.batch)
		self.assertEqual(self.states(), [("Batched", self.batch), ("Batched", self.batch)])
		self.assertEqual(frappe.db.get_value("DATEV Batch", self.batch, "status"), "Sending")

	def test_accepted_sequence_transfers(self):
		body = {"additional_messages": [{"id": "REW9", "description": "Sequence queued"}]}
		with patch("datev.http.requests.request", return_value=fake_response(200, body, "")):
			dispatch.send(self.batch)
		self.assertEqual(self.states(), [("Transferred", self.batch), ("Transferred", self.batch)])
		self.assertEqual(
			frappe.db.get_value("DATEV Batch", self.batch, ["status", "message"]),
			("Sent", "2 sent, 0 failed.\nREW9: Sequence queued"),
		)
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", self.bookings[0], "message"), "REW9: Sequence queued"
		)


class TestDATEVconnectUncertainGroup(IntegrationTestCase):
	def setUp(self):
		for patcher in (patch("frappe.get_hooks", fake_hooks), patch("frappe.db.commit")):
			patcher.start()
			self.addCleanup(patcher.stop)
		self.client_doc = dc_client(approval_required=0, require_document_link=1)
		party = make_party_account(self.client_doc, account_number=10001, sync_status="Imported")

		def add(date, **values):
			document = make_document(
				self.client_doc,
				document_date=date,
				status="Uploaded",
				external_id="ID-1",
				program_code="BEDI",
			)
			return intake.add_booking(
				document.name,
				"1",
				kind="Outgoing Invoice",
				party_account=party.name,
				contra_account=4400,
				amount=119,
				debit_credit="S",
				**values,
			)

		self.delivered = add("2026-12-30")
		self.uncertain = add("2027-01-05")
		self.unbuildable = add("2027-01-06", bu_key="X1")
		with patch(f"{DC_API}.fiscal_years", return_value=OPEN_YEARS):
			self.batch = dispatch.collect(self.client_doc.name)

		def request(method, url, **kwargs):
			if "/20260101/" in url:
				return fake_response(201, {"succeeded": [{"index": "0"}], "failed": []})
			raise requests.ReadTimeout("read timed out")

		with patch("datev.http.requests.request", side_effect=request):
			dispatch.send(self.batch)

	def states(self):
		return [
			frappe.db.get_value("DATEV Booking", name, ["status", "batch"])
			for name in (self.delivered, self.uncertain, self.unbuildable)
		]

	def test_uncertain_group_keeps_other_results(self):
		self.assertEqual(
			self.states(),
			[("Transferred", self.batch), ("Batched", self.batch), ("Failed", None)],
		)
		self.assertEqual(
			frappe.db.get_value("DATEV Batch", self.batch, ["status", "message"]),
			("Sending", "Delivery to DATEV is uncertain. Check DATEV, then resolve the batch."),
		)

	def test_resolve_not_delivered_resets_only_uncertain(self):
		dispatch.resolve_batch(self.batch, delivered=False)
		self.assertEqual(self.states(), [("Transferred", self.batch), ("Pending", None), ("Failed", None)])

	def test_resolve_delivered_transfers_only_uncertain(self):
		dispatch.resolve_batch(self.batch, delivered=True)
		self.assertEqual(
			self.states(), [("Transferred", self.batch), ("Transferred", self.batch), ("Failed", None)]
		)
		self.assertEqual(frappe.db.get_value("DATEV Batch", self.batch, "status"), "Partially Sent")


def client_job(client):
	return {
		"queue": "long",
		"timeout": 3600,
		"job_id": f"datev-client-{client}",
		"deduplicate": True,
	}


def send_job(batch):
	return {"queue": "long", "timeout": 3600, "job_id": f"datev-send-{batch}", "deduplicate": True}


def only_own_client(client):
	real_get_all = frappe.get_all

	def get_all(doctype, *args, **kwargs):
		key = {"DATEV Client": "name", "DATEV Batch": "client"}.get(doctype)
		if key:
			kwargs["filters"] = {**(kwargs.get("filters") or {}), key: client}
		return real_get_all(doctype, *args, **kwargs)

	return get_all


class TestDispatchErrors(IntegrationTestCase):
	def setUp(self):
		for patcher in (patch("frappe.db.commit"), patch("frappe.db.rollback")):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_run_isolates_client_errors(self):
		broken = make_client(dispatch_frequency="Daily")
		healthy = make_client(dispatch_frequency="Daily")
		real_is_due = dispatch.is_due

		def is_due(client, now):
			if client.name == broken.name:
				raise RuntimeError("boom")
			return real_is_due(client, now)

		with (
			patch("datev.dispatch.is_due", side_effect=is_due),
			patch("frappe.enqueue") as enqueue,
			patch("frappe.log_error") as log_error,
		):
			dispatch.run()
		clients = [call.kwargs.get("client") for call in enqueue.call_args_list]
		self.assertIn(healthy.name, clients)
		self.assertNotIn(broken.name, clients)
		self.assertIn(broken.name, [call.kwargs.get("reference_name") for call in log_error.call_args_list])

	def test_process_client_logs_and_reraises(self):
		client = make_client()
		with (
			patch("datev.dispatch.collect", side_effect=RuntimeError("boom")),
			patch("frappe.log_error") as log_error,
		):
			with self.assertRaises(RuntimeError):
				dispatch.process_client(client.name)
		log_error.assert_called_once_with(
			title="DATEV booking collection failed",
			reference_doctype="DATEV Client",
			reference_name=client.name,
		)
