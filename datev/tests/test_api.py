import base64
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from datev import api
from datev.tests.factories import make_client, make_document, make_party_account


class TestApi(IntegrationTestCase):
	def setUp(self):
		self.client_doc = make_client()
		self.addCleanup(frappe.set_user, "Administrator")

	def submit(self, **overrides):
		values = {
			"client": self.client_doc.name,
			"source_key": f"SINV-{frappe.generate_hash(length=8)}",
			"direction": "Outgoing",
			"file_name": "invoice.txt",
			"content_base64": base64.b64encode(b"invoice").decode(),
			"document_date": "2027-01-05",
			"document_number": "RE-1",
			"amount": "119.00",
		}
		values.update(overrides)
		with patch("frappe.enqueue"):
			return api.submit_document(**values), values

	def test_submit_document_roundtrip(self):
		result, values = self.submit()
		doc = frappe.get_doc("DATEV Document", result["name"])
		self.assertEqual(result["status"], "Pending")
		self.assertEqual(doc.source_key, values["source_key"])
		self.assertEqual(doc.amount, 119)
		file = frappe.get_doc("File", {"file_url": doc.file})
		self.assertEqual(file.get_content(), "invoice")

	def test_invalid_base64_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.submit(content_base64="not base64!!")

	def test_invalid_amount_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.submit(amount="abc")

	def test_non_finite_numbers_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.submit(amount="NaN")
		party = api.submit_party(self.client_doc.name, "Debtor", "Example Party")
		document, _values = self.submit()
		with self.assertRaises(frappe.ValidationError):
			api.submit_booking(
				document["name"], "1", "Outgoing Invoice", party["name"], 8400, 119, "S", tax_rate="Infinity"
			)

	def test_user_without_roles_rejected(self):
		user = frappe.get_doc(
			{"doctype": "User", "email": "no-roles@example.com", "first_name": "No Roles"}
		).insert(ignore_if_duplicate=True, ignore_permissions=True)
		frappe.set_user(user.name)
		with self.assertRaises(frappe.PermissionError):
			api.status("anything")

	def test_source_key_of_other_client_rejected(self):
		result, values = self.submit()
		other = make_client()
		with self.assertRaises(frappe.ValidationError):
			self.submit(client=other.name, source_key=values["source_key"])
		self.assertEqual(
			frappe.db.get_value("DATEV Document", result["name"], "client"), self.client_doc.name
		)

	def test_permission_required(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			self.submit()
		with self.assertRaises(frappe.PermissionError):
			api.status("anything")

	def test_submit_party_and_booking(self):
		party = api.submit_party(
			self.client_doc.name, "Debtor", "Example Party", vat_id="DE123456789", preferred_number="10001"
		)
		self.assertEqual(frappe.db.get_value("DATEV Party Account", party["name"], "preferred_number"), 10001)
		document, _values = self.submit()
		booking = api.submit_booking(
			document["name"], "1", "Outgoing Invoice", party["name"], "8400", "119.00", "S", tax_rate="19"
		)
		self.assertEqual(booking["status"], "Pending")
		self.assertEqual(frappe.db.get_value("DATEV Booking", booking["name"], "contra_account"), 8400)

	def test_status_reports_bookings(self):
		document = make_document(self.client_doc, status="Uploaded", external_id="ID-1", program_code="DUO")
		party = make_party_account(self.client_doc)
		api.submit_booking(document.name, "1", "Outgoing Invoice", party.name, 8400, 119, "S")
		result = api.status(document.source_key)
		self.assertEqual(result["document"]["external_id"], "ID-1")
		self.assertEqual(result["document"]["program_code"], "DUO")
		self.assertEqual(len(result["bookings"]), 1)
		self.assertEqual(result["bookings"][0]["line_key"], "1")
		self.assertEqual(result["bookings"][0]["status"], "Pending")

	def test_status_unknown_key(self):
		with self.assertRaises(frappe.DoesNotExistError):
			api.status("does-not-exist")
