import frappe

from datev.protect import internal


def make_client(**overrides):
	values = {
		"doctype": "DATEV Client",
		"title": f"Test Client {frappe.generate_hash(length=8)}",
		"consultant_number": 29098,
		"client_number": 55003,
		"chart": "SKR04",
		"storage_connector": "Business OS",
		"bos_api_key": "dummy-key",
		"bos_connection_id": "dummy-connection",
		"export_connector": "EXTF download",
		"master_data_connector": "EXTF",
		"creditor_numbering": "Source system",
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()


def make_document(client, **overrides):
	values = {
		"doctype": "DATEV Document",
		"client": client.name,
		"source_key": f"test-{frappe.generate_hash(length=10)}",
		"direction": "Outgoing",
		"document_date": "2027-01-05",
		"document_number": "RE-1",
		"amount": 119,
	}
	values.update(overrides)
	return internal(frappe.get_doc(values)).insert()


def make_party_account(client, kind="Debtor", **overrides):
	values = {
		"doctype": "DATEV Party Account",
		"client": client.name,
		"kind": kind,
		"party_name": f"Test Party {frappe.generate_hash(length=6)}",
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()


def make_booking(document, party_account, **overrides):
	values = {
		"doctype": "DATEV Booking",
		"client": document.client,
		"document": document.name,
		"line_key": "1",
		"kind": "Outgoing Invoice",
		"party_account": party_account.name,
		"contra_account": 8400,
		"debit_credit": "S",
		"amount": 119,
		"currency": "EUR",
		"document_date": document.document_date,
	}
	values.update(overrides)
	return internal(frappe.get_doc(values)).insert()
