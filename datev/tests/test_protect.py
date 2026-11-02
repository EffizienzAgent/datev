from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from datev import dispatch
from datev.tests.factories import make_booking, make_client, make_document, make_party_account


class TestProtectedFields(IntegrationTestCase):
	def setUp(self):
		self.client_doc = make_client()
		self.party = make_party_account(self.client_doc, account_number=10001)
		self.document = make_document(self.client_doc, status="Uploaded", external_id="ID-1")
		self.booking = make_booking(self.document, self.party)

	def test_desk_save_cannot_change_booking_state(self):
		other = make_party_account(self.client_doc, account_number=10002)
		for field, value in (("status", "Transferred"), ("amount", 5), ("party_account", other.name)):
			with self.subTest(field=field):
				doc = frappe.get_doc("DATEV Booking", self.booking.name)
				doc.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					doc.save()

		doc = frappe.get_doc("DATEV Booking", self.booking.name)
		doc.posting_text = "Free text"
		doc.save()

		doc.status = "Exported"
		doc.flags.datev_internal = True
		doc.save()
		self.assertEqual(frappe.db.get_value("DATEV Booking", doc.name, "status"), "Exported")

	def test_desk_insert_cannot_set_state(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{**self.booking.as_dict(), "name": None, "line_key": "2", "status": "Transferred"}
			).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "DATEV Batch", "client": self.client_doc.name, "status": "Approved"}
			).insert()

	def test_desk_save_cannot_change_document_link_or_batch_status(self):
		document = frappe.get_doc("DATEV Document", self.document.name)
		document.external_id = "OTHER"
		with self.assertRaises(frappe.ValidationError):
			document.save()

		batch = frappe.get_doc({"doctype": "DATEV Batch", "client": self.client_doc.name}).insert()
		batch.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			batch.save()

	def test_fields_read_only_in_meta(self):
		for doctype, fields in (
			("DATEV Booking", ("status", "batch", "amount", "party_account", "contra_account", "document")),
			("DATEV Batch", ("status",)),
			("DATEV Document", ("status", "external_id", "guid", "program_code")),
		):
			meta = frappe.get_meta(doctype)
			for field in fields:
				self.assertTrue(meta.get_field(field).read_only, f"{doctype}.{field}")


class TestResetBooking(IntegrationTestCase):
	def setUp(self):
		client = make_client()
		party = make_party_account(client, account_number=10001)
		self.booking = make_booking(make_document(client), party)

	def test_reset_requires_reason_and_logs_it(self):
		self.booking.db_set({"status": "Transferred", "batch": None})
		with self.assertRaises(frappe.ValidationError):
			dispatch.reset_booking(self.booking.name, "  ")
		dispatch.reset_booking(self.booking.name, "Deleted in DATEV by the tax office")
		self.assertEqual(
			frappe.db.get_value("DATEV Booking", self.booking.name, ["status", "batch"]), ("Pending", None)
		)
		log = frappe.get_all(
			"DATEV Event Log",
			filters={"reference_name": self.booking.name, "event": "reset"},
			pluck="message",
		)
		self.assertEqual(log, ["Reset from Transferred: Deleted in DATEV by the tax office"])

	def test_reset_only_from_final_or_failed(self):
		for status in ("Pending", "Batched", "Stuck"):
			with self.subTest(status=status):
				self.booking.db_set("status", status)
				with self.assertRaises(frappe.ValidationError):
					dispatch.reset_booking(self.booking.name, "reason")

	def test_reset_requires_manager(self):
		self.booking.db_set("status", "Exported")
		with patch("frappe.only_for", side_effect=frappe.PermissionError) as only_for:
			with self.assertRaises(frappe.PermissionError):
				dispatch.reset_booking(self.booking.name, "reason")
		only_for.assert_called_once_with("DATEV Manager")
