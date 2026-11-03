# Concepts

## Data model

| Doctype | Purpose |
| --- | --- |
| DATEV Client | One DATEV Mandant: consultant number, client number, account length, chart (SKR03/SKR04), fiscal year start, connectors, dispatch schedule, approval and numbering settings |
| DATEV Document | A file (invoice PDF) with direction, Belegtyp, GUID, and after upload external ID and program code, which form the Beleglink. Payment documents have no file (status Not Required) |
| DATEV Booking | One booking per voucher line: party account or general ledger account, contra account, debit/credit, gross amount, BU key, Belegfeld 1, posting text, fiscal year, general reversal flag, the document it depends on, link to its document and batch |
| DATEV Batch | Buchungsstapel of one client: approval, export connector, result, generated EXTF files and, for EXTF, who confirmed the import and when |
| DATEV Party Account | Debtor or creditor account per client and party, with number and sync status |
| DATEV Event Log | What happened to a document, booking, batch or party account, with a translated label, and the connector response (never credentials) |

Account mappings and tax keys (tax code to BU key) are tables on the client. An empty BU key means an automatic account. A booking delivered with a tax code that has no row in the tax keys is rejected, so no booking goes out with a guessed BU key; add a row with an empty BU key for automatic accounts.

Booking, batch, party account and client track their changes (version history).

## Statuses

| Doctype | Statuses |
| --- | --- |
| Document | Pending, Uploaded, No Link (e-mail storage), Not Required (no file), Stuck, Discarded |
| Booking | Pending, Batched, Stuck, Transferred, Exported, Failed; flag Held |
| Batch | Awaiting Approval, Approved, Sending, Sent, Partially Sent, Failed |
| Party Account | New, Created, Imported, To Clarify, Error |

"Transferred" means the API confirmed the booking. "Exported" means an EXTF file was generated. Both are final. Exported is not imported: the tax office still has to import the file, and the batch counts as unconfirmed until someone confirms the import on it.

Statuses, the held flag, the batch link, amounts, party account, account and contra account, the general reversal flag, the dependency and the document link of a booking, the batch status and import confirmation, and status, GUID, external ID and program code of a document are maintained by the app. They are read only, and saving a changed value from the desk or the REST API is rejected. DATEV Users can read bookings and batches but not edit them; hold, approve and send go through the app's methods.

A DATEV Manager can set a Transferred, Exported or Failed booking back to Pending ("Reset to Pending" on the booking, `datev.dispatch.reset_booking(booking, reason)`). A reason is required and goes to the event log. Use it only when the booking is known to be missing in DATEV, for example after the tax office deleted it.

## Flow

1. The source delivers a document (`submit_document`), its parties and bookings (see [intake API](intake-api.md)). Each call is idempotent.
2. The document is uploaded by the storage connector right away. Failures retry with backoff (5 min, 30 min, 2 h, 12 h); after five attempts the document is Stuck. "Retry upload" on a stuck document resolves its Belegtyp again and queues the upload. The upload result is saved and committed before anything else, so a later error cannot lose the Beleglink, and a failure never turns an uploaded document back into Stuck. Business OS uploads run in one background job per client (queue `long`) that uploads the due documents in order, at least five seconds apart. Documents of disabled clients wait until the client is enabled again.
3. Bookings collect until the client's dispatch date (daily, weekly or monthly, hourly check). Before a batch is built each booking is checked: mandatory fields (kind and debit/credit included), the export connector's own checks (DATEVconnect: the fiscal year exists in DATEV and is not locked; payments also strictly, see [connectors](connectors.md#datevconnect)), account and contra account (after mapping) not longer than the client's account length, document uploaded or without file (and linked, if the client requires a Beleglink; this applies to invoices only), party account in DATEV. Failing bookings become Stuck with a message. A booking that depends on another document stays Pending until that document is in DATEV, see [payments](#payments). A document goes out all or nothing: if one of its open lines is stuck, held or waiting, its other lines stay Pending with "Waiting for line … of this document". The rest goes into a batch.
4. With approval required, the batch waits as "Awaiting Approval" until the responsible user or role (or a DATEV Manager/User) approves it. Bookings can be held and move to a later batch; holding a booking holds all lines of its document, and is refused while another line of the document is already in an approved or sending batch. Without approval the batch goes out on schedule. "Send bookings now" starts an extra run.
5. The export connector transfers the batch. The result is stored on each booking and the batch; rejected bookings become Failed with the message and are tried again in the next batch.

DATEV is called only in background jobs. The hourly run only decides what is due and queues one job per due client (collect, then send if approved) and one per approved batch, on queue `long` with a one-hour timeout; a job that is already queued is not queued twice. Approving and "Send bookings now" queue the same jobs. State-changing methods accept POST only.

## Payments

Bank and cash bookings use the kind `Payment`. Their documents have no file: `upsert_document` with direction `Other` and without `content` creates a document with status Not Required, which is never uploaded and never blocks a booking. Invoice documents always need a file, a document that already has a file keeps it, and a document without file takes no invoice bookings. The client's Beleglink requirement applies to invoices only.

A booking has either a party account (Konto = the debtor's or creditor's number) or a general ledger `account` (Konto = that account number, for fees, interest or transfers), never both. Invoices always need a party account. The client's account mapping applies to `account` as to the contra account.

**Dependencies:** a payment line that clears an invoice names the invoice's document in `depends_on_document`. It stays Pending with "Waiting for document … to reach DATEV" until all bookings of that document are Transferred or Exported; if that document is Stuck or Discarded, the message names its status and the responsible people get a notice once a day per document. The other lines of the payment's document wait with it. Waiting lines are flagged "Waiting for Dependency", shown as a column in the status report and do not make the dispatch overdue. When a batch delivers the document they wait for, the client's collection runs again right away, so the payments do not wait for the next dispatch date. A dependency may only point to a document with invoice bookings that have no dependency themselves, so chains and cycles are rejected.

**Reversals:** bookings can be added to a document whose other bookings are already in DATEV, as new lines with new line keys. A line with `general_reversal` reverses an earlier line in DATEV (Generalumkehr): same accounts and side, the flag tells DATEV to post it the other way round. When a payment is cancelled, `intake.discard_open_bookings(source_key)` deletes its open lines (also those in a batch awaiting approval) and keeps the transferred ones, which the source then reverses. It is refused while a line is in an approved or sending batch. `discard_document` is refused for a document with batched or transferred bookings.

## Party accounts

Each client has its own accounts. Numbers come from the source system (preferred number) or from DATEV (next available), configurable for debtors and creditors. A party gets its account when its first booking needs it and is created in DATEV before that booking is sent. A booking must use a party account of its own client: outgoing invoices a debtor, incoming invoices a creditor.

When DATEVconnect creates an account, the number and status "Created" are committed at once, so the account is never created twice. Temporary errors (timeouts, server errors) leave the party New with a message and are retried with the next run; other errors set "Error". A DATEV Manager can retry a party in "Error" or "To Clarify" ("Retry" on the party account, `datev.parties.retry_party`).

Initial matching imports existing DATEV accounts (via DATEVconnect, or from a master data export file) and links them by number (if enabled), VAT ID, IBAN or name. A number match links only if the normalised names or the VAT IDs agree; otherwise the party goes to "To Clarify" with both names, and a retry states the mismatch again. Ambiguous or colliding hits go to "To Clarify" and are neither created nor booked until someone decides. A master data file whose consultant number, client number or account length differ from the client is rejected.

"Mark resolved" (DATEV Manager) settles a party to clarify with the DATEV account number, either as an existing account (Imported) or as a new account confirmed in DATEV (Created). A reason is required and goes to the event log.

Each import is stored on the client ("Last Party Import On"). With the EXTF master data connector this import is required: before a new account goes out in a master data file, its number is checked against the last import. The same number under the same name or VAT ID links the account (Imported), otherwise it goes to "To Clarify". Import the master data before the first bookings are sent. Without any recorded import new parties go to "To Clarify" with "Match DATEV accounts from a master data file first."

## Duplicate protection

- A Transferred or Exported booking never enters another batch.
- Responses are evaluated per booking, so in a partly rejected batch only the failed bookings are sent again.
- A document that is already in DATEV cannot be discarded; create a cancellation invoice instead (its own document, its own booking). Payments without file are reversed with general reversal lines instead.
- A reversal line is a booking like any other, with its own line key, so repeating the call does not create a second reversal.
- **Uncertain delivery:** if a DATEVconnect request may have reached DATEV but the answer is missing or cannot be read (timeout, connection loss, server error, a success response without a result for a booking, or an error after the first request), the batch stays in "Sending" and the affected bookings are not touched. Bookings of later requests that were never sent are marked Failed and go into the next batch. Check in DATEV, then call `datev.dispatch.resolve_batch(batch, delivered)` (button "Resolve" on the batch, DATEV Manager only). `delivered=1` marks the bookings as transferred (the batch is "Partially Sent" if other bookings of the run failed); `delivered=0` returns them to Pending for the next batch. A batch is never retried automatically. Only an error raised before any request was sent returns the bookings to Pending on its own.
- **Uncertain upload:** with Business OS in "Use returned ID" mode, an upload that may have arrived (timeout after sending or a server error) or a success response without an ID makes the document Stuck instead of retrying. In "Send own ID" mode an answer with a different ID makes the document Stuck, because the Beleglink would be wrong. Check DUo, then "Retry upload", or, if the document is already there, "Mark as uploaded" with its DUo ID and a reason (DATEV Manager).
- **EXTF:** files are not delivered to DATEV directly, so an EXTF batch can only be resolved as not delivered.

## Visibility

The report **DATEV Status** shows per client: transferred in the last 30 days, batches awaiting approval or sending, failed or partially sent batches of the last 30 days, EXTF files not confirmed as imported, stuck and failed bookings, stuck documents, documents without link, held bookings, bookings waiting for a dependency, party accounts to clarify or with errors, overdue dispatch and remaining Business OS credits. Problem clients come first, disabled clients last and greyed. Open bookings (Pending, Stuck, Failed), except held ones and those waiting for a dependency, make a passed dispatch date overdue; an EXTF file unconfirmed for more than seven days is a problem.

The responsible user or role is notified once per batch when a batch waits for approval or an EXTF file is ready, and once a day per reason when a dispatch is overdue, something is stuck or an EXTF file is unconfirmed for more than seven days, a batch has been sending for over an hour, a dependency of waiting bookings is Stuck or Discarded (once per document), or Business OS credits run low (only once credits have been reported by Business OS).
