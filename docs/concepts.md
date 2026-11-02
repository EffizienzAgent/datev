# Concepts

## Data model

| Doctype | Purpose |
| --- | --- |
| DATEV Client | One DATEV Mandant: consultant number, client number, account length, chart (SKR03/SKR04), fiscal year start, connectors, dispatch schedule, approval and numbering settings |
| DATEV Document | A file (invoice PDF) with direction, Belegtyp, GUID, and after upload external ID and program code, which form the Beleglink |
| DATEV Booking | One booking per voucher line: party account, contra account, debit/credit, gross amount, BU key, Belegfeld 1, posting text, fiscal year, link to its document and batch |
| DATEV Batch | Buchungsstapel of one client: approval, export connector, result, generated EXTF file |
| DATEV Party Account | Debtor or creditor account per client and party, with number and sync status |
| DATEV Event Log | What happened to a document, booking, batch or party account, and the connector response (never credentials) |

Account mappings and tax keys (tax code to BU key) are tables on the client. An empty BU key means an automatic account.

## Statuses

| Doctype | Statuses |
| --- | --- |
| Document | Pending, Uploaded, No Link (e-mail storage), Stuck, Discarded |
| Booking | Pending, Batched, Stuck, Transferred, Exported, Failed; flag Held |
| Batch | Awaiting Approval, Approved, Sending, Sent, Partially Sent, Failed |
| Party Account | New, Created, Imported, To Clarify, Error |

"Transferred" means the API confirmed the booking. "Exported" means an EXTF file was generated. Both are final.

Statuses, the batch link, amounts, party and contra account and the document link of a booking, the batch status, and status, GUID, external ID and program code of a document are maintained by the app. They are read only, and saving a changed value from the desk or the REST API is rejected.

A DATEV Manager can set a Transferred, Exported or Failed booking back to Pending ("Reset to Pending" on the booking, `datev.dispatch.reset_booking(booking, reason)`). A reason is required and goes to the event log. Use it only when the booking is known to be missing in DATEV, for example after the tax office deleted it.

## Flow

1. The source delivers a document (`submit_document`), its parties and bookings (see [intake API](intake-api.md)). Each call is idempotent.
2. The document is uploaded by the storage connector right away. Failures retry with backoff (5 min, 30 min, 2 h, 12 h); after five attempts the document is Stuck. A stuck document can be retried by hand. The upload result is saved and committed before anything else, so a later error cannot lose the Beleglink. Documents of disabled clients wait until the client is enabled again.
3. Bookings collect until the client's dispatch date (daily, weekly or monthly, hourly check). Before a batch is built each booking is checked: mandatory fields, the export connector's own checks (DATEVconnect: no payments yet, the fiscal year exists in DATEV and is not locked), document uploaded (and linked, if the client requires a Beleglink), party account in DATEV. Failing bookings become Stuck with a message; the rest goes into a batch.
4. With approval required, the batch waits as "Awaiting Approval" until the responsible user or role (or a DATEV Manager/User) approves it. Single bookings can be held and move to a later batch. Without approval the batch goes out on schedule. "Send bookings now" starts an extra run. Approving and "Send bookings now" queue the transfer as a background job (queue `long`), so DATEV is never called inside the web request.
5. The export connector transfers the batch. The result is stored on each booking and the batch; rejected bookings become Failed with the message and are tried again in the next batch.

## Party accounts

Each client has its own accounts. Numbers come from the source system (preferred number) or from DATEV (next available), configurable for debtors and creditors. A party gets its account when its first booking needs it and is created in DATEV before that booking is sent. A booking must use a party account of its own client: outgoing invoices a debtor, incoming invoices a creditor.

When DATEVconnect creates an account, the number and status "Created" are committed at once, so the account is never created twice. Temporary errors (timeouts, server errors) leave the party New with a message and are retried with the next run; other errors set "Error". A DATEV Manager can retry a party in "Error" or "To Clarify" ("Retry" on the party account, `datev.parties.retry_party`).

Initial matching imports existing DATEV accounts (via DATEVconnect, or from a master data export file) and links them by number (if enabled), VAT ID, IBAN or name. Ambiguous or colliding hits go to "To Clarify" and are neither created nor booked until someone decides.

Each import is stored on the client ("Last Party Import On"). With the EXTF master data connector this import is required: before a new account goes out in a master data file, its number is checked against the last import. The same number under the same name links the account (Imported), under another name it goes to "To Clarify". Without any recorded import new parties go to "To Clarify" with "Match DATEV accounts from a master data file first."

## Duplicate protection

- A Transferred or Exported booking never enters another batch.
- Responses are evaluated per booking, so in a partly rejected batch only the failed bookings are sent again.
- A document that is already in DATEV cannot be discarded; create a cancellation invoice instead (its own document, its own booking).
- **Uncertain delivery:** if a DATEVconnect request may have reached DATEV but the answer is missing or cannot be read (timeout, connection loss, server error, a success response without a result for a booking, or an error after the first request), the batch stays in "Sending" and the affected bookings are not touched. Bookings of later requests that were never sent are marked Failed and go into the next batch. Check in DATEV, then call `datev.dispatch.resolve_batch(batch, delivered)` (button "Resolve" on the batch, DATEV Manager only). `delivered=1` marks the bookings as transferred (the batch is "Partially Sent" if other bookings of the run failed); `delivered=0` returns them to Pending for the next batch. A batch is never retried automatically. Only an error raised before any request was sent returns the bookings to Pending on its own.
- **Uncertain upload:** with Business OS in "Use returned ID" mode, an upload that may have arrived (timeout after sending) or a success response without an ID makes the document Stuck instead of retrying. Check DUo before retrying it by hand.

## Visibility

The report **DATEV Status** shows per client: transferred in the last 30 days, batches awaiting approval or sending, stuck and failed bookings, stuck documents, documents without link, held bookings, party accounts to clarify or with errors, overdue dispatch and remaining Business OS credits. Problem clients come first. Open bookings (Pending, Stuck, Failed) make a passed dispatch date overdue.

The responsible user or role receives notifications (once a day per reason) when a batch waits for approval, a dispatch is overdue, something is stuck, a batch has been sending for over an hour, or Business OS credits run low (only once credits have been reported by Business OS).
