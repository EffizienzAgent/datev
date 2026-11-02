import frappe
from frappe import _

from datev.beleglink import new_guid
from datev.document_types import DocumentTypeError, resolve
from datev.events import log_event

DOCUMENT = "DATEV Document"
PARTY_ACCOUNT = "DATEV Party Account"


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
	existing = frappe.db.get_value(DOCUMENT, {"source_key": source_key}, ["name", "status"], as_dict=True)
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

	if document.status == "Pending":
		frappe.enqueue("datev.documents.upload", name=document.name, enqueue_after_commit=True, queue="short")
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
	frappe.db.get_value(DOCUMENT, name, "status", for_update=True)
	document = frappe.get_doc(DOCUMENT, name)
	if document.status in ("Uploaded", "No Link"):
		frappe.throw(_("The document is already in DATEV. Create a cancellation invoice instead."))
	if document.status == "Discarded":
		return
	document.db_set("status", "Discarded")
	log_event(DOCUMENT, document.name, "discarded")
	for booking in frappe.get_all(
		"DATEV Booking", filters={"document": document.name, "status": "Pending"}, pluck="name"
	):
		frappe.delete_doc("DATEV Booking", booking, ignore_permissions=True)


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
