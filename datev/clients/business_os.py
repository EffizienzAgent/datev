import base64
import os

from frappe import _

from datev import http
from datev.connectors.base import ConnectorError

RETRYABLE_STATUS = (408, 425, 429)
SOURCE = "frappe-datev"


class BusinessOSError(ConnectorError):
	def __init__(
		self,
		message: str,
		*,
		code: str | None = None,
		status: int | None = None,
		retryable: bool = False,
		response: object = None,
		maybe_sent: bool = False,
	):
		super().__init__(message, retryable=retryable, response=response)
		self.code = code
		self.status = status
		self.maybe_sent = maybe_sent


class BusinessOS:
	def __init__(
		self, api_key: str, connection_id: str | None = None, base_url: str = "https://api.business-os.de"
	):
		self.api_key = api_key
		self.connection_id = connection_id
		self.base_url = base_url.rstrip("/")

	def connections(self, module: str = "datev-duo") -> list[dict]:
		return self._call("GET", f"/v2/{module}/connections")["connections"]

	def companies(self) -> list[dict]:
		return self._call("GET", "/v2/datev/companies")["data"]

	def document_types(self) -> list[dict]:
		return self._call("GET", "/v2/datev-duo/belegtypen")["data"]

	def upload_document(
		self,
		content: bytes,
		filename: str,
		document_type: str,
		id: str | None = None,
		note: str | None = None,
	) -> dict:
		body = {
			"datei": {"content": base64.b64encode(content).decode(), "dateiname": os.path.basename(filename)},
			"belegtyp": document_type,
		}
		if id:
			body["id"] = id
		if note:
			body["belegnotiz"] = note[:60]
		data = self._call("POST", "/v2/datev-duo/beleg-bereitstellen", json=body)
		if not isinstance(data, dict) or not data.get("id"):
			raise BusinessOSError(_("Unexpected response from Business OS."), response=data)
		return data

	def _call(self, method: str, path: str, json=None) -> dict:
		params = {"quelle": SOURCE}
		if self.connection_id:
			params["connectionId"] = self.connection_id
		try:
			response = http.request(
				method,
				self.base_url + path,
				headers={"x-api-key": self.api_key},
				params=params,
				json=json,
				timeout=60,
			)
		except http.TransportError as e:
			raise BusinessOSError(str(e), retryable=True, maybe_sent=e.maybe_sent) from e

		if 200 <= response.status < 300:
			return response.data
		body = response.data if isinstance(response.data, dict) else {}
		raise BusinessOSError(
			body.get("error") or response.text,
			code=body.get("code"),
			status=response.status,
			retryable=response.status in RETRYABLE_STATUS or response.status >= 500,
			response=response.data,
			maybe_sent=response.status >= 500,
		)
