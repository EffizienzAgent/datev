"""Fills a development site with a realistic demo: bench --site <site> execute datev.demo.create

Nothing talks to DATEV or Business OS. Document uploads and the party import are recorded directly,
the exported batch is built by the EXTF connector.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from datev import dispatch, intake
from datev.beleglink import new_guid
from datev.connectors.base import RemoteParty
from datev.parties import record_import
from datev.protect import internal

TITLE = "Mustermann Handels GmbH"
CONSULTANT, CLIENT_NUMBER = 29098, 55003

DEBTORS = {
	"hansen": ("Hansen Elektrotechnik GmbH", 10001),
	"nordwind": ("Nordwind Logistik AG", 10002),
	"kaiser": ("Kaiser Gastronomie GmbH", 10003),
	"berger": ("Berger & Söhne Bau OHG", 10004),
	"altmann": ("Altmann Metallbau GmbH", 10005),
	"lindner": ("Lindner Textil KG", 10006),
}


def pdf(text: str) -> bytes:
	stream = f"BT /F1 14 Tf 20 60 Td ({text}) Tj ET".encode()
	objects = [
		b"<</Type/Catalog/Pages 2 0 R>>",
		b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
		b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
		b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
		b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
	]
	out, offsets = b"%PDF-1.4\n", []
	for number, body in enumerate(objects, start=1):
		offsets.append(len(out))
		out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
	out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
	return out + b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def create() -> str:
	existing = frappe.db.get_value("DATEV Client", {"title": TITLE})
	if existing:
		return existing
	set_default_currency()
	client = make_client()
	record_import(client.name, [RemoteParty("Debtor", 10020, "Weber Druckerei e.K.")])
	parties = {key: make_party(client.name, *values) for key, values in DEBTORS.items()}
	parties["berger"] = flag_to_clarify(parties["berger"])

	invoice(client, "RE-2026-1041", "2026-10-05", parties["hansen"], [(8400, 1190, 19), (8300, 214, 7)])
	invoice(client, "RE-2026-1042", "2026-10-12", parties["nordwind"], [(8400, 2380, 19)])
	export_first_batch(client.name)

	invoice(client, "RE-2026-1043", "2026-10-20", parties["kaiser"], [(8400, 595, 19)])
	invoice(client, "RE-2026-1044", "2026-10-22", parties["hansen"], [(8300, 107, 7)])
	invoice(client, "ST-2026-0007", "2026-10-26", parties["nordwind"], [(8400, 2380, 19)], cancellation=True)
	invoice(client, "RE-2026-1045", "2026-10-28", parties["altmann"], [(8400, 1785, 19)], stuck=True)
	invoice(client, "RE-2026-1046", "2026-10-29", parties["berger"], [(8400, 4165, 19)])
	dispatch.collect(client.name)
	held = invoice(client, "RE-2026-1047", "2026-10-30", parties["lindner"], [(8400, 357, 19)])
	dispatch.hold(held)
	age_dispatch(client.name)
	frappe.db.commit()
	return client.name


def set_default_currency() -> None:
	frappe.db.set_single_value("Global Defaults", "default_currency", "EUR")
	frappe.db.set_default("currency", "EUR")


def make_client():
	return frappe.get_doc(
		{
			"doctype": "DATEV Client",
			"title": TITLE,
			"consultant_number": CONSULTANT,
			"client_number": CLIENT_NUMBER,
			"chart": "SKR04",
			"account_length": 4,
			"storage_connector": "Business OS",
			"bos_api_key": "demo-key",
			"bos_connection_id": "6f1c2a9e-0d4b-4c58-9e1a-3b7d52f0a1c4",
			"bos_credits": 842,
			"bos_credits_updated_on": now_datetime(),
			"export_connector": "EXTF download",
			"master_data_connector": "EXTF",
			"dispatch_frequency": "Weekly",
			"dispatch_weekday": "Monday",
			"approval_required": 1,
			"responsible_role": "DATEV Manager",
			"debtor_numbering": "Source system",
			"creditor_numbering": "Source system",
			"tax_keys": [{"tax_code": "DE 19%", "bu_key": ""}, {"tax_code": "DE 7%", "bu_key": ""}],
			"account_mappings": [
				{"source_account": 4000, "datev_account": 8400},
				{"source_account": 4010, "datev_account": 8300},
			],
		}
	).insert()


def make_party(client: str, name: str, number: int) -> str:
	return intake.ensure_party_account(
		client, "Debtor", party_type=None, party=None, party_name=name, preferred_number=number
	)


def flag_to_clarify(name: str) -> str:
	frappe.db.set_value(
		"DATEV Party Account",
		name,
		{
			"sync_status": "To Clarify",
			"message": "Several DATEV accounts match: 10040 Berger Bau OHG; 10041 Berger und Söhne Bau OHG",
		},
	)
	return name


def invoice(client, number, date, party, lines, cancellation=False, stuck=False) -> str:
	gross = sum(amount for _, amount, _ in lines)
	document = internal(
		frappe.get_doc(
			{
				"doctype": "DATEV Document",
				"client": client.name,
				"source_key": number,
				"direction": "Outgoing",
				"document_type": "Rechnungsausgang",
				"is_return": int(cancellation),
				"file_name": f"{number}.pdf",
				"document_date": date,
				"document_number": number,
				"amount": gross,
				"guid": new_guid(),
			}
		)
	).insert(ignore_permissions=True)
	intake.replace_file(document, f"{number}.pdf", pdf(f"Invoice {number}"))
	if stuck:
		document.db_set(
			{
				"status": "Stuck",
				"attempts": 5,
				"message": "Business OS did not answer within 60 seconds. Check DUo before retrying.",
			}
		)
	else:
		document.db_set({"status": "Uploaded", "external_id": document.guid, "program_code": "BEDI"})
	for index, (account, amount, rate) in enumerate(lines, start=1):
		intake.add_booking(
			document.name,
			str(index),
			kind="Outgoing Invoice",
			party_account=party,
			contra_account=account,
			amount=amount,
			debit_credit="H" if cancellation else "S",
			tax_rate=rate,
			posting_text=("Stornorechnung " if cancellation else "Rechnung ") + number,
			tax_code=f"DE {rate}%",
		)
	return frappe.db.get_value("DATEV Booking", {"document": document.name}, "name")


def export_first_batch(client: str) -> None:
	batch = dispatch.collect(client)
	frappe.db.set_value(
		"DATEV Batch",
		batch,
		{"status": "Approved", "approved_by": "Administrator", "approved_on": now_datetime()},
	)
	dispatch.send(batch)


def age_dispatch(client: str) -> None:
	frappe.db.set_value("DATEV Client", client, "last_dispatch_on", add_to_date(now_datetime(), days=-9))
