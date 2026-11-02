import frappe
from frappe.tests import IntegrationTestCase

from datev.beleglink import extf_value
from datev.events import log_event
from datev.tests.factories import make_booking, make_client, make_document, make_party_account


class TestRecords(IntegrationTestCase):
	def setUp(self):
		self.client = make_client()
		self.document = make_document(self.client)
		self.party = make_party_account(self.client)

	def test_booking_line_key_unique_per_document(self):
		make_booking(self.document, self.party, line_key="1")
		make_booking(self.document, self.party, line_key="2")
		with self.assertRaises(frappe.ValidationError):
			make_booking(self.document, self.party, line_key="1")
		other = make_document(self.client)
		make_booking(other, self.party, line_key="1")

	def test_booking_amount_positive(self):
		for amount in (0, -5):
			with self.assertRaises(frappe.ValidationError):
				make_booking(self.document, self.party, amount=amount)

	def test_booking_sets_fiscal_year(self):
		booking = make_booking(self.document, self.party, document_date="2027-01-05")
		self.assertEqual(booking.fiscal_year, "20270101")
		july = make_client(fiscal_year_start_month=7)
		july_document = make_document(july)
		july_party = make_party_account(july)
		booking = make_booking(july_document, july_party, document_date="2027-03-01")
		self.assertEqual(booking.fiscal_year, "20260701")

	def test_party_account_number_unique(self):
		make_party_account(self.client, account_number=10001)
		with self.assertRaises(frappe.ValidationError):
			make_party_account(self.client, account_number=10001)
		make_party_account(self.client, kind="Creditor", account_number=70001)
		make_party_account(make_client(), account_number=10001)

	def test_party_account_number_ranges(self):
		make_party_account(self.client, account_number=10000)
		with self.assertRaises(frappe.ValidationError):
			make_party_account(self.client, account_number=9999)
		make_party_account(self.client, kind="Creditor", account_number=70000)
		with self.assertRaises(frappe.ValidationError):
			make_party_account(self.client, kind="Creditor", account_number=69999)
		with self.assertRaises(frappe.ValidationError):
			make_party_account(self.client, account_number=70000)

	def test_party_account_number_ranges_scale_with_length(self):
		client = make_client(account_length=5)
		make_party_account(client, account_number=100000)
		make_party_account(client, kind="Creditor", account_number=700000)
		with self.assertRaises(frappe.ValidationError):
			make_party_account(client, account_number=10000)

	def test_event_log_not_editable(self):
		log_event("DATEV Client", self.client.name, "test", "message", {"a": 1})
		entry = frappe.get_last_doc("DATEV Event Log")
		self.assertEqual(entry.event, "test")
		self.assertEqual(frappe.parse_json(entry.response), {"a": 1})
		meta = frappe.get_meta("DATEV Event Log")
		self.assertTrue(meta.in_create)
		for permission in meta.permissions:
			self.assertFalse(permission.write or permission.delete)

	def test_event_log_truncates_response(self):
		log_event("DATEV Client", self.client.name, "big", response="x" * 30000)
		self.assertLessEqual(len(frappe.get_last_doc("DATEV Event Log").response), 20000)

	def test_extf_beleglink_value(self):
		self.assertEqual(extf_value("BEDI", "ABC"), 'BEDI "ABC"')
