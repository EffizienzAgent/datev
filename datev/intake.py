import frappe
from frappe import _

from datev.beleglink import new_guid
from datev.document_types import DocumentTypeError, resolve
from datev.documents import enqueue_upload
from datev.events import log_event
from datev.protect import internal

DOCUMENT = "DATEV Document"
PARTY_ACCOUNT = "DATEV Party Account"
BOOKING = "DATEV Booking"
OPEN_BOOKING_STATUSES = ("Pending", "Stuck", "Failed")


def upsert_document(
	client: str,
	source_key: str,
	*,
	direction: str,
	file_name: str,
	content: bytes,
	document_date,
	document_number: str,
	amount,
	currency: str = "EUR",
	source_doctype: str | None = None,
	source_name: str | None = None,
	is_return: bool = False,
) -> str:
	existing = frappe.db.get_value(
		DOCUMENT, {"source_key": source_key}, ["name", "status", "client"], as_dict=True
	)
	if existing and existing.client != client:
		frappe.throw(_("The source key {0} already belongs to another client.").format(source_key))
	if existing and existing.status not in ("Pending", "Stuck"):
		return existing.name

	document = frappe.get_doc(DOCUMENT, existing.name) if existing else frappe.new_doc(DOCUMENT)
	document.update(
		{
			"client": client,
			"source_key": source_key,
			"direction": direction,
			"file_name": file_name,
			"document_date": document_date,
			"document_number": document_number,
			"amount": amount,
			"currency": currency,
			"source_doctype": source_doctype,
			"source_name": source_name,
			"is_return": is_return,
		}
	)
	internal(document)
	document.guid = document.guid or new_guid()
	try:
		document.document_type = resolve(
			frappe.get_doc("DATEV Client", client), direction, source_doctype, is_return
		)
		document.status, document.message, document.attempts = "Pending", "", 0
		document.next_attempt = None
	except DocumentTypeError as error:
		document.status, document.message = "Stuck", str(error)
	document.save(ignore_permissions=True)

	replace_file(document, file_name, content)

	if document.status == "Pending" and frappe.db.get_value("DATEV Client", client, "enabled"):
		enqueue_upload(document.name, frappe.get_doc("DATEV Client", client))
	return document.name


def replace_file(document, file_name: str, content: bytes) -> None:
	for old in frappe.get_all(
		"File", filters={"attached_to_doctype": DOCUMENT, "attached_to_name": document.name}, pluck="name"
	):
		frappe.delete_doc("File", old, ignore_permissions=True)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
			"attached_to_doctype": DOCUMENT,
			"attached_to_name": document.name,
		}
	).insert(ignore_permissions=True)
	document.db_set("file", file.file_url)


def discard_document(source_key: str) -> None:
	name = frappe.db.get_value(DOCUMENT, {"source_key": source_key}, "name")
	if not name:
		frappe.throw(_("No document with source key {0}.").format(source_key), frappe.DoesNotExistError)
	status = frappe.db.get_value(DOCUMENT, name, "status", for_update=True)
	if status in ("Uploaded", "No Link"):
		frappe.throw(_("The document is already in DATEV. Create a cancellation invoice instead."))
	if status == "Discarded":
		return
	frappe.db.set_value(DOCUMENT, name, "status", "Discarded")
	log_event(DOCUMENT, name, "discarded")
	for booking in frappe.get_all(
		BOOKING, filters={"document": name, "status": ("in", OPEN_BOOKING_STATUSES)}, pluck="name"
	):
		frappe.delete_doc("DATEV Booking", booking, ignore_permissions=True)


def add_booking(
	document: str,
	line_key: str,
	*,
	kind: str,
	party_account: str,
	contra_account: int,
	amount,
	debit_credit: str,
	tax_rate: float = 0,
	bu_key: str = "",
	posting_text: str = "",
	due_date=None,
	tax_code: str | None = None,
) -> str:
	existing = frappe.db.get_value(
		BOOKING,
		{"document": document, "line_key": line_key},
		["name", "status"],
		as_dict=True,
		for_update=True,
	)
	if existing and existing.status not in OPEN_BOOKING_STATUSES:
		return existing.name

	source = frappe.get_doc(DOCUMENT, document)
	if source.status == "Discarded":
		frappe.throw(_("Document {0} is discarded and takes no bookings.").format(document))
	if tax_code:
		mapped = tax_key(source.client, tax_code)
		bu_key = bu_key or mapped
	booking = internal(frappe.get_doc(BOOKING, existing.name) if existing else frappe.new_doc(BOOKING))
	booking.update(
		{
			"client": source.client,
			"document": document,
			"line_key": line_key,
			"kind": kind,
			"party_account": party_account,
			"contra_account": contra_account,
			"amount": amount,
			"debit_credit": debit_credit,
			"tax_rate": tax_rate,
			"bu_key": bu_key or "",
			"posting_text": posting_text,
			"due_date": due_date,
			"currency": source.currency,
			"document_date": source.document_date,
			"document_field_1": source.document_number,
		}
	)
	booking.save(ignore_permissions=True)
	return booking.name


def tax_key(client: str, tax_code: str) -> str:
	row = frappe.db.get_value(
		"DATEV Tax Key Rule",
		{"parent": client, "parenttype": "DATEV Client", "tax_code": tax_code},
		"bu_key",
		as_dict=True,
	)
	if row is None:
		frappe.throw(
			_(
				"Tax code {0} has no row in the DATEV Client's tax keys. "
				"Add it; leave the BU key empty for automatic accounts."
			).format(tax_code)
		)
	return row.bu_key or ""


def ensure_party_account(
	client: str,
	kind: str,
	*,
	party_type: str | None,
	party: str | None,
	party_name: str,
	vat_id: str | None = None,
	iban: str | None = None,
	preferred_number: int | None = None,
) -> str:
	filters = {"client": client, "kind": kind}
	if party_type and party:
		filters.update({"party_type": party_type, "party": party})
	else:
		filters.update({"party": ("is", "not set"), "party_name": party_name})
	name = frappe.db.get_value(PARTY_ACCOUNT, filters, "name")
	if name:
		account = frappe.get_doc(PARTY_ACCOUNT, name)
	else:
		account = frappe.new_doc(PARTY_ACCOUNT)
		account.update(
			{"client": client, "kind": kind, "party_type": party_type, "party": party, "sync_status": "New"}
		)
	account.update(
		{"party_name": party_name, "vat_id": vat_id, "iban": iban, "preferred_number": preferred_number}
	)
	account.save(ignore_permissions=True)
	return account.name
