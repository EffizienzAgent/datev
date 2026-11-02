import frappe
from frappe.tests import IntegrationTestCase

from datev.tests.factories import make_client


class TestClient(IntegrationTestCase):
	def test_defaults(self):
		client = make_client()
		self.assertEqual(client.account_length, 4)
		self.assertEqual(client.dispatch_frequency, "Weekly")
		self.assertTrue(client.approval_required)
		self.assertTrue(client.require_document_link)

	def test_account_length_range(self):
		for value in (3, 9):
			with self.assertRaises(frappe.ValidationError):
				make_client(account_length=value)
		make_client(account_length=8)

	def test_month_and_day_ranges(self):
		for field, value in (
			("fiscal_year_start_month", 0),
			("fiscal_year_start_month", 13),
			("dispatch_day_of_month", 0),
			("dispatch_day_of_month", 29),
		):
			with self.assertRaises(frappe.ValidationError):
				make_client(**{field: value})

	def test_business_os_requires_credentials(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(bos_api_key=None)
		with self.assertRaises(frappe.ValidationError):
			make_client(bos_connection_id=None)

	def test_duo_requires_email(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(storage_connector="E-mail to DUo", duo_email=None)
		make_client(storage_connector="E-mail to DUo", duo_email="duo@example.com")

	def test_extf_email_requires_address(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(export_connector="EXTF e-mail")

	def test_datevconnect_requires_credentials(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(export_connector="DATEVconnect")
		with self.assertRaises(frappe.ValidationError):
			make_client(master_data_connector="DATEVconnect", dc_base_url="https://host:58452")
		make_client(
			export_connector="DATEVconnect",
			dc_base_url="https://host:58452/datev/api/accounting/v1",
			dc_username="user",
			dc_password="secret",
		)

	def test_extf_master_data_forbids_datev_numbering(self):
		for field in ("debtor_numbering", "creditor_numbering"):
			with self.assertRaises(frappe.ValidationError):
				make_client(master_data_connector="EXTF", **{field: "DATEV next available"})

	def test_bu_key_digits_only(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(tax_keys=[{"tax_code": "A", "bu_key": "9a"}])
		with self.assertRaises(frappe.ValidationError):
			make_client(tax_keys=[{"tax_code": "A", "bu_key": "12345"}])
		make_client(tax_keys=[{"tax_code": "A", "bu_key": "9"}, {"tax_code": "B", "bu_key": ""}])

	def test_duplicate_tax_code_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(tax_keys=[{"tax_code": "A", "bu_key": "9"}, {"tax_code": "A", "bu_key": "8"}])

	def test_duplicate_source_account_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_client(
				account_mappings=[
					{"source_account": 8400, "datev_account": 4400},
					{"source_account": 8400, "datev_account": 4401},
				]
			)
