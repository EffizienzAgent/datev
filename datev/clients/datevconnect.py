from frappe import _

from datev import http
from datev.connectors.base import ConnectorError

PROPOSAL_DIRECTIONS = ("outgoing", "incoming")
PARTY_KINDS = ("debitors", "creditors")


class DATEVconnectError(ConnectorError):
	def __init__(
		self,
		message: str,
		*,
		status: int | None = None,
		retryable: bool = False,
		response: object = None,
		maybe_sent: bool = False,
	):
		super().__init__(message, retryable=retryable, response=response)
		self.status = status
		self.maybe_sent = maybe_sent


def error_message(data) -> str:
	if not isinstance(data, dict):
		return ""
	head = ": ".join(str(part) for part in (data.get("error"), data.get("error_description")) if part)
	details = "; ".join(
		f"{message.get('id')}: {message.get('description')}"
		if message.get("id")
		else message.get("description")
		for message in data.get("additional_messages") or []
		if isinstance(message, dict) and message.get("description")
	)
	if head and details:
		return f"{head} ({details})"
	return head or details


def unexpected(data) -> DATEVconnectError:
	return DATEVconnectError(_("Unexpected response from DATEVconnect."), response=data)


class DATEVconnect:
	def __init__(self, base_url: str, username: str, password: str, verify: bool = True):
		self.base_url = base_url.rstrip("/")
		self.username = username
		self.password = password
		self.verify = verify

	def clients(self) -> list[dict]:
		return self._call("GET", "/clients")

	def client_id_for(self, client_number: int, consultant_number: int) -> str:
		clients = self.clients()
		try:
			for item in clients:
				if str(item.get("number")) != str(client_number):
					continue
				if str(item.get("consultant_number", consultant_number)) == str(consultant_number):
					return str(item["id"])
		except AttributeError, KeyError, TypeError:
			raise unexpected(clients) from None
		raise DATEVconnectError(_("No DATEVconnect client {0}/{1}").format(consultant_number, client_number))

	def fiscal_years(self, client_id: str) -> list[dict]:
		years = self._call("GET", f"/clients/{client_id}/fiscal-years")
		if not isinstance(years, list) or not all(isinstance(y, dict) and y.get("id") for y in years):
			raise unexpected(years)
		return years

	def post_proposals(self, client_id: str, fiscal_year_id: str, direction: str, items: list[dict]):
		if direction not in PROPOSAL_DIRECTIONS:
			raise ValueError(f"direction must be one of {PROPOSAL_DIRECTIONS}")
		path = self._year_path(client_id, fiscal_year_id, f"posting-proposals-{direction}-invoices/batch")
		return self._call("POST", path, json=items)

	def post_accounting_sequence(self, client_id: str, fiscal_year_id: str, payload: dict) -> dict:
		path = self._year_path(client_id, fiscal_year_id, "accounting-sequences")
		return self._call("POST", path, json=payload) or {}

	def parties(self, client_id: str, fiscal_year_id: str, kind: str) -> list[dict]:
		return self._call("GET", self._party_path(client_id, fiscal_year_id, kind))

	def next_available(self, client_id: str, fiscal_year_id: str, kind: str) -> int:
		path = self._party_path(client_id, fiscal_year_id, kind) + "/next-available"
		data = self._call("GET", path)
		try:
			return int(data["account_number"])
		except KeyError, TypeError, ValueError:
			raise unexpected(data) from None

	def put_party(self, client_id: str, fiscal_year_id: str, kind: str, payload: list[dict]) -> dict:
		return self._call(
			"PUT",
			self._party_path(client_id, fiscal_year_id, kind),
			json=payload,
			content_type="application/merge-patch+json",
		)

	def _year_path(self, client_id: str, fiscal_year_id: str, tail: str) -> str:
		return f"/clients/{client_id}/fiscal-years/{fiscal_year_id}/{tail}"

	def _party_path(self, client_id: str, fiscal_year_id: str, kind: str) -> str:
		if kind not in PARTY_KINDS:
			raise ValueError(f"kind must be one of {PARTY_KINDS}")
		return self._year_path(client_id, fiscal_year_id, kind)

	def _call(self, method: str, path: str, json=None, content_type: str | None = None):
		headers = {"Accept": "application/json"}
		if content_type:
			headers["Content-Type"] = content_type
		try:
			response = http.request(
				method,
				self.base_url + path,
				headers=headers,
				json=json,
				auth=(self.username, self.password),
				verify=self.verify,
				timeout=60,
			)
		except http.TransportError as e:
			raise DATEVconnectError(str(e), retryable=True, maybe_sent=e.maybe_sent) from e

		if 200 <= response.status < 300:
			return response.data
		raise DATEVconnectError(
			error_message(response.data) or response.text or f"HTTP {response.status}",
			status=response.status,
			retryable=response.status >= 500,
			response=response.data,
		)
