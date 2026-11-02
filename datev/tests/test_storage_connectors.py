from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from datev.connectors.registry import get_storage
from datev.tests.factories import make_client, make_document


def fake_response(body):
	response = MagicMock()
	response.status_code = 200
	response.json.return_value = body
	return response


class TestStorageConnectors(IntegrationTestCase):
	def make_document_with_file(self, client, **overrides):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{frappe.generate_hash(length=8)}.txt",
				"content": b"test content",
				"is_private": 1,
			}
		).insert()
		return make_document(
			client,
			file=file.file_url,
			file_name=file.file_name,
			guid="3F2504E0-4F89-11D3-9A0C-0305E82C3301",
			document_type="Rechnungsausgang",
			**overrides,
		)

	@patch("datev.http.requests.request")
	def test_send_own_id(self, request):
		request.return_value = fake_response({"id": "ignored"})
		client = make_client(bos_id_mode="Send own ID")
		document = self.make_document_with_file(client)

		result = get_storage(client).upload(document)

		body = request.call_args.kwargs["json"]
		self.assertEqual(body["id"], document.guid)
		self.assertEqual(body["belegtyp"], "Rechnungsausgang")
		self.assertEqual(result.external_id, document.guid)
		self.assertEqual(result.program_code, "BEDI")

	@patch("datev.http.requests.request")
	def test_use_returned_id(self, request):
		request.return_value = fake_response({"id": "returned-id"})
		client = make_client(bos_id_mode="Use returned ID")
		document = self.make_document_with_file(client)

		result = get_storage(client).upload(document)

		self.assertNotIn("id", request.call_args.kwargs["json"])
		self.assertEqual(result.external_id, "returned-id")
		self.assertEqual(result.program_code, "BEDI")

	@patch("datev.http.requests.request")
	def test_credits_stored(self, request):
		request.return_value = fake_response({"id": "x", "business-os": {"neuesGuthaben": 42}})
		client = make_client(bos_id_mode="Use returned ID")
		document = self.make_document_with_file(client)

		get_storage(client).upload(document)

		self.assertEqual(frappe.db.get_value("DATEV Client", client.name, "bos_credits"), 42)

	@patch("frappe.sendmail")
	def test_duo_email_has_no_link(self, sendmail):
		client = make_client(storage_connector="E-mail to DUo", duo_email="duo@example.com")
		document = self.make_document_with_file(client)

		result = get_storage(client).upload(document)

		self.assertIsNone(result.external_id)
		self.assertIsNone(result.program_code)
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["duo@example.com"])
		self.assertEqual(kwargs["attachments"][0]["fcontent"], b"test content")
		self.assertTrue(kwargs["now"])

	@patch("datev.http.requests.request")
	def test_refresh_document_types(self, request):
		rows = [
			{"belegtyp": "Rechnungsausgang", "belegkreis": "Ausgangsrechnung"},
			{"belegtyp": "Rechnungseingang", "belegkreis": "Eingangsrechnung"},
		]
		request.return_value = fake_response({"data": rows})
		client = make_client()
		client.append("document_type_options", {"document_type": "Old", "category": "Old"})
		client.save()

		count = client.refresh_document_types()

		self.assertEqual(count, 2)
		client.reload()
		self.assertEqual(
			[(r.document_type, r.category) for r in client.document_type_options],
			[("Rechnungsausgang", "Ausgangsrechnung"), ("Rechnungseingang", "Eingangsrechnung")],
		)

	@patch("datev.http.requests.request")
	def test_business_os_connections_includes_matching_company(self, request):
		request.side_effect = [
			fake_response({"connections": [{"id": "c1", "bezeichnung": "Main", "status": "active"}]}),
			fake_response(
				{
					"data": [
						{"id": "1-2", "name": "Other", "consultant_number": 1, "client_number": 2},
						{
							"id": "29098-55003",
							"name": "Match",
							"consultant_number": 29098,
							"client_number": 55003,
						},
					]
				}
			),
		]
		client = make_client()

		rows = client.business_os_connections()

		self.assertEqual(rows[0], {"id": "c1", "bezeichnung": "Main", "status": "active"})
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1]["id"], "29098-55003")
