from datetime import datetime, time
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from datev.clients.datevconnect import DATEVconnect, DATEVconnectError
from datev.connectors.base import (
	ConnectorError,
	ExportConnector,
	ExportItemResult,
	ExportResult,
	MasterDataConnector,
	RemoteParty,
)
from datev.extf.format import document_field
from datev.fiscal import fiscal_year_id

TECHNICAL_GL_LENGTH = 8
DIRECTIONS = {"Outgoing Invoice": "outgoing", "Incoming Invoice": "incoming"}
PARTY_FIELDS = {"Outgoing Invoice": "debitor_account_number", "Incoming Invoice": "creditor_account_number"}
POSITIVE = {("Outgoing Invoice", "S"), ("Incoming Invoice", "H")}
PARTY_KINDS = {"Debtor": "debitors", "Creditor": "creditors"}
DATEV_TIMEZONE = ZoneInfo("Europe/Berlin")


def technical_factor(client) -> int:
	return 10 ** (TECHNICAL_GL_LENGTH - (client.account_length or 4))


def document_datetime(value) -> str:
	return datetime.combine(getdate(value), time(), DATEV_TIMEZONE).isoformat()


def proposal_item(booking, document, party_account, client) -> dict:
	if not (party_account and party_account.account_number):
		raise ConnectorError(_("Booking {0} has no party account number.").format(booking.name))
	if not booking.contra_account:
		raise ConnectorError(_("Booking {0} has no contra account.").format(booking.name))
	if booking.bu_key and not str(booking.bu_key).isdigit():
		raise ConnectorError(_("Booking {0} has an invalid BU key.").format(booking.name))
	factor = technical_factor(client)
	mappings = {row.source_account: row.datev_account for row in client.account_mappings}
	contra = mappings.get(booking.contra_account, booking.contra_account)
	amount = flt(booking.amount, 2)
	item = {
		"amount": amount if (booking.kind, booking.debit_credit) in POSITIVE else -amount,
		"date": document_datetime(booking.document_date),
		PARTY_FIELDS[booking.kind]: party_account.account_number * factor,
		"account_number": contra * factor,
		"accounting_transaction_key": int(booking.bu_key) if booking.bu_key else None,
		"tax_rate": booking.tax_rate or None,
		"document_field1": document_field(booking.document_field_1),
		"due_date": getdate(booking.due_date).isoformat() if booking.due_date else None,
		"name": (party_account.party_name or "")[:40],
		"posting_description": (booking.posting_text or "")[:60],
		"currency_code": booking.currency,
	}
	if document and document.external_id:
		item["document_system"] = document.program_code
		item["document_link"] = document.external_id.upper()
	return {key: value for key, value in item.items() if value not in (None, "")}


def messages(entry: dict) -> str:
	return "; ".join(
		f"{message.get('id')}: {message.get('description')}"
		if message.get("id")
		else message.get("description")
		for message in entry.get("additional_messages") or []
		if message.get("description")
	)


def item_results(bookings: list, response) -> tuple[list[ExportItemResult], list[str]]:
	everything = [booking.name for booking in bookings]
	if not isinstance(response, dict):
		return [], everything
	try:
		succeeded = {str(entry.get("index")): messages(entry) for entry in response.get("succeeded") or []}
		failed = {str(entry.get("index")): messages(entry) for entry in response.get("failed") or []}
	except AttributeError, TypeError:
		return [], everything
	results, uncertain = [], []
	for index, booking in enumerate(bookings):
		key = str(index)
		if key in succeeded:
			results.append(ExportItemResult(booking.name, True, succeeded[key]))
		elif key in failed:
			results.append(
				ExportItemResult(booking.name, False, failed[key] or _("Rejected by DATEVconnect."))
			)
		else:
			uncertain.append(booking.name)
	return results, uncertain


class DATEVconnectMixin:
	def api(self) -> DATEVconnect:
		return DATEVconnect(
			self.client.dc_base_url,
			self.client.dc_username,
			self.client.get_password("dc_password"),
			verify=bool(self.client.dc_verify_tls),
		)

	def client_id(self, api: DATEVconnect) -> str:
		if not self.client.dc_client_id:
			client_id = api.client_id_for(self.client.client_number, self.client.consultant_number)
			frappe.db.set_value("DATEV Client", self.client.name, "dc_client_id", client_id)
			self.client.dc_client_id = client_id
		return self.client.dc_client_id


class DATEVconnectExport(DATEVconnectMixin, ExportConnector):
	def export(self, batch, bookings: list) -> ExportResult:
		try:
			api = self.api()
			client_id = self.client_id(api)
			documents = self.load("DATEV Document", {booking.document for booking in bookings})
			parties = self.load("DATEV Party Account", {booking.party_account for booking in bookings})
		except ConnectorError as error:
			error.nothing_sent = True
			raise
		except Exception as error:
			raise ConnectorError(str(error), nothing_sent=True) from error

		results = {}
		groups = {}
		for booking in bookings:
			try:
				if booking.kind not in DIRECTIONS:
					raise ConnectorError(_("Payments are not supported yet"))
				item = proposal_item(
					booking, documents.get(booking.document), parties.get(booking.party_account), self.client
				)
			except ConnectorError as e:
				results[booking.name] = ExportItemResult(booking.name, False, str(e))
				continue
			groups.setdefault((booking.fiscal_year, DIRECTIONS[booking.kind]), []).append((booking, item))

		responses = []
		uncertain = []
		queue = list(groups.items())
		for position, ((fiscal_year, direction), entries) in enumerate(queue):
			group = [booking for booking, _item in entries]
			try:
				response = api.post_proposals(
					client_id, fiscal_year, direction, [item for _b, item in entries]
				)
				responses.append(response)
				mapped, unmapped = item_results(group, response)
			except DATEVconnectError as e:
				responses.append({"error": str(e), "response": e.response})
				if e.maybe_sent or (e.status or 0) >= 500:
					uncertain.extend(booking.name for booking in group)
				else:
					results.update(
						{booking.name: ExportItemResult(booking.name, False, str(e)) for booking in group}
					)
				continue
			except Exception as e:
				frappe.log_error(
					title="DATEVconnect export failed",
					reference_doctype="DATEV Batch",
					reference_name=batch.name,
				)
				responses.append({"error": str(e)})
				uncertain.extend(booking.name for booking in group)
				not_sent = _("Not sent because an earlier request failed.")
				for _key, rest in queue[position + 1 :]:
					results.update({b.name: ExportItemResult(b.name, False, not_sent) for b, _item in rest})
				break
			results.update({result.booking: result for result in mapped})
			uncertain.extend(unmapped)
		return ExportResult(
			items=[results[booking.name] for booking in bookings if booking.name in results],
			response=responses,
			uncertain=uncertain,
		)

	@staticmethod
	def load(doctype: str, names: set) -> dict:
		return {name: frappe.get_doc(doctype, name) for name in names if name}


class DATEVconnectMasterData(DATEVconnectMixin, MasterDataConnector):
	def current_fiscal_year(self) -> str:
		return fiscal_year_id(self.client.name, getdate(today()))

	def fetch_parties(self) -> list[RemoteParty]:
		api = self.api()
		client_id = self.client_id(api)
		fiscal_year = self.current_fiscal_year()
		factor = technical_factor(self.client)
		return [
			remote_party(kind, row, factor)
			for kind, path in PARTY_KINDS.items()
			for row in api.parties(client_id, fiscal_year, path) or []
			if row.get("account_number")
		]

	def next_number(self, kind: str) -> int:
		api = self.api()
		number = api.next_available(self.client_id(api), self.current_fiscal_year(), PARTY_KINDS[kind])
		return int(number) // technical_factor(self.client)

	def create_party(self, party_account) -> None:
		if not party_account.account_number:
			raise ConnectorError(_("Party account {0} has no account number.").format(party_account.name))
		api = self.api()
		client_id = self.client_id(api)
		years = [year["id"] for year in api.fiscal_years(client_id) if not year.get("is_locked")]
		if not years:
			raise ConnectorError(_("DATEVconnect has no open fiscal year for this client."))
		payload = party_payload(party_account, technical_factor(self.client))
		for fiscal_year in years:
			response = api.put_party(client_id, fiscal_year, PARTY_KINDS[party_account.kind], [payload])
			failed = (response or {}).get("failed") or []
			if failed:
				message = messages(failed[0]) or _("Rejected by DATEVconnect.")
				raise ConnectorError(_("Fiscal year {0}: {1}").format(fiscal_year, message))


def party_payload(party_account, factor: int) -> dict:
	name = party_account.party_name or ""
	payload = {
		"id": str(party_account.account_number * factor),
		"legal_entity_type": "legal_person",
		"legal_person": {"legal_name": name[:40]},
		"short_name": name[:15],
	}
	iban = "".join((party_account.iban or "").split()).upper()
	if iban:
		payload["banks"] = [{"iban": iban, "country_code": iban[:2]}]
	vat_id = "".join((party_account.vat_id or "").split()).upper()
	if vat_id:
		payload["eu_vat_id_country_code"] = vat_id[:2]
		payload["eu_vat_id_number"] = vat_id[2:15]
	return payload


def remote_party(kind: str, row: dict, factor: int) -> RemoteParty:
	vat_id = (row.get("eu_vat_id_country_code") or "") + (row.get("eu_vat_id_number") or "")
	ibans = [bank.get("iban") for bank in row.get("banks") or [] if bank.get("iban")]
	return RemoteParty(
		kind=kind,
		account_number=int(row["account_number"]) // factor,
		name=party_name(row),
		vat_id=vat_id or None,
		iban=ibans[0] if ibans else None,
	)


def party_name(row: dict) -> str:
	person = row.get("natural_person") or {}
	full_name = " ".join(part for part in (person.get("firstname"), person.get("surname")) if part)
	return (
		row.get("caption")
		or (row.get("legal_person") or {}).get("legal_name")
		or full_name
		or row.get("short_name")
		or ""
	)
