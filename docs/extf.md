# EXTF

The EXTF connectors write DATEV's import format from DATEV's public format description.

## Format

- Header row `"EXTF";700;...`, then the column row, then one line per record.
- Encoding Windows-1252 (cp1252), separator `;`, decimal comma, line ends CRLF.
- Text is quoted with inner quotes doubled. Control characters are replaced by a space; characters outside cp1252 are folded (for example diacritics removed) or dropped.
- Buchungsstapel: category 21, format version 13, 125 columns.
- Debitoren/Kreditoren: category 16, format version 5.
- The header carries consultant number, client number, fiscal year start, account length (Sachkontenlänge) and the chart. Bookings are written with Festschreibung 0.

## Files

One Buchungsstapel file per fiscal year in the batch: `EXTF_Buchungsstapel_<client number>_<from>_<to>.csv`, with the period from the document dates. Fiscal years follow the client's fiscal year start month.

When a batch contains bookings for parties with status New, one master data file per fiscal year of the batch is added, `EXTF_Debitoren_Kreditoren_<client number>_<fiscal year start>_<date>.csv`, with the matching WJ-Beginn in its header. Those parties become Created once the batch is exported.

The files are attached to the batch (private) and can be downloaded again at any time; the content is identical. The batch's file field links the first file. EXTF by e-mail additionally sends all files to the client's EXTF address.

## Booking lines

One line per booking: amount (always positive) with debit/credit flag, currency, party account as account, contra account (after the client's account mapping), BU key, document date (DDMM), Belegfeld 1, posting text (60 characters) and Beleglink.

Belegfeld 1 keeps only characters DATEV allows (letters, digits and `$ & % * + - /`) and at most 36 of them.

## Beleglink

The Beleglink column contains `BEDI "<GUID>"`, that is program code and external ID of the uploaded document. Without an external ID (e-mail storage) the column stays empty.

## Master data import

To match existing accounts without API access, upload the tax office's debtor/creditor export (category 16) with "Match from file" on the client. Personenkonten have one digit more than the Sachkontenlänge: those starting with 1 to 6 count as debtors, those starting with 7 to 9 as creditors.
