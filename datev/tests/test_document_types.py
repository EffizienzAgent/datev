import frappe
from frappe.tests import IntegrationTestCase

from datev.document_types import DocumentTypeError, resolve
from datev.tests.factories import make_client


def rule(direction="Outgoing", document_type="X", source_doctype=None, is_return="Any"):
	return {
		"direction": direction,
		"source_doctype": source_doctype,
		"is_return": is_return,
		"document_type": document_type,
	}


class TestDocumentTypes(IntegrationTestCase):
	def test_default_per_direction(self):
		client = make_client()
		self.assertEqual(resolve(client, "Outgoing", None, False), "Rechnungsausgang")
		self.assertEqual(resolve(client, "Incoming", None, False), "Rechnungseingang")
		self.assertEqual(resolve(client, "Other", None, False), "Sonstige")

	def test_rule_order_first_match(self):
		client = make_client(
			document_type_rules=[
				rule(document_type="Specific", source_doctype="Sales Invoice"),
				rule(document_type="Generic"),
			]
		)
		self.assertEqual(resolve(client, "Outgoing", "Sales Invoice", False), "Specific")
		self.assertEqual(resolve(client, "Outgoing", "Other Doctype", False), "Generic")
		self.assertEqual(resolve(client, "Incoming", None, False), "Rechnungseingang")

	def test_return_rule(self):
		client = make_client(document_type_rules=[rule(document_type="Stornorechnung", is_return="Yes")])
		self.assertEqual(resolve(client, "Outgoing", None, True), "Stornorechnung")
		self.assertEqual(resolve(client, "Outgoing", None, False), "Rechnungsausgang")

	def test_unknown_type_rejected_when_options_present(self):
		client = make_client(document_type_options=[{"document_type": "Rechnungsausgang"}])
		self.assertEqual(resolve(client, "Outgoing", None, False), "Rechnungsausgang")
		with self.assertRaises(DocumentTypeError) as ctx:
			resolve(client, "Incoming", None, False)
		self.assertIn("Rechnungseingang", str(ctx.exception))

	def test_any_type_allowed_without_options(self):
		client = make_client(document_type_rules=[rule(document_type="Anything")])
		self.assertEqual(resolve(client, "Outgoing", None, False), "Anything")
