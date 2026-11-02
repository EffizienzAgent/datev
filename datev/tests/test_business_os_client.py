import base64
from unittest.mock import MagicMock, patch

import requests
from frappe.tests import IntegrationTestCase

from datev.clients.business_os import BusinessOS, BusinessOSError


def fake_response(status=200, body=None, text=""):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = body
	if body is None:
		response.json.side_effect = ValueError
	response.text = text
	return response


class TestBusinessOSClient(IntegrationTestCase):
	def setUp(self):
		self.client = BusinessOS("key", connection_id="conn-1")

	@patch("datev.http.requests.request")
	def test_upload_body_and_params(self, request):
		request.return_value = fake_response(200, {"id": "abc", "dateiname": "a.pdf"})
		result = self.client.upload_document(b"%PDF", "/tmp/dir/a.pdf", "Rechnungseingang", note="x" * 80)

		self.assertEqual(result["id"], "abc")
		args, kwargs = request.call_args
		self.assertEqual(args, ("POST", "https://api.business-os.de/v2/datev-duo/beleg-bereitstellen"))
		self.assertEqual(kwargs["headers"]["x-api-key"], "key")
		self.assertEqual(kwargs["params"], {"connectionId": "conn-1", "quelle": "frappe-datev"})
		self.assertEqual(kwargs["timeout"], 60)
		body = kwargs["json"]
		self.assertEqual(body["datei"]["content"], base64.b64encode(b"%PDF").decode())
		self.assertEqual(body["datei"]["dateiname"], "a.pdf")
		self.assertEqual(body["belegtyp"], "Rechnungseingang")
		self.assertEqual(body["belegnotiz"], "x" * 60)
		self.assertNotIn("id", body)

	@patch("datev.http.requests.request")
	def test_upload_includes_id_when_given(self, request):
		request.return_value = fake_response(200, {"id": "abc"})
		self.client.upload_document(b"x", "a.pdf", "T", id="abc")
		body = request.call_args.kwargs["json"]
		self.assertEqual(body["id"], "abc")
		self.assertNotIn("belegnotiz", body)

	@patch("datev.http.requests.request")
	def test_no_connection_id_param_when_unset(self, request):
		request.return_value = fake_response(200, {"connections": [{"id": "1"}]})
		self.assertEqual(BusinessOS("key").connections(), [{"id": "1"}])
		args, kwargs = request.call_args
		self.assertEqual(args[1], "https://api.business-os.de/v2/datev-duo/connections")
		self.assertEqual(kwargs["params"], {"quelle": "frappe-datev"})

	@patch("datev.http.requests.request")
	def test_companies(self, request):
		request.return_value = fake_response(200, {"data": [{"name": "A"}]})
		self.assertEqual(self.client.companies(), [{"name": "A"}])
		self.assertTrue(request.call_args.args[1].endswith("/v2/datev/companies"))

	@patch("datev.http.requests.request")
	def test_document_types_parsed(self, request):
		rows = [{"belegtyp": "Rechnungseingang", "belegkreis": "Eingangsrechnung"}]
		request.return_value = fake_response(200, {"data": rows})
		self.assertEqual(self.client.document_types(), rows)
		self.assertTrue(request.call_args.args[1].endswith("/v2/datev-duo/belegtypen"))

	@patch("datev.http.requests.request")
	def test_error_code_exposed(self, request):
		request.return_value = fake_response(400, {"error": "x", "code": "BELEGTYP_UNKNOWN"})
		with self.assertRaises(BusinessOSError) as ctx:
			self.client.document_types()
		self.assertEqual(ctx.exception.code, "BELEGTYP_UNKNOWN")
		self.assertEqual(ctx.exception.status, 400)
		self.assertEqual(str(ctx.exception), "x")
		self.assertFalse(ctx.exception.retryable)

	@patch("datev.http.requests.request")
	def test_error_falls_back_to_text(self, request):
		request.return_value = fake_response(502, None, "Bad gateway")
		with self.assertRaises(BusinessOSError) as ctx:
			self.client.companies()
		self.assertEqual(str(ctx.exception), "Bad gateway")
		self.assertIsNone(ctx.exception.code)
		self.assertTrue(ctx.exception.retryable)

	@patch("datev.http.requests.request")
	def test_rate_limit_is_retryable(self, request):
		request.return_value = fake_response(429, {"error": "slow down"})
		with self.assertRaises(BusinessOSError) as ctx:
			self.client.companies()
		self.assertTrue(ctx.exception.retryable)

	@patch("datev.http.requests.request", side_effect=requests.Timeout("timed out"))
	def test_timeout_is_retryable(self, request):
		with self.assertRaises(BusinessOSError) as ctx:
			self.client.companies()
		self.assertTrue(ctx.exception.retryable)
