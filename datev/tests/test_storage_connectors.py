from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from datev.clients.business_os import BusinessOSError
from datev.connectors.registry import get_storage
from datev.tests.factories import make_client, make_document


def fake_response(body, status=200):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = body
	if body is None:
		response.json.side_effect = ValueError
	response.text = ""
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
		request.return_value = fake_response({"id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"})
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

		credits_left, updated_on = frappe.db.get_value(
			"DATEV Client", client.name, ["bos_credits", "bos_credits_updated_on"]
		)
		self.assertEqual(credits_left, 42)
		self.assertTrue(updated_on)

	@patch("datev.http.requests.request")
	def test_credits_tolerate_missing_data(self, request):
		client = make_client(bos_id_mode="Use returned ID")
		document = self.make_document_with_file(client)
		for body in ({"id": "x"}, {"id": "x", "business-os": None}, {"id": "x", "business-os": "n/a"}):
			with self.subTest(body=body):
				request.return_value = fake_response(body)
				self.assertEqual(get_storage(client).upload(document).external_id, "x")
		self.assertFalse(frappe.db.get_value("DATEV Client", client.name, "bos_credits_updated_on"))

	@patch("datev.http.requests.request")
	def test_unexpected_success_body_is_not_retryable(self, request):
		for mode in ("Send own ID", "Use returned ID"):
			client = make_client(bos_id_mode=mode)
			document = self.make_document_with_file(client)
			for body in (None, {"status": "ok"}, ["x"]):
				with self.subTest(mode=mode, body=body):
					request.return_value = fake_response(body)
					with self.assertRaises(BusinessOSError) as raised:
						get_storage(client).upload(document)
					self.assertFalse(raised.exception.retryable)
					self.assertEqual(str(raised.exception), "Unexpected response from Business OS.")

	@patch("datev.http.requests.request", side_effect=requests.ReadTimeout("read timed out"))
	def test_maybe_sent_upload_with_returned_id_is_not_retried(self, request):
		client = make_client(bos_id_mode="Use returned ID")
		with self.assertRaises(BusinessOSError) as raised:
			get_storage(client).upload(self.make_document_with_file(client))
		self.assertFalse(raised.exception.retryable)
		self.assertEqual(str(raised.exception), "Upload may have reached DATEV. Check DUo before retrying.")

		own = make_client(bos_id_mode="Send own ID")
		with self.assertRaises(BusinessOSError) as raised:
			get_storage(own).upload(self.make_document_with_file(own))
		self.assertTrue(raised.exception.retryable)

	@patch("datev.http.requests.request")
	def test_returned_id_5xx_stuck(self, request):
		request.return_value = fake_response({"error": "gateway"}, status=502)
		client = make_client(bos_id_mode="Use returned ID")
		with self.assertRaises(BusinessOSError) as raised:
			get_storage(client).upload(self.make_document_with_file(client))
		self.assertFalse(raised.exception.retryable)
		self.assertEqual(str(raised.exception), "Upload may have reached DATEV. Check DUo before retrying.")

		own = make_client(bos_id_mode="Send own ID")
		with self.assertRaises(BusinessOSError) as raised:
			get_storage(own).upload(self.make_document_with_file(own))
		self.assertTrue(raised.exception.retryable)

	@patch("datev.http.requests.request")
	def test_own_id_mismatch_stuck(self, request):
		request.return_value = fake_response({"id": "other-id"})
		client = make_client(bos_id_mode="Send own ID")
		document = self.make_document_with_file(client)
		with self.assertRaises(BusinessOSError) as raised:
			get_storage(client).upload(document)
		self.assertFalse(raised.exception.retryable)
		self.assertIn("other-id", str(raised.exception))
		self.assertIn(document.guid, str(raised.exception))

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
	def test_business_os_connections_flags_matching_connection(self, request):
		matching = {"consultant_number": 29098, "client_number": 55003, "id": "29098-55003"}
		other = {"consultant_number": 1, "client_number": 2, "id": "1-2"}
		request.side_effect = [
			fake_response(
				{
					"connections": [
						{"id": "c1", "bezeichnung": "First", "status": "connected"},
						{"id": "c2", "bezeichnung": "Second", "status": "pending"},
					]
				}
			),
			fake_response({"data": [other]}),
			fake_response({"data": [other, matching]}),
		]
		client = make_client()

		rows = client.business_os_connections()

		self.assertEqual(
			rows,
			[
				{"id": "c1", "bezeichnung": "First", "status": "connected", "suggested": False},
				{"id": "c2", "bezeichnung": "Second", "status": "pending", "suggested": True},
			],
		)
		company_calls = [c for c in request.call_args_list if c.args[1].endswith("/v2/datev/companies")]
		self.assertEqual([c.kwargs["params"]["connectionId"] for c in company_calls], ["c1", "c2"])
