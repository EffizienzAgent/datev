# Concepts

## Data model

| Doctype | Purpose |
| --- | --- |
| DATEV Client | One DATEV Mandant: consultant number, client number, account length, chart (SKR03/SKR04), fiscal year start, connectors, dispatch schedule, approval and numbering settings |
| DATEV Document | A file (invoice PDF) with direction, Belegtyp, GUID, and after upload external ID and program code, which form the Beleglink |
| DATEV Booking | One booking per voucher line: party account, contra account, debit/credit, gross amount, BU key, Belegfeld 1, posting text, fiscal year, link to its document and batch |
| DATEV Batch | Buchungsstapel of one client: approval, export connector, result, generated EXTF file |
| DATEV Party Account | Debtor or creditor account per client and party, with number and sync status |
| DATEV Event Log | What happened to a document, booking or batch, and the connector response (never credentials) |

Account mappings and tax keys (tax code to BU key) are tables on the client. An empty BU key means an automatic account.

## Statuses

| Doctype | Statuses |
| --- | --- |
| Document | Pending, Uploaded, No Link (e-mail storage), Stuck, Discarded |
| Booking | Pending, Batched, Stuck, Transferred, Exported, Failed; flag Held |
| Batch | Awaiting Approval, Approved, Sending, Sent, Partially Sent, Failed |
| Party Account | New, Created, Imported, To Clarify, Error |

"Transferred" means the API confirmed the booking. "Exported" means an EXTF file was generated. Both are final.

## Flow

1. The source delivers a document (`submit_document`), its parties and bookings (see [intake API](intake-api.md)). Each call is idempotent.
2. The document is uploaded by the storage connector right away. Failures retry with backoff (5 min, 30 min, 2 h, 12 h); after five attempts the document is Stuck. A stuck document can be retried by hand.
3. Bookings collect until the client's dispatch date (daily, weekly or monthly, hourly check). Before a batch is built each booking is checked: mandatory fields, document uploaded (and linked, if the client requires a Beleglink), party account in DATEV. Failing bookings become Stuck with a message; the rest goes into a batch.
4. With approval required, the batch waits as "Awaiting Approval" until the responsible user or role (or a DATEV Manager/User) approves it. Single bookings can be held and move to a later batch. Without approval the batch goes out on schedule. "Send bookings now" starts an extra run.
5. The export connector transfers the batch. The result is stored on each booking and the batch; failed bookings return to Pending with the message and are tried again in the next batch.

## Party accounts

Each client has its own accounts. Numbers come from the source system (preferred number) or from DATEV (next available), configurable for debtors and creditors. A party gets its account when its first booking needs it and is created in DATEV before that booking is sent.

Initial matching imports existing DATEV accounts (via DATEVconnect, or from a master data export file) and links them by number (if enabled), VAT ID, IBAN or name. Ambiguous or colliding hits go to "To Clarify" and are neither created nor booked until someone decides.

## Duplicate protection

- A Transferred or Exported booking never enters another batch.
- Responses are evaluated per booking, so in a partly rejected batch only the failed bookings are sent again.
- A document that is already in DATEV cannot be discarded; create a cancellation invoice instead (its own document, its own booking).
- **Uncertain delivery:** if a DATEVconnect request may have reached DATEV but the answer is missing (timeout, connection loss, server error), the batch stays in "Sending" and its bookings are not touched. Check in DATEV, then call `datev.dispatch.resolve_batch(batch, delivered)` (button "Resolve" on the batch, DATEV Manager only). `delivered=1` marks the bookings as transferred; `delivered=0` returns them to Pending for the next batch. A batch is never retried automatically.

## Visibility

The report **DATEV Status** shows per client: transferred in the last 30 days, batches awaiting approval or sending, stuck bookings and documents, documents without link, held bookings, party accounts to clarify, overdue dispatch and remaining Business OS credits. Problem clients come first.

The responsible user or role receives notifications (once a day per reason) when a batch waits for approval, a dispatch is overdue, something is stuck, a batch has been sending for over an hour, or Business OS credits run low.
