from datetime import datetime

import frappe
from frappe import _
from frappe.utils import getdate

from datev.beleglink import extf_value
from datev.extf import header
from datev.extf.format import amount, ddmm, document_field, encode, text, yyyymmdd

COLUMNS = (
	"Umsatz (ohne Soll/Haben-Kz)",
	"Soll/Haben-Kennzeichen",
	"WKZ Umsatz",
	"Kurs",
	"Basis-Umsatz",
	"WKZ Basis-Umsatz",
	"Konto",
	"Gegenkonto (ohne BU-Schlüssel)",
	"BU-Schlüssel",
	"Belegdatum",
	"Belegfeld 1",
	"Belegfeld 2",
	"Skonto",
	"Buchungstext",
	"Postensperre",
	"Diverse Adressnummer",
	"Geschäftspartnerbank",
	"Sachverhalt",
	"Zinssperre",
	"Beleglink",
	"Beleginfo - Art 1",
	"Beleginfo - Inhalt 1",
	"Beleginfo - Art 2",
	"Beleginfo - Inhalt 2",
	"Beleginfo - Art 3",
	"Beleginfo - Inhalt 3",
	"Beleginfo - Art 4",
	"Beleginfo - Inhalt 4",
	"Beleginfo - Art 5",
	"Beleginfo - Inhalt 5",
	"Beleginfo - Art 6",
	"Beleginfo - Inhalt 6",
	"Beleginfo - Art 7",
	"Beleginfo - Inhalt 7",
	"Beleginfo - Art 8",
	"Beleginfo - Inhalt 8",
	"KOST1 - Kostenstelle",
	"KOST2 - Kostenstelle",
	"Kost-Menge",
	"EU-Land u. UStID (Bestimmung)",
	"EU-Steuersatz (Bestimmung)",
	"Abw. Versteuerungsart",
	"Sachverhalt L+L",
	"Funktionsergänzung L+L",
	"BU 49 Hauptfunktionstyp",
	"BU 49 Hauptfunktionsnummer",
	"BU 49 Funktionsergänzung",
	"Zusatzinformation - Art 1",
	"Zusatzinformation- Inhalt 1",
	"Zusatzinformation - Art 2",
	"Zusatzinformation- Inhalt 2",
	"Zusatzinformation - Art 3",
	"Zusatzinformation- Inhalt 3",
	"Zusatzinformation - Art 4",
	"Zusatzinformation- Inhalt 4",
	"Zusatzinformation - Art 5",
	"Zusatzinformation- Inhalt 5",
	"Zusatzinformation - Art 6",
	"Zusatzinformation- Inhalt 6",
	"Zusatzinformation - Art 7",
	"Zusatzinformation- Inhalt 7",
	"Zusatzinformation - Art 8",
	"Zusatzinformation- Inhalt 8",
	"Zusatzinformation - Art 9",
	"Zusatzinformation- Inhalt 9",
	"Zusatzinformation - Art 10",
	"Zusatzinformation- Inhalt 10",
	"Zusatzinformation - Art 11",
	"Zusatzinformation- Inhalt 11",
	"Zusatzinformation - Art 12",
	"Zusatzinformation- Inhalt 12",
	"Zusatzinformation - Art 13",
	"Zusatzinformation- Inhalt 13",
	"Zusatzinformation - Art 14",
	"Zusatzinformation- Inhalt 14",
	"Zusatzinformation - Art 15",
	"Zusatzinformation- Inhalt 15",
	"Zusatzinformation - Art 16",
	"Zusatzinformation- Inhalt 16",
	"Zusatzinformation - Art 17",
	"Zusatzinformation- Inhalt 17",
	"Zusatzinformation - Art 18",
	"Zusatzinformation- Inhalt 18",
	"Zusatzinformation - Art 19",
	"Zusatzinformation- Inhalt 19",
	"Zusatzinformation - Art 20",
	"Zusatzinformation- Inhalt 20",
	"Stück",
	"Gewicht",
	"Zahlweise",
	"Forderungsart",
	"Veranlagungsjahr",
	"Zugeordnete Fälligkeit",
	"Skontotyp",
	"Auftragsnummer",
	"Buchungstyp",
	"USt-Schlüssel (Anzahlungen)",
	"EU-Land (Anzahlungen)",
	"Sachverhalt L+L (Anzahlungen)",
	"EU-Steuersatz (Anzahlungen)",
	"Erlöskonto (Anzahlungen)",
	"Herkunft-Kz",
	"Buchungs GUID",
	"KOST-Datum",
	"SEPA-Mandatsreferenz",
	"Skontosperre",
	"Gesellschaftername",
	"Beteiligtennummer",
	"Identifikationsnummer",
	"Zeichnernummer",
	"Postensperre bis",
	"Bezeichnung SoBil-Sachverhalt",
	"Kennzeichen SoBil-Buchung",
	"Festschreibung",
	"Leistungsdatum",
	"Datum Zuord. Steuerperiode",
	"Fälligkeit",
	"Generalumkehr (GU)",
	"Steuersatz",
	"Land",
	"Abrechnungsreferenz",
	"BVV-Position",
	"EU-Land u. UStID (Ursprung)",
	"EU-Steuersatz (Ursprung)",
	"Abw. Skontokonto",
)


def build(client, bookings: list, created: datetime, description: str) -> list[tuple[str, bytes]]:
	mappings = {row.source_account: row.datev_account for row in client.account_mappings}
	groups = {}
	for booking in bookings:
		groups.setdefault(booking.fiscal_year, []).append(booking)
	return [
		_file(client, fiscal_year, group, mappings, created, description)
		for fiscal_year, group in sorted(groups.items())
	]


def _file(client, fiscal_year, bookings, mappings, created, description):
	dates = [getdate(booking.document_date) for booking in bookings]
	date_from, date_to = min(dates), max(dates)
	header_row = header.row(
		client,
		category=21,
		format_name="Buchungsstapel",
		format_version=13,
		created=created,
		fiscal_year_start=datetime.strptime(fiscal_year, "%Y%m%d").date(),
		date_from=date_from,
		date_to=date_to,
		description=description,
	)
	account_numbers = _account_numbers(bookings)
	documents = _documents(bookings)
	lines = [header_row, ";".join(COLUMNS)]
	lines += [_line(booking, account_numbers, documents, mappings) for booking in bookings]
	name = f"EXTF_Buchungsstapel_{client.client_number}_{yyyymmdd(date_from)}_{yyyymmdd(date_to)}.csv"
	return name, encode(lines)


def _account_numbers(bookings):
	names = {booking.party_account for booking in bookings if booking.party_account}
	if not names:
		return {}
	return dict(
		frappe.get_all(
			"DATEV Party Account",
			filters={"name": ("in", names)},
			fields=["name", "account_number"],
			as_list=True,
		)
	)


def _documents(bookings):
	rows = frappe.get_all(
		"DATEV Document",
		filters={"name": ("in", {booking.document for booking in bookings})},
		fields=["name", "program_code", "external_id"],
	)
	return {row.name: row for row in rows}


def _optional_text(value, max_len=None):
	return text(value, max_len) if value else ""


def _line(booking, account_numbers, documents, mappings):
	if booking.account:
		account = mappings.get(booking.account, booking.account)
	else:
		account = account_numbers.get(booking.party_account)
	if not account:
		frappe.throw(_("Booking {0} has no party account number.").format(booking.name))
	document = documents.get(booking.document)
	beleglink = ""
	if document and document.external_id:
		beleglink = text(extf_value(document.program_code, document.external_id), 210)
	values = {
		"Umsatz (ohne Soll/Haben-Kz)": amount(booking.amount),
		"Soll/Haben-Kennzeichen": text(booking.debit_credit),
		"WKZ Umsatz": _optional_text(booking.currency),
		"Konto": str(account),
		"Gegenkonto (ohne BU-Schlüssel)": str(mappings.get(booking.contra_account, booking.contra_account)),
		"BU-Schlüssel": _optional_text(booking.bu_key),
		"Belegdatum": ddmm(getdate(booking.document_date)),
		"Belegfeld 1": _optional_text(document_field(booking.document_field_1)),
		"Buchungstext": _optional_text(booking.posting_text, 60),
		"Beleglink": beleglink,
		"Festschreibung": "0",
	}
	return ";".join(values.get(column, "") for column in COLUMNS)
