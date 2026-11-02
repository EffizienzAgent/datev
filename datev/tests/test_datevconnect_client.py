from unittest.mock import MagicMock, patch

import requests
from frappe.tests import IntegrationTestCase

from datev.clients.datevconnect import DATEVconnect, DATEVconnectError

BASE = "https://host:58452/datev/api/accounting/v1"
CLIENT = "78a11a29-2a32-4a5e-a73b-632f6aeae131"


def fake_response(status=200, body=None, text=""):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = body
	if body is None:
		response.json.side_effect = ValueError
	response.text = text
	return response


class TestDATEVconnectClient(IntegrationTestCase):
	def setUp(self):
		self.client = DATEVconnect(BASE + "/", "user", "secret", verify=False)

	@patch("datev.http.requests.request")
	def test_basic_auth_and_tls_flag(self, request):
		request.return_value = fake_response(200, [])
		self.client.clients()
		args, kwargs = request.call_args
		self.assertEqual(args, ("GET", BASE + "/clients"))
		self.assertEqual(kwargs["auth"], ("user", "secret"))
		self.assertIs(kwargs["verify"], False)
		self.assertEqual(kwargs["headers"]["Accept"], "application/json")
		self.assertEqual(kwargs["timeout"], 60)

	@patch("datev.http.requests.request")
	def test_client_id_lookup(self, request):
		request.return_value = fake_response(
			200,
			[
				{"id": "other", "number": "11111", "consultant_number": "29098"},
				{"id": CLIENT, "number": "55003", "consultant_number": "29098"},
			],
		)
		self.assertEqual(self.client.client_id_for(55003, 29098), CLIENT)
		with self.assertRaises(DATEVconnectError):
			self.client.client_id_for(99999, 29098)
		with self.assertRaises(DATEVconnectError):
			self.client.client_id_for(55003, 11111)

	@patch("datev.http.requests.request")
	def test_client_id_lookup_without_consultant_field(self, request):
		request.return_value = fake_response(200, [{"id": CLIENT, "number": "55003"}])
		self.assertEqual(self.client.client_id_for(55003, 29098), CLIENT)

	@patch("datev.http.requests.request")
	def test_post_proposals_path(self, request):
		body = {"succeeded": [{"index": "0"}], "failed": []}
		request.return_value = fake_response(201, body)
		result = self.client.post_proposals(CLIENT, "20260101", "outgoing", [{"a": 1}])
		args, kwargs = request.call_args
		self.assertEqual(
			args,
			(
				"POST",
				f"{BASE}/clients/{CLIENT}/fiscal-years/20260101/posting-proposals-outgoing-invoices/batch",
			),
		)
		self.assertEqual(kwargs["json"], [{"a": 1}])
		self.assertEqual(result, body)

	def test_invalid_direction_and_kind(self):
		with self.assertRaises(ValueError):
			self.client.post_proposals(CLIENT, "20260101", "sideways", [])
		with self.assertRaises(ValueError):
			self.client.parties(CLIENT, "20260101", "assets")

	@patch("datev.http.requests.request")
	def test_post_accounting_sequence_empty_body(self, request):
		request.return_value = fake_response(200, None)
		self.assertEqual(self.client.post_accounting_sequence(CLIENT, "20260101", {"x": 1}), {})
		self.assertTrue(request.call_args.args[1].endswith("/fiscal-years/20260101/accounting-sequences"))

	@patch("datev.http.requests.request")
	def test_next_available(self, request):
		request.return_value = fake_response(200, {"id": "100000000", "account_number": 100000000})
		self.assertEqual(self.client.next_available(CLIENT, "20260101", "debitors"), 100000000)
		self.assertTrue(request.call_args.args[1].endswith("/fiscal-years/20260101/debitors/next-available"))

	@patch("datev.http.requests.request")
	def test_malformed_bodies_raise_datevconnect_error(self, request):
		calls = (
			(lambda: self.client.next_available(CLIENT, "20260101", "debitors"), ({"id": "1"}, None, [1])),
			(lambda: self.client.client_id_for(55003, 29098), ([{"number": "55003"}], ["x"], {"a": 1})),
			(lambda: self.client.fiscal_years(CLIENT), ({"id": "1"}, [{"name": "x"}], ["x"], None)),
		)
		for call, bodies in calls:
			for body in bodies:
				with self.subTest(body=body):
					request.return_value = fake_response(200, body)
					with self.assertRaises(DATEVconnectError) as raised:
						call()
					self.assertEqual(str(raised.exception), "Unexpected response from DATEVconnect.")

	@patch("datev.http.requests.request")
	def test_put_party_content_type(self, request):
		request.return_value = fake_response(200, {"id": "1"})
		self.client.put_party(CLIENT, "20260101", "creditors", {"name": "A"})
		args, kwargs = request.call_args
		self.assertEqual(args[0], "PUT")
		self.assertTrue(args[1].endswith("/fiscal-years/20260101/creditors"))
		self.assertEqual(kwargs["headers"]["Content-Type"], "application/merge-patch+json")
		self.assertEqual(kwargs["json"], {"name": "A"})

	@patch("datev.http.requests.request")
	def test_server_error_retryable(self, request):
		request.return_value = fake_response(503, None, "unavailable")
		with self.assertRaises(DATEVconnectError) as ctx:
			self.client.fiscal_years(CLIENT)
		self.assertTrue(ctx.exception.retryable)
		self.assertEqual(ctx.exception.status, 503)

	@patch("datev.http.requests.request")
	def test_client_error_not_retryable(self, request):
		request.return_value = fake_response(400, {"message": "bad"}, "bad request")
		with self.assertRaises(DATEVconnectError) as ctx:
			self.client.fiscal_years(CLIENT)
		self.assertFalse(ctx.exception.retryable)
		self.assertEqual(ctx.exception.response, {"message": "bad"})

	@patch("datev.http.requests.request", side_effect=requests.ConnectionError("down"))
	def test_transport_error_retryable(self, request):
		with self.assertRaises(DATEVconnectError) as ctx:
			self.client.clients()
		self.assertTrue(ctx.exception.retryable)
