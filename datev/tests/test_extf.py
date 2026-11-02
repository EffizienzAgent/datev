from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from datev.extf import booking_batch, header, master_data
from datev.extf.format import amount, document_field, encode, text
from datev.tests.factories import make_booking, make_client, make_document, make_party_account

FIXTURES = Path(__file__).parent / "fixtures"
CREATED = datetime(2027, 1, 31, 10, 30)


def data_lines(content: bytes) -> list[list[str]]:
	return [line.split(";") for line in content.decode("cp1252").split("\r\n")[2:] if line]


class TestExtfFormat(IntegrationTestCase):
	def test_text_sanitising(self):
		value = text('Miete "Mai";\r\nX😀')
		self.assertEqual(value, '"Miete ""Mai""; X"')
		self.assertEqual(encode([value]), b'"Miete ""Mai""; X"\r\n')
		self.assertEqual(text("Erdős"), '"Erdos"')
		self.assertEqual(text(None), '""')
		self.assertEqual(text("abcdef", 3), '"abc"')

	def test_document_field_1_charset(self):
		self.assertEqual(document_field("RE 2027#0042/ä"), "RE20270042/")
		self.assertEqual(document_field("A" * 40), "A" * 36)

	def test_amount_format(self):
		self.assertEqual(amount(Decimal("1234.5")), "1234,50")
		self.assertEqual(amount(1000000), "1000000,00")
		self.assertEqual(amount(Decimal("-12.345")), "12,35")
		self.assertEqual(amount(119.0), "119,00")

	def test_cp1252_umlauts(self):
		self.assertEqual(encode(["Müller"]), b"M\xfcller\r\n")
		self.assertEqual(encode(["a😀"]), b"a?\r\n")


class TestExtfHeader(IntegrationTestCase):
	def test_header_row(self):
		client = make_client()
		row = header.row(
			client,
			category=21,
			format_name="Buchungsstapel",
			format_version=13,
			created=datetime(2027, 1, 5, 10, 30),
			fiscal_year_start=date(2027, 1, 1),
			date_from=date(2027, 1, 1),
			date_to=date(2027, 1, 31),
			description="Week 1",
		)
		self.assertEqual(
			row,
			'"EXTF";700;21;"Buchungsstapel";13;20270105103000000;;"RE";"";"";29098;55003;20270101;4;'
			'20270101;20270131;"Week 1";"";1;0;0;"EUR";;"";;;"04";;;"";""',
		)
		self.assertEqual(len(row.split(";")), 31)


class TestBookingBatch(IntegrationTestCase):
	def setUp(self):
		self.client = make_client(account_mappings=[{"source_account": 8400, "datev_account": 4400}])
		self.party = make_party_account(self.client, account_number=10000, party_name="Beispiel GmbH")

	def booking(self, document_date, **overrides):
		document = make_document(self.client, document_date=document_date)
		return make_booking(document, self.party, document_date=document_date, **overrides)

	def test_columns_count(self):
		self.assertEqual(len(booking_batch.COLUMNS), 125)
		self.assertEqual(booking_batch.COLUMNS[0], "Umsatz (ohne Soll/Haben-Kz)")
		self.assertEqual(booking_batch.COLUMNS[113], "Festschreibung")

	def test_booking_line_matches_reference(self):
		first = self.booking(
			"2027-01-05", amount=119, posting_text="Miete Januar", document_field_1="RE-2027-0001"
		)
		linked = make_document(
			self.client,
			document_date="2027-01-20",
			program_code="BEDI",
			external_id="E0A08953-FBAA-4054-AF36-993D5D68F040",
		)
		second = make_booking(
			linked,
			self.party,
			document_date="2027-01-20",
			amount=1234.5,
			debit_credit="H",
			contra_account=4930,
			bu_key="3",
			posting_text="Stornorechnung Müller",
			document_field_1="SR-2027-0002",
		)
		files = booking_batch.build(self.client, [first, second], CREATED, "Januar 2027")
		self.assertEqual(len(files), 1)
		name, content = files[0]
		self.assertEqual(name, "EXTF_Buchungsstapel_55003_20270105_20270120.csv")
		self.assertEqual(content, (FIXTURES / "extf_booking_batch_expected.csv").read_bytes())

	def test_credit_note_sign(self):
		booking = self.booking("2027-01-05", amount=50, debit_credit="H")
		_, content = booking_batch.build(self.client, [booking], CREATED, "Credit")[0]
		line = data_lines(content)[0]
		self.assertEqual(line[0], "50,00")
		self.assertEqual(line[1], '"H"')
		self.assertEqual(len(line), 125)

	def test_split_by_fiscal_year(self):
		december = self.booking("2026-12-30")
		january = self.booking("2027-01-02")
		files = booking_batch.build(self.client, [january, december], CREATED, "Turn of year")
		self.assertEqual(
			[name for name, _ in files],
			[
				"EXTF_Buchungsstapel_55003_20261230_20261230.csv",
				"EXTF_Buchungsstapel_55003_20270102_20270102.csv",
			],
		)
		headers = [content.decode("cp1252").split("\r\n")[0].split(";") for _, content in files]
		self.assertEqual([h[12] for h in headers], ["20260101", "20270101"])
		self.assertEqual(
			[(h[14], h[15]) for h in headers], [("20261230", "20261230"), ("20270102", "20270102")]
		)

	def test_missing_party_account_number_rejected(self):
		party = make_party_account(self.client)
		document = make_document(self.client)
		booking = make_booking(document, party)
		with self.assertRaises(frappe.ValidationError):
			booking_batch.build(self.client, [booking], CREATED, "Missing")


class TestMasterData(IntegrationTestCase):
	def test_master_data_roundtrip(self):
		client = make_client()
		debtor = make_party_account(
			client,
			account_number=10000,
			party_name="Beispiel GmbH",
			vat_id="DE123456789",
			iban="DE02120300000000202051",
		)
		creditor = make_party_account(
			client, kind="Creditor", account_number=70000, party_name="Lieferant AG"
		)
		content = master_data.build(client, [debtor, creditor], CREATED)
		self.assertTrue(content.startswith(b'"EXTF";700;16;"Debitoren/Kreditoren";5;'))
		self.assertTrue(content.endswith(b"\r\n"))
		expected = [
			("Debtor", 10000, "Beispiel GmbH", "DE123456789", "DE02120300000000202051"),
			("Creditor", 70000, "Lieferant AG", None, None),
		]
		for raw in (content, b'"DTVF"' + content[len(b'"EXTF"') :]):
			parties = master_data.parse(raw)
			self.assertEqual(
				[(p.kind, p.account_number, p.name, p.vat_id, p.iban) for p in parties], expected
			)

	def test_master_data_columns(self):
		self.assertEqual(len(master_data.COLUMNS), 254)
		self.assertEqual(master_data.COLUMNS[:2], ("Konto", "Name (Adressatentyp Unternehmen)"))
