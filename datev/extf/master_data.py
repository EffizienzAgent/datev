import codecs
import csv
import io
from datetime import date, datetime

import frappe
from frappe import _

from datev.connectors.base import RemoteParty
from datev.extf import header
from datev.extf.format import ENCODING, encode, text
from datev.fiscal import fiscal_year_start

COLUMNS = (
	"Konto",
	"Name (Adressatentyp Unternehmen)",
	"Unternehmensgegenstand",
	"Name (Adressatentyp natürl. Person)",
	"Vorname (Adressatentyp natürl. Person)",
	"Name (Adressatentyp keine Angabe)",
	"Adressatentyp",
	"Kurzbezeichnung",
	"EU-Land",
	"EU-UStID",
	"Anrede",
	"Titel / Akad. Grad",
	"Adelstitel",
	"Namensvorsatz",
	"Adressart",
	"Straße",
	"Postfach",
	"Postleitzahl",
	"Ort",
	"Land",
	"Versandzusatz",
	"Adresszusatz",
	"Abweichende Anrede",
	"Abw. Zustellbezeichnung 1",
	"Abw. Zustellbezeichnung 2",
	"Kennz. Korrespondenzadresse",
	"Adresse Gültig von",
	"Adresse Gültig bis",
	"Telefon",
	"Bemerkung (Telefon)",
	"Telefon Geschäftsleitung",
	"Bemerkung (Telefon GL)",
	"E-Mail",
	"Bemerkung (E-Mail)",
	"Internet",
	"Bemerkung (Internet)",
	"Fax",
	"Bemerkung (Fax)",
	"Sonstige",
	"Bemerkung (Sonstige)",
	"Bankleitzahl 1",
	"Bankbezeichnung 1",
	"Bankkonto-Nummer 1",
	"Länderkennzeichen 1",
	"IBAN-Nr. 1",
	"Leerfeld",
	"SWIFT-Code 1",
	"Abw. Kontoinhaber 1",
	"Kennz. Hauptbankverb. 1",
	"Bankverb. 1 Gültig von",
	"Bankverb. 1 Gültig bis",
	"Bankleitzahl 2",
	"Bankbezeichnung 2",
	"Bankkonto-Nummer 2",
	"Länderkennzeichen 2",
	"IBAN-Nr. 2",
	"Leerfeld",
	"SWIFT-Code 2",
	"Abw. Kontoinhaber 2",
	"Kennz. Hauptbankverb. 2",
	"Bankverb. 2 Gültig von",
	"Bankverb. 2 Gültig bis",
	"Bankleitzahl 3",
	"Bankbezeichnung 3",
	"Bankkonto-Nummer 3",
	"Länderkennzeichen 3",
	"IBAN-Nr. 3",
	"Leerfeld",
	"SWIFT-Code 3",
	"Abw. Kontoinhaber 3",
	"Kennz. Hauptbankverb. 3",
	"Bankverb. 3 Gültig von",
	"Bankverb. 2 Gültig bis",
	"Bankleitzahl 4",
	"Bankbezeichnung 4",
	"Bankkonto-Nummer 4",
	"Länderkennzeichen 4",
	"IBAN-Nr. 4",
	"Leerfeld",
	"SWIFT-Code 4",
	"Abw. Kontoinhaber 4",
	"Kennz. Hauptbankverb. 4",
	"Bankverb. 4 Gültig von",
	"Bankverb. 4 Gültig bis",
	"Bankleitzahl 5",
	"Bankbezeichnung 5",
	"Bankkonto-Nummer 5",
	"Länderkennzeichen 5",
	"IBAN-Nr. 5",
	"Leerfeld",
	"SWIFT-Code 5",
	"Abw. Kontoinhaber 5",
	"Kennz. Hauptbankverb. 5",
	"Bankverb. 5 Gültig von",
	"Bankverb. 5 Gültig bis",
	"Leerfeld",
	"Briefanrede",
	"Grußformel",
	"Kundennummer",
	"Steuernummer",
	"Sprache",
	"Ansprechpartner",
	"Vertreter",
	"Sachbearbeiter",
	"Diverse-Konto",
	"Ausgabeziel",
	"Währungssteuerung",
	"Kreditlimit (Debitor)",
	"Zahlungsbedingung",
	"Fälligkeit in Tagen (Debitor)",
	"Skonto in Prozent (Debitor)",
	"Kreditoren-Ziel 1 (Tage)",
	"Kreditoren-Skonto 1 (%)",
	"Kreditoren-Ziel 2 (Tage)",
	"Kreditoren-Skonto 2 (%)",
	"Kreditoren-Ziel 3 Brutto (Tage)",
	"Kreditoren-Ziel 4 (Tage)",
	"Kreditoren-Skonto 4 (%)",
	"Kreditoren-Ziel 5 (Tage)",
	"Kreditoren-Skonto 5 (%)",
	"Mahnung",
	"Kontoauszug",
	"Mahntext 1",
	"Mahntext 2",
	"Mahntext 3",
	"Kontoauszugstest",
	"Mahnlimit Betrag",
	"Mahnlimit %",
	"Zinsberechnung",
	"Mahnzinssatz 1",
	"Mahnzinssatz 2",
	"Mahnzinssatz 3",
	"Lastschrift",
	"Leerfeld",
	"Mandantenbank",
	"Zahlungsträger",
	"Indiv. Feld 1",
	"Indiv. Feld 2",
	"Indiv. Feld 3",
	"Indiv. Feld 4",
	"Indiv. Feld 5",
	"Indiv. Feld 6",
	"Indiv. Feld 7",
	"Indiv. Feld 8",
	"Indiv. Feld 9",
	"Indiv. Feld 10",
	"Indiv. Feld 11",
	"Indiv. Feld 12",
	"Indiv. Feld 13",
	"Indiv. Feld 14",
	"Indiv. Feld 15",
	"Abweichende Anrede (Rechnungsadresse)",
	"Adressart (Rechnungsadresse)",
	"Straße (Rechnungsadresse)",
	"Postfach (Rechnungsadresse)",
	"Postleitzahl (Rechnungsadresse)",
	"Ort (Rechnungsadresse)",
	"Land (Rechnungsadresse)",
	"Versandzusatz (Rechnungsadresse)",
	"Adresszusatz (Rechnungsadresse)",
	"Abw. Zustellbezeichnung 1 (Rechnungsadresse)",
	"Abw. Zustellbezeichnung 2 (Rechnungsadresse)",
	"Adresse Gültig von (Rechnungsadresse)",
	"Adresse Gültig bis (Rechnungsadresse)",
	"Bankleitzahl 6",
	"Bankbezeichnung 6",
	"Bankkonto-Nummer 6",
	"Länderkennzeichen 6",
	"IBAN-Nr. 6",
	"Leerfeld",
	"SWIFT-Code 6",
	"Abw. Kontoinhaber 6",
	"Kennz. Hauptbankverb. 6",
	"Bankverb. 6 Gültig von",
	"Bankverb. 6 Gültig bis",
	"Bankleitzahl 7",
	"Bankbezeichnung 7",
	"Bankkonto-Nummer 7",
	"Länderkennzeichen 7",
	"IBAN-Nr. 7",
	"Leerfeld",
	"SWIFT-Code 7",
	"Abw. Kontoinhaber 7",
	"Kennz. Hauptbankverb. 7",
	"Bankverb. 7 Gültig von",
	"Bankverb. 7 Gültig bis",
	"Bankleitzahl 8",
	"Bankbezeichnung 8",
	"Bankkonto-Nummer 8",
	"Länderkennzeichen 8",
	"IBAN-Nr. 8",
	"Leerfeld",
	"SWIFT-Code 8",
	"Abw. Kontoinhaber 8",
	"Kennz. Hauptbankverb. 8",
	"Bankverb. 8 Gültig von",
	"Bankverb. 8 Gültig bis",
	"Bankleitzahl 9",
	"Bankbezeichnung 9",
	"Bankkonto-Nummer 9",
	"Länderkennzeichen 9",
	"IBAN-Nr. 9",
	"Leerfeld",
	"SWIFT-Code 9",
	"Abw. Kontoinhaber 9",
	"Kennz. Hauptbankverb. 9",
	"Bankverb. 9 Gültig von",
	"Bankverb. 9 Gültig bis",
	"Bankleitzahl 10",
	"Bankbezeichnung 10",
	"Bankkonto-Nummer 10",
	"Länderkennzeichen 10",
	"IBAN-Nr. 10",
	"Leerfeld",
	"SWIFT-Code 10",
	"Abw. Kontoinhaber 10",
	"Kennz. Hauptbankverb. 10",
	"Bankverb. 10 Gültig von",
	"Bankverb. 10 Gültig bis",
	"Nummer Fremdsystem",
	"Insolvent",
	"SEPA-Mandatsreferenz 1",
	"SEPA-Mandatsreferenz 2",
	"SEPA-Mandatsreferenz 3",
	"SEPA-Mandatsreferenz 4",
	"SEPA-Mandatsreferenz 5",
	"SEPA-Mandatsreferenz 6",
	"SEPA-Mandatsreferenz 7",
	"SEPA-Mandatsreferenz 8",
	"SEPA-Mandatsreferenz 9",
	"SEPA-Mandatsreferenz 10",
	"Verknüpftes OPOS-Konto",
	"Mahnsperre bis",
	"Lastschriftsperre bis",
	"Zahlungssperre bis",
	"Gebührenberechnung",
	"Mahngebühr 1",
	"Mahngebühr 2",
	"Mahngebühr 3",
	"Pauschalenberechnung",
	"Verzugspauschale 1",
	"Verzugspauschale 2",
	"Verzugspauschale 3",
	"Alternativer Suchname",
	"Status",
	"Anschrift manuell geändert (Korrespondenzadresse)",
	"Anschrift individuell (Korrespondenzadresse)",
	"Anschrift manuell geändert (Rechnungsadresse)",
	"Anschrift individuell (Rechnungsadresse)",
	"Fristberechnung bei Debitor",
	"Mahnfrist 1",
	"Mahnfrist 2",
	"Mahnfrist 3",
	"Letzte Frist",
)
CREDITOR_FIRST_GROUP = 7
COMPANY = "2"


def build(client, party_accounts: list, created: datetime, year_start: date | None = None) -> bytes:
	header_row = header.row(
		client,
		category=16,
		format_name="Debitoren/Kreditoren",
		format_version=5,
		created=created,
		fiscal_year_start=year_start or fiscal_year_start(client.name, created.date()),
		date_from=None,
		date_to=None,
		description="",
	)
	return encode([header_row, ";".join(COLUMNS)] + [_line(party) for party in party_accounts])


def _compact(value: str | None) -> str:
	return "".join((value or "").split()).upper()


def _line(party):
	vat_id = _compact(party.vat_id)
	values = {
		"Konto": str(party.account_number),
		"Name (Adressatentyp Unternehmen)": text(party.party_name, 50),
		"Adressatentyp": text(COMPANY),
		"EU-Land": text(vat_id[:2]) if vat_id else "",
		"EU-UStID": text(vat_id[2:], 13) if vat_id else "",
		"IBAN-Nr. 1": text(_compact(party.iban), 34) if party.iban else "",
	}
	return ";".join(values.get(column, "") for column in COLUMNS)


def parse(content: bytes) -> list[RemoteParty]:
	decoded = content.decode("utf-8-sig") if content.startswith(codecs.BOM_UTF8) else content.decode(ENCODING)
	rows = list(csv.reader(io.StringIO(decoded, newline=""), delimiter=";"))
	if len(rows) < 2 or rows[0][0] not in ("EXTF", "DTVF") or rows[0][2] != "16":
		frappe.throw(_("Not a DATEV debtor/creditor file."))
	account_length = int(rows[0][13] or 4)
	names = rows[1]
	parties = []
	for row in rows[2:]:
		values = dict(zip(names, row, strict=False))
		number = values.get("Konto", "").strip()
		kind = _kind(int(number), account_length) if number.isdigit() else None
		if not kind:
			continue
		vat_id = values.get("EU-Land", "") + values.get("EU-UStID", "")
		parties.append(
			RemoteParty(
				kind=kind,
				account_number=int(number),
				name=_name(values),
				vat_id=vat_id or None,
				iban=values.get("IBAN-Nr. 1") or None,
			)
		)
	return parties


def _kind(number: int, account_length: int) -> str | None:
	base = 10**account_length
	if base <= number < CREDITOR_FIRST_GROUP * base:
		return "Debtor"
	if CREDITOR_FIRST_GROUP * base <= number < 10 * base:
		return "Creditor"
	return None


def _name(values: dict) -> str:
	person = " ".join(
		part
		for part in (
			values.get("Vorname (Adressatentyp natürl. Person)"),
			values.get("Name (Adressatentyp natürl. Person)"),
		)
		if part
	)
	return (
		values.get("Name (Adressatentyp Unternehmen)")
		or person
		or values.get("Name (Adressatentyp keine Angabe)", "")
	)
