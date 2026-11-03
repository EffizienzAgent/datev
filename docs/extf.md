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

The files are attached to the batch (private) and can be downloaded again at any time; the content is identical. The batch's file field links the first file. EXTF by e-mail additionally sends all files to the client's EXTF address; "Send e-mail again" on the batch repeats it.

## Exported is not imported

The app cannot see whether the tax office imported a file. After the export the responsible people get one "DATEV file ready" notification. Once the import is done, "Confirm import by tax office" on the batch (`datev.dispatch.confirm_import(batch)`) records who confirmed it and when. Until then the status report counts the batch as unconfirmed, and after seven days the daily summary mentions it. An EXTF batch can only be resolved as not delivered, which exports its bookings again.

## Booking lines

One line per booking: amount (always positive) with debit/credit flag, currency, party account or general ledger account as account, contra account (both after the client's account mapping), BU key, document date (DDMM), Belegfeld 1, posting text (60 characters), Beleglink and "Generalumkehr (GU)" = `1` for general reversal lines.

Belegfeld 1 keeps only characters DATEV allows (letters, digits and `$ & % * + - /`) and at most 36 of them.

## Beleglink

The Beleglink column contains `BEDI "<GUID>"`, that is program code and external ID of the uploaded document. Without an external ID (e-mail storage, payments without file) the column stays empty.

## Master data import

To match existing accounts without API access, upload the tax office's debtor/creditor export (category 16) with "Match from file" on the client, before the first bookings are sent. The file's consultant number, client number and account length must match the client. Personenkonten have one digit more than the Sachkontenlänge: those starting with 1 to 6 count as debtors, those starting with 7 to 9 as creditors.
