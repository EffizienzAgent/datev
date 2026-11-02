import re

import frappe
from frappe import _

from datev.connectors.base import ConnectorError, RemoteParty
from datev.connectors.registry import get_master_data
from datev.events import log_event

PARTY = "DATEV Party Account"
SETTLED = ("Imported", "Created")
CACHE_KEY = "datev_remote_parties"
LEGAL_FORM_SUFFIX = re.compile(r"(?:^|[\s,\-])(?:gmbh|mbh|e\.\s*k\.?|ug|ag|kg|ohg|gbr|&\s*co\.?)[\s.,]*$")
NUMBERING_FIELDS = {"Debtor": "debtor_numbering", "Creditor": "creditor_numbering"}
MATCH_BY_NUMBER_FIELDS = {"Debtor": "match_debtors_by_number", "Creditor": "match_creditors_by_number"}


def normalize_name(name: str | None) -> str:
	value = (name or "").casefold().strip()
	while (stripped := LEGAL_FORM_SUFFIX.sub("", value).strip()) != value:
		value = stripped
	return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def compact(value: str | None) -> str:
	return "".join((value or "").split()).upper()


def clear_remote_cache() -> None:
	frappe.local.datev_remote_parties = {}


def remote_parties(client) -> list[RemoteParty]:
	cache = getattr(frappe.local, CACHE_KEY, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, CACHE_KEY, cache)
	if client.name not in cache:
		cache[client.name] = get_master_data(client).fetch_parties()
	return cache[client.name]


def assign_number(party_account) -> int:
	if party_account.account_number:
		return party_account.account_number
	client = frappe.get_doc("DATEV Client", party_account.client)
	if client.get(NUMBERING_FIELDS[party_account.kind]) == "DATEV next available":
		number = get_master_data(client).next_number(party_account.kind)
	elif party_account.preferred_number:
		number = party_account.preferred_number
	else:
		frappe.throw(
			_("Party account {0} has no preferred number from the source system.").format(party_account.name),
			frappe.ValidationError,
		)
	party_account.account_number = int(number)
	return party_account.account_number


def ensure_in_datev(party_account) -> bool:
	if party_account.sync_status in ("To Clarify", "Error"):
		return False
	if party_account.sync_status in SETTLED:
		return True
	try:
		client = frappe.get_doc("DATEV Client", party_account.client)
		connector = get_master_data(client)
		assign_number(party_account)
		party_account.save(ignore_permissions=True)
		if connector.creates_via_export:
			return True
		if connector.can_fetch:
			remote = find_remote(remote_parties(client), party_account.kind, party_account.account_number)
			if remote:
				return link_existing(party_account, remote)
		connector.create_party(party_account)
		if connector.can_fetch:
			remote_parties(client).append(
				RemoteParty(party_account.kind, party_account.account_number, party_account.party_name)
			)
		set_status(party_account, "Created", "", "created")
		return True
	except (ConnectorError, frappe.ValidationError) as error:
		party_account.db_set({"sync_status": "Error", "message": str(error)})
		log_event(PARTY, party_account.name, "error", str(error))
		return False


def find_remote(remote: list[RemoteParty], kind: str, number: int) -> RemoteParty | None:
	return next((party for party in remote if party.kind == kind and party.account_number == number), None)


def link_existing(party_account, remote: RemoteParty) -> bool:
	if normalize_name(remote.name) == normalize_name(party_account.party_name):
		set_status(party_account, "Imported", "", "imported")
		return True
	message = _("DATEV account {0} already exists as {1}. Check the account and resolve it manually.").format(
		remote.account_number, remote.name
	)
	set_status(party_account, "To Clarify", message, "to_clarify")
	return False


def set_status(party_account, status: str, message: str, event: str) -> None:
	party_account.sync_status = status
	party_account.message = message
	party_account.save(ignore_permissions=True)
	log_event(PARTY, party_account.name, event, message)


def import_existing(client: str, remote: list[RemoteParty] | None = None) -> dict:
	client_doc = frappe.get_doc("DATEV Client", client)
	if remote is None:
		remote = get_master_data(client_doc).fetch_parties()
	linked = {
		(row.kind, row.account_number): row.name
		for row in frappe.get_all(
			PARTY,
			filters={"client": client, "account_number": (">", 0)},
			fields=["name", "kind", "account_number"],
		)
	}
	result = {"imported": 0, "to_clarify": 0, "unmatched": 0}
	for name in frappe.get_all(
		PARTY,
		filters={"client": client, "sync_status": ("not in", SETTLED)},
		pluck="name",
		order_by="creation",
	):
		party_account = frappe.get_doc(PARTY, name)
		candidates = match_candidates(client_doc, party_account, remote)
		if not candidates:
			result["unmatched"] += 1
			continue
		if len(candidates) > 1:
			message = _("Several DATEV accounts match: {0}").format(describe(candidates))
			mark_to_clarify(party_account, message, result)
			continue
		candidate = candidates[0]
		owner = linked.get((candidate.kind, candidate.account_number))
		if owner and owner != party_account.name:
			message = _("DATEV account {0} is already linked to {1}.").format(describe(candidates), owner)
			mark_to_clarify(party_account, message, result)
			continue
		try:
			party_account.account_number = candidate.account_number
			set_status(party_account, "Imported", "", "imported")
		except frappe.ValidationError as error:
			party_account.reload()
			mark_to_clarify(party_account, str(error), result)
			continue
		linked[(candidate.kind, candidate.account_number)] = party_account.name
		result["imported"] += 1
	return result


def match_candidates(client, party_account, remote: list[RemoteParty]) -> list[RemoteParty]:
	same_kind = [party for party in remote if party.kind == party_account.kind]
	if client.get(MATCH_BY_NUMBER_FIELDS[party_account.kind]) and party_account.preferred_number:
		by_number = [p for p in same_kind if p.account_number == party_account.preferred_number]
		if by_number:
			return by_number
	for key, normalize in (("vat_id", compact), ("iban", compact), ("party_name", normalize_name)):
		value = normalize(party_account.get(key))
		if not value:
			continue
		remote_key = "name" if key == "party_name" else key
		matches = [p for p in same_kind if normalize(getattr(p, remote_key)) == value]
		if matches:
			return matches
	return []


def describe(candidates: list[RemoteParty]) -> str:
	return ", ".join(f"{party.account_number} {party.name}" for party in candidates)


def mark_to_clarify(party_account, message: str, result: dict) -> None:
	party_account.db_set({"sync_status": "To Clarify", "message": message})
	log_event(PARTY, party_account.name, "to_clarify", message)
	result["to_clarify"] += 1
