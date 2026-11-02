from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from datev import http
from datev.connectors import registry
from datev.connectors.base import StorageConnector, StorageResult
from datev.connectors.business_os import BusinessOSStorage
from datev.connectors.datevconnect import DATEVconnectExport, DATEVconnectMasterData
from datev.connectors.duo_email import DuoEmailStorage
from datev.connectors.extf import ExtfDownloadExport, ExtfEmailExport, ExtfMasterData
from datev.tests.factories import make_client


class FakeStorage(StorageConnector):
	def upload(self, document):
		return StorageResult(external_id="x", program_code=None)


def fake_hooks(name):
	return [{"Fake": "datev.tests.test_registry.FakeStorage"}]


class TestHttp(IntegrationTestCase):
	def test_http_parses_json(self):
		response = MagicMock(status_code=200, text='{"a": 1}')
		response.json.return_value = {"a": 1}
		with patch("datev.http.requests.request", return_value=response) as mocked:
			result = http.request("GET", "https://example.test", params={"q": 1})
		self.assertEqual((result.status, result.data, result.text), (200, {"a": 1}, '{"a": 1}'))
		self.assertEqual(mocked.call_args.args, ("GET", "https://example.test"))

	def test_http_non_json_body(self):
		response = MagicMock(status_code=500, text="oops")
		response.json.side_effect = ValueError
		with patch("datev.http.requests.request", return_value=response):
			result = http.request("GET", "https://example.test")
		self.assertIsNone(result.data)
		self.assertEqual(result.status, 500)

	def test_http_maybe_sent(self):
		from http.client import RemoteDisconnected

		from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError, ProtocolError

		def wrapped(reason):
			return requests.ConnectionError(MaxRetryError(None, "/", reason=reason))

		cases = [
			(requests.ConnectTimeout("connect timed out"), False),
			(requests.exceptions.SSLError("bad certificate"), False),
			(wrapped(NewConnectionError(None, "refused")), False),
			(wrapped(NameResolutionError("host", None, "unknown")), False),
			(
				requests.ConnectionError(ProtocolError("Connection aborted.", RemoteDisconnected("closed"))),
				True,
			),
			(wrapped(ProtocolError("Connection aborted.")), True),
			(requests.ConnectionError("unknown"), True),
			(requests.ReadTimeout("read timed out"), True),
			(requests.exceptions.ChunkedEncodingError("broken"), True),
		]
		for error, expected in cases:
			with self.subTest(error=type(error).__name__, expected=expected):
				with patch("datev.http.requests.request", side_effect=error):
					with self.assertRaises(http.TransportError) as raised:
						http.request("POST", "https://example.test")
				self.assertIs(raised.exception.maybe_sent, expected)

	def test_http_transport_error(self):
		with patch("datev.http.requests.request", side_effect=requests.ConnectionError("boom")):
			with self.assertRaises(http.TransportError):
				http.request("GET", "https://example.test")


class TestRegistry(IntegrationTestCase):
	def test_registry_resolves_hook_class(self):
		client = make_client()
		client.storage_connector = "Fake"
		with patch("frappe.get_hooks", fake_hooks):
			connector = registry.get_storage(client)
		self.assertIsInstance(connector, FakeStorage)
		self.assertIs(connector.client, client)

	def test_later_hook_values_win(self):
		client = make_client()
		client.storage_connector = "Fake"
		hooks = lambda name: {  # noqa: E731
			"Fake": ["datev.nonexistent.Nope", "datev.tests.test_registry.FakeStorage"]
		}
		with patch("frappe.get_hooks", hooks):
			self.assertIsInstance(registry.get_storage(client), FakeStorage)

	def test_unknown_label_raises(self):
		client = make_client()
		client.storage_connector = "Missing"
		with patch("frappe.get_hooks", fake_hooks):
			with self.assertRaises(frappe.ValidationError):
				registry.get_storage(client)

	def test_builtin_labels_resolve(self):
		client = make_client()
		cases = [
			(registry.get_storage, "storage_connector", "Business OS", BusinessOSStorage),
			(registry.get_storage, "storage_connector", "E-mail to DUo", DuoEmailStorage),
			(registry.get_export, "export_connector", "DATEVconnect", DATEVconnectExport),
			(registry.get_export, "export_connector", "EXTF download", ExtfDownloadExport),
			(registry.get_export, "export_connector", "EXTF e-mail", ExtfEmailExport),
			(registry.get_master_data, "master_data_connector", "DATEVconnect", DATEVconnectMasterData),
			(registry.get_master_data, "master_data_connector", "EXTF", ExtfMasterData),
		]
		for resolve, fieldname, label, cls in cases:
			with self.subTest(label=label):
				client.set(fieldname, label)
				self.assertIs(type(resolve(client)), cls)
