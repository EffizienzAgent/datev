from datetime import datetime
from typing import ClassVar
from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from datev import dispatch, intake
from datev.connectors.base import ConnectorError, ExportConnector, ExportItemResult, ExportResult
from datev.tests.factories import make_client, make_document, make_party_account
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
		for target in ("frappe.get_hooks", "frappe.db.commit"):
			patcher = patch(target, fake_hooks) if target == "frappe.get_hooks" else patch(target)
			patcher.start()
			self.addCleanup(patcher.stop)
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
		self.assertEqual(add("2", tax_code="UNKNOWN"), "")
		self.assertEqual(add("3", tax_code="DE19", bu_key="9"), "9")
		self.assertEqual(add("4"), "")

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
		approved = frappe.get_doc("DATEV Batch", batch)
		self.assertEqual((approved.status, approved.approved_by), ("Sent", frappe.session.user))
		self.assertTrue(approved.approved_on)
		self.assertEqual(FakeExport.calls, [[ready]])

	def test_collect_without_approval_sends_on_run(self):
		booking = self.booking()
		with (
			patch("frappe.get_all", side_effect=only_own_client(self.client_doc.name)),
			patch("frappe.db.rollback"),
		):
			dispatch.run()

		batch = frappe.db.get_value("DATEV Booking", booking, "batch")
		self.assertEqual(self.status(booking), "Exported")
		batch_doc = frappe.get_doc("DATEV Batch", batch)
		self.assertEqual(batch_doc.status, "Sent")
		self.assertTrue(batch_doc.sent_on)
		self.assertEqual(
			frappe.db.get_value("DATEV Party Account", self.party.name, "sync_status"), "Created"
		)
		self.assertTrue(frappe.db.exists("DATEV Event Log", {"reference_name": batch, "event": "sent"}))

		with patch("frappe.get_all", side_effect=only_own_client(self.client_doc.name)):
			dispatch.run()
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
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(kept)
		self.assertEqual(self.status(kept), "Exported")

	def test_hold_removes_empty_batch(self):
		self.client_doc.db_set("approval_required", 1)
		booking = self.booking()
		batch = dispatch.collect(self.client_doc.name)
		dispatch.hold(booking)
		self.assertFalse(frappe.db.exists("DATEV Batch", batch))

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
		FakeExport.error = ConnectorError("DATEV unavailable", response={"error": "down"})
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

	def test_send_now_ignores_schedule(self):
		self.client_doc.db_set({"dispatch_frequency": "Monthly", "last_dispatch_on": frappe.utils.now()})
		booking = self.booking()
		dispatch.send_now(self.client_doc.name)
		self.assertEqual(self.status(booking), "Exported")

	def test_approve_requires_permission(self):
		self.client_doc.db_set({"approval_required": 1, "responsible_user": "approver@example.com"})
		self.booking()
		batch = dispatch.collect(self.client_doc.name)
		with patch("frappe.get_roles", return_value=["Guest"]):
			with self.assertRaises(frappe.PermissionError):
				dispatch.approve(batch)
			with patch.dict(frappe.session, {"user": "approver@example.com"}):
				dispatch.approve(batch)
		self.assertEqual(frappe.db.get_value("DATEV Batch", batch, "status"), "Sent")

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

	def test_hold_rejects_stuck_booking(self):
		booking = self.booking(self.document(status="Pending", external_id=None))
		dispatch.collect(self.client_doc.name)
		self.assertEqual(self.status(booking), "Stuck")
		with self.assertRaises(frappe.ValidationError):
			dispatch.hold(booking)


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
		self.assertEqual(frappe.db.get_value("DATEV Batch", self.batch, "status"), "Sent")


def only_own_client(client):
	real_get_all = frappe.get_all

	def get_all(doctype, *args, **kwargs):
		key = {"DATEV Client": "name", "DATEV Batch": "client"}.get(doctype)
		if key:
			kwargs["filters"] = {**(kwargs.get("filters") or {}), key: client}
		return real_get_all(doctype, *args, **kwargs)

	return get_all
