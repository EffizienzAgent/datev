from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import frappe


class ConnectorError(Exception):
	def __init__(
		self, message: str, *, retryable: bool = False, response: object = None, nothing_sent: bool = False
	):
		super().__init__(message)
		self.retryable = retryable
		self.response = response
		self.nothing_sent = nothing_sent


@dataclass
class StorageResult:
	external_id: str | None
	program_code: str | None
	response: object = None


@dataclass
class ExportItemResult:
	booking: str
	ok: bool
	message: str = ""


@dataclass
class ExportResult:
	items: list[ExportItemResult]
	files: list[str] = field(default_factory=list)
	response: object = None
	uncertain: list[str] = field(default_factory=list)


@dataclass
class RemoteParty:
	kind: str
	account_number: int
	name: str
	vat_id: str | None = None
	iban: str | None = None


class Connector:
	def __init__(self, client):
		self.client = client


class StorageConnector(Connector, ABC):
	@abstractmethod
	def upload(self, document) -> StorageResult: ...

	def document_types(self) -> list[dict]:
		return []


class ExportConnector(Connector, ABC):
	final_status: str = "Transferred"
	remote: bool = True

	@abstractmethod
	def export(self, batch, bookings: list) -> ExportResult: ...

	def check_booking(self, booking) -> str | None:
		return None


class MasterDataConnector(Connector, ABC):
	creates_via_export: bool = False
	can_fetch: bool = True

	@abstractmethod
	def fetch_parties(self) -> list[RemoteParty]: ...

	@abstractmethod
	def next_number(self, kind: str) -> int: ...

	@abstractmethod
	def create_party(self, party_account) -> None: ...


def read_document_file(document) -> bytes:
	content = frappe.get_doc("File", {"file_url": document.file}).get_content()
	return content.encode() if isinstance(content, str) else content
