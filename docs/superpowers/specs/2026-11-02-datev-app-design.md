# DATEV App for Frappe – Design

Date: 2026-11-02 · Status: draft for approval

## Intent

Bookings should reach DATEV complete, clean and with a document link (Beleglink), so the tax office never has to match receipts by hand. An existing n8n-based integration already does this for one client. This app turns that pattern into a reusable, open-source Frappe app, first used with ERPNext.

Existing ERPNext DATEV integrations export one line per GL entry against a suspense account, without one booking per voucher, without BU keys and without Beleglinks, and send receipts only by e-mail. Tax offices expect one booking per voucher with a linked document image.

## Language

- Code, doctype/field names, specs, docs and commit messages are in **English**.
- **Every user-facing string has a German translation** (Frappe translation files), including doctype and field labels, options, messages and notifications. German domain terms follow DATEV usage (Mandant, Beleg, Buchungsstapel, Belegtyp, Debitor/Kreditor, BU-Schlüssel).
- No client or customer names in code, specs, docs, fixtures or commits. Deployment specifics live in the deploying project, not in this repo.

## Goals

- Every booking reaches DATEV with its Beleglink. No booking without a document, including cancellation invoices.
- One booking per voucher: party account against G/L account, gross amount, BU key only where needed (none for automatic accounts).
- Interchangeable connectors in three places (document storage, booking export, master data), configurable per client and switchable without changing apps.
- Multi-company: several companies in one site, one DATEV client (Mandant) per company. Groups with around 20 companies must be manageable.
- Nothing gets stuck unnoticed: pending, stuck and overdue items are visible to the person responsible.
- No duplicates in DATEV.
- Correct document types (Belegtypen) on upload, chosen from the provider's list or self-defined.

## Non-goals

- No bookkeeping inside the app. DATEV stays the tax office's ledger, the ERP the source.
- No e-invoice parsing or generation. Outgoing e-invoices come from an e-invoicing app (e.g. `eu_einvoice`), incoming ones from a separate parser service or OCR.
- No DATEV online APIs that require our own DATEV partner approval (`accounting:extf-files`, `accounting:documents`, `dxso-jobs`). An open-source app cannot ship an approved client ID. Business OS covers DATEV online access through its own approval.
- No central hub instance serving several customers. Multi-tenancy means several companies in one site.

## Anti-goals (failure even if it technically works)

- Items silently stall because someone forgot an approval or nobody notices an error.
- Bookings arrive in DATEV without a document.
- Wrong bookings: wrong party account, wrong tax key, duplicates.
- Existing debtors/creditors in DATEV get created twice instead of linked.

## Scope

| | Design | First release |
|---|---|---|
| Sales invoices incl. cancellation invoices | yes | **build** |
| Purchase invoices | yes, fully | later |
| Payments / bank transactions | yes, fully | later (next step) |
| Debtors: create + initial matching | yes | **build** |
| Creditors: create + initial matching | yes | with purchase invoices |
| Document storage: Business OS (DUo) | yes | **build** |
| Document storage: e-mail to DUo | yes | **build** (fallback) |
| Document storage: webhook | yes | later |
| Document storage: DocArchive | yes | later |
| Export: DATEVconnect (on-prem) | yes | **build** |
| Export: Business OS (DATEV Rechnungswesen online) | yes | evaluate, see open questions |
| Export: EXTF download | yes | **build** (fallback) |
| Export: EXTF by e-mail | yes | **build** (thin layer on download) |

Data model and connector interfaces are cut so purchase invoices, payments and more connectors can be added without restructuring.

## Architecture

### Two apps

1. **Core app** (this repo, `frappe-datev`, MIT). Depends on Frappe only. Contains the neutral data model, connectors, EXTF generator, DATEVconnect and Business OS clients, dispatch control, overview, intake API.
2. **ERPNext adapter** (separate app and repo, GPL-3 because it depends on ERPNext). App `datev_erpnext`, repo `EffizienzAgent/datev_erpnext` (locally `frappe-datev-erpnext`). Separate repo because bench installs one app per repository and the licenses differ. The name starts with `datev` so it groups with the core and is not confused with ALYF's `erpnext_datev`. Translates ERPNext documents into neutral documents and bookings and stores DATEV account numbers on customers/suppliers.

**Clean implementation, no third-party code:** both apps are written from scratch. The EXTF generator is built from DATEV's public format description, the ERPNext mapping and purchase-invoice matching from our own analysis. No code is copied from other DATEV or e-invoicing apps. Other apps are only used as installed dependencies where needed (e.g. an e-invoicing app that produces the outgoing PDF/XML).

### Neutral data model (core)

- **DATEV Client (Mandant):** consultant number, client number, G/L account length, chart (SKR03/04), fiscal year start, optional link to a Company. Per area: connector settings. Dispatch schedule (daily/weekly/monthly), approval required yes/no, responsible role/user, strictness "no booking without Beleglink" (default on).
  - Values the export side can read (DATEVconnect `GET /clients/{id}/fiscal-years`, Business OS `datev-rewe-read/geschaeftsjahre`) are read from there and checked against the settings.
- **Document (Beleg):** file, direction (outgoing, incoming, other), document type (Belegtyp), origin (source document or API), storage connector, external ID, program code (Kürzel), resulting Beleglink, status, error, attempts.
- **Booking (Buchung):** client, fiscal year, document date, Belegfeld 1 (invoice number), account, contra account, debit/credit, gross amount, currency, BU key, tax rate, posting text, due date, link to document, source document, kind (outgoing invoice proposal, incoming invoice proposal, payment), status, batch.
- **Batch (Stapel):** client, period, export connector, bookings, approval (who, when), result (API response or generated EXTF file), status.
- **Party Account:** client, party (customer/supplier or external reference), kind (debtor/creditor), DATEV account number, sync status (imported from DATEV, new, created in DATEV, to clarify, error).
- **Event log** per document, booking and batch: what happened when, via which path, with which response.

Every object remembers its path and status. When a client switches connector (e.g. e-mail to Business OS), the app detects documents without a link and lists them. Whether to backfill is decided by a human together with the tax office. No automatic re-upload, to avoid duplicates in DUo.

### Connectors

Each connector kind has a small fixed interface. Implementations are registered via hooks, so other apps can contribute connectors. Credentials are password fields per client.

#### Document storage

Uploads or links a file. Result: program code + ID → Beleglink.

| Connector | ID | Code | Notes |
|---|---|---|---|
| E-mail to DUo | none | – | Fallback, document gets no link. With strict setting its bookings are "stuck". |
| Business OS | per client: send our own UUID in `id`, **or** take the returned `id` | `BEDI` | See below. Proven in production (with returned ID). |
| Webhook | per client: send ours or take the returned one | configurable | Generic contract modeled on Business OS, see below. For any DMS. |
| DocArchive | archive document name | configurable | Can be both: source for other connectors (file already archived and locked, not re-archived) **or** its own Beleglink. A non-BEDI link needs a DATEV client module at the tax office. |

With a self-generated UUID the Beleglink is known before upload, and a retry sends the same UUID. Whether DATEV keeps the supplied UUID unchanged is confirmed with a test document (open questions).

**Business OS (verified against the OpenAPI spec, 2026-11-02):**
- Base `https://api.business-os.de`, header `x-api-key`. Every call takes `connectionId` (UUID of the DATEV connection) and optional `quelle` (source tag) as query parameters.
- Connection per client: `GET /v2/datev-duo/connections` lists connections. `GET /v2/datev/companies` returns clients as `<consultant>-<client>` with their booked DATEV services. The app offers to pick the connection that matches the client's consultant/client number.
- Document types: `GET /v2/datev-duo/belegtypen` → `[{belegtyp: "Rechnungseingang", belegkreis: "invoices_received"}, …]`. Costs 1 credit per call, so it is fetched on demand in the connector settings and cached, not per upload.
- Upload: `POST /v2/datev-duo/beleg-bereitstellen`, body `datei.content` (base64), `datei.dateiname` (base name, max 255), `belegtyp` (required, must match the list), `id` (optional UUID), `belegnotiz` (optional, max 60). Response: `id` (file ID in DATEV), `dateiname`, `business-os.neuesGuthaben` (remaining credits).
- Errors: JSON `{error, message, code, details}`. `code` is stable for known categories and drives retry decisions.
- Cost/limits: 1 credit per upload. Observed limit 200 requests / 15 min; throttle to one call per 5 s. Remaining credits are stored and shown, with a warning below a threshold.

#### Document types (Belegtypen)

- Each storage connector has a **document type mapping** per client: rule → Belegtyp. Rules match on direction and source (e.g. source doctype, is return/credit note). There is always a default per direction.
- Choosing a type is a dropdown. Options come from the provider where it offers a list (Business OS: fetched as above, stored with `belegkreis`), otherwise from a self-defined list in the connector settings (webhook, e-mail).
- Sensible defaults ship with the app (outgoing → `Rechnungsausgang`, incoming → `Rechnungseingang`, other → `Sonstige`) and are validated against the fetched list.
- On upload the resolved type is stored on the document. An unknown or missing type stops the document with a clear message instead of uploading with a wrong type.
- Payments, bank statements and cash later use the same mapping.

#### Webhook connector (generic)

Same shape as Business OS, so the same code path and mapping UI are reused, while any other system can be connected:
- Per client: target URL, auth (header name + secret, or basic auth), timeout, program code for the Beleglink, ID mode (send ours / take from response), optional URL that returns the document type list in the same shape as Business OS (`[{belegtyp, belegkreis}]`), otherwise self-defined types.
- Request: `POST` JSON `{file: {content (base64), filename}, document_type, id (UUID, if we send it), note, metadata: {client, direction, document_date, document_number, amount, currency, source}}`. Field names follow the Business OS contract in English; an optional field-name mapping per connector covers systems that need other names. *(Claude's call)*
- Response: any 2xx with JSON `{id}`. The returned `id` is stored when the ID mode says so. Non-2xx is an error with the body stored.
- Idempotency: the request carries the document's UUID (also as `X-Idempotency-Key` header) so receivers can deduplicate retries.

#### Booking export

| Connector | What | Notes |
|---|---|---|
| DATEVconnect | JSON to DATEV Rechnungswesen on the tax office's server, base `/datev/api/accounting/v1`, basic auth, over VPN | Outgoing: `POST /clients/{id}/fiscal-years/{fy}/posting-proposals-outgoing-invoices/batch`. Incoming: `…-incoming-invoices/batch`. Payments: `POST …/accounting-sequences` (not committed). Fields: `amount, date, debitor_/creditor_account_number, document_field1 (max 36), document_system, document_link, due_date, tax_rate, name (40), posting_description (60), currency_code, account_number, accounting_transaction_key`. One call per direction and fiscal year. Self-signed certificates can be allowed per client. Client GUID resolved via `GET /clients` by client number. |
| Business OS (Rechnungswesen online) | `POST /v2/datev-rewe/buchung` per voucher: `kontenrahmen, kontonummernLaenge, geschaeftsjahrStartDatum, waehrung, sollHabenKennzeichen, buchungstext (60), belegfeld1, transaktionsdatum, buchungszeilen[{kontonummer, umsatz (gross, signed), buSchluessel, kostenstelle1/2, skontobetrag}], belegId, faelligkeitsdatum, lieferdatum, steuererfassungsdatum` | No VPN or DATEVconnect, but the tax office must grant online rights for the client in DATEV (checkable via `GET /v2/datev-rewe-read/permissions`). The document is uploaded with `POST /v2/datev-rewe/beleg-bereitstellen` (`belegId` UUID, `belegdatum`, `belegtyp` from `GET /v2/datev-rewe/belegtypen`) and linked through `belegId`. Valid BU keys from `GET /v2/datev-rewe/steuersaetze`. 1 credit per booking. |
| EXTF download | EXTF booking batch (category 21) plus master data (category 16) | Fallback. File is attached to the batch and downloaded. |
| EXTF by e-mail | as download, additionally sent to one address per client | |

An EXTF file is only generated when an EXTF connector is active.

Business OS also offers `POST /v2/datev-duo/buchungsvorschlag` (structured proposal per document in DUo). Not planned, since bookings should land in Rechnungswesen, but the connector interface does not exclude it.

#### Master data (debtors, creditors)

- DATEVconnect: `GET …/{debitors|creditors}`, `GET …/next-available`, `PUT …/{debitors|creditors}` with `application/merge-patch+json` into every open fiscal year.
- Business OS: `POST /v2/datev-rewe/personenkonto` (creates or updates by `personenkontoNummer`).
- EXTF master data file in the same dispatch.

### Account numbers and initial matching

- The DATEV account number lives on the Party Account, i.e. per client and party.
- Assignment per client: from a configurable field in the source system (e.g. customer number) or from DATEV via `next-available`. Can differ for debtors and creditors.
- The number is assigned when the first booking needs it. New parties are created in DATEV before their first booking.
- A booking whose party does not exist in DATEV, or is unresolved, is not sent. It shows as "stuck" with a message. A number colliding with an unrelated DATEV account goes to the clarification list. *(Claude's call)*
- **One-off initial matching:** read existing debtors/creditors from DATEV and match them to customers/suppliers, by number where the client's numbering matches, then by VAT ID, IBAN or name. Unambiguous hits are linked. Everything else goes to a clarification list and is neither created nor booked until a human decides.
- Without API access, matching works from a master data export supplied by the tax office.

### Flow

1. The source document is submitted (e.g. sales invoice). The adapter creates document and booking(s).
2. The document goes to the storage connector immediately and automatically. On errors: automatic retry with backoff, then status "stuck".
3. Bookings collect until the client's next dispatch date.
4. With approval: the responsible person sees the batch, reviews and approves; individual bookings can be held back and move to the next batch, visible as "waiting". Without approval the batch goes out on schedule.
5. Before dispatch the app checks mandatory fields, party accounts, Beleglinks, open fiscal years and lock status. Failures stay with a message; the rest goes out.
6. The export connector transfers. Result and response are stored on the batch and on each booking.

### Duplicate protection and corrections

- A transferred booking is never sent again. Only what demonstrably did not arrive is retried.
- **When is a booking transferred?** With DATEVconnect/Business OS when the API confirms it. Responses are evaluated per proposal/booking, so in a partially rejected batch only the failed bookings are resent. With EXTF it counts as "exported" when the file is generated and never enters another batch. The file stays on the batch and can be downloaded or re-sent at any time with identical content. Optionally the import can be marked "confirmed by tax office". *(Claude's call)*
- **Corrections** go through the source system: a cancellation invoice creates its own booking with its own document. Invoice and cancellation invoice are both sent, even if the invoice was not yet transferred, because both documents are already in DUo and every booking needs a document. *(Claude's call)*
- **Hard cancel** of an invoice whose document is already uploaded is blocked by the adapter with a pointer to the cancellation invoice. Before upload it is allowed; document and booking are discarded. *(Claude's call)*
- Manually resetting a transferred booking requires a reason in the event log. (In the n8n predecessor, manual resets were routine and a source of duplicates.)

### Visibility

- Overview across all clients: transferred, waiting for approval, stuck, overdue, documents without link, remaining Business OS credits.
- "Overdue": the dispatch date has passed and nothing went out.
- The responsible role/user per client gets a Frappe notification when an approval is due, overdue, or something is stuck. E-mail reminders later.

### ERPNext adapter

- **Sales invoice:** debtor against revenue account, gross, BU key from the tax template (empty for automatic accounts). Several revenue accounts or tax rates produce one booking line per revenue account and tax rate, all with the same invoice number in Belegfeld 1 and the same Beleglink (common for collective invoices, e.g. taxable rent plus tax-exempt insurance). Cancellation invoice (return / credit note) as its own booking with its own document and Belegtyp. Document image is the invoice PDF, with embedded XML when an e-invoicing app provides it. *(Claude's call)*
- **E-invoicing app is optional.** Neither app requires one. The document image comes from a "document image provider" hook: by default the print-format PDF (configurable per doctype) or an attached file; if an e-invoicing app such as `eu_einvoice` is installed, a provider uses its PDF with embedded XML (or the XML file). Detection via installed apps, no hard import.
- **Purchase invoice:** creditor against expense account, input VAT, reverse charge. Documents arrive through an e-invoice parser or OCR; supplier/item/PO matching is our own implementation.
- **Payment (Payment Entry, Bank Transaction):** bank account against party account, collective payments (one line per matched invoice when amounts match exactly, otherwise one collective line), contra account from booking rules where no party is linked.
- **Accounts:** 1:1 by account number (the ERPNext chart uses DATEV numbers). An optional re-mapping table per client covers deviations. *(Claude's call)*
- **BU keys:** a maintained table per client, tax template → BU key. Empty means automatic account, no key. ERPNext posts VAT as its own GL line; the adapter folds it into the revenue line to get the gross amount.
- The client is linked to a Company, party accounts to Customer/Supplier.

### Intake API

Whitelisted methods through which any source (e.g. a SeaTable/n8n setup) can deliver documents and bookings, idempotent via a source key. Part of the core, not required for ERPNext use.

## Constraints

- Frappe v16, Python ≥ 3.14 (ERPNext v16 for the adapter).
- Conventions as in our other Frappe apps (e.g. `frappe-docarchive`): flit/pyproject, ruff (Frappe defaults), release-please, branch `version-16`, CI with bench and MariaDB, tests under `<app>/tests`, README with a "No compliance promise" section. GitHub org `EffizienzAgent`.
- EXTF: cp1252, `;` separator, decimal comma, CRLF. Header `"EXTF";700;21;"Buchungsstapel";…` (format version per current DATEV spec). Beleglink column as `BEDI "GUID"`.
- Published once one closing period has been imported cleanly at a tax office.

## Open questions

1. Which export path is available at the first deployment: DATEVconnect (VPN, own user, test client) or DATEV online rights for Business OS? EXTF works in any case.
2. Does DATEV keep a UUID supplied to Business OS unchanged, and does the Beleglink in DATEV Rechnungswesen open the document image? Test document.
3. Trial import: how does DATEV handle several lines/proposals with the same invoice number (one open item or several)?
4. Duplicate risk: pre-coding / automation service in DUo must be switched off for these documents. Clarify with the tax office.
5. Does on-prem DATEVconnect also accept EXTF files? That would be a further export path.
6. Business OS pricing per client and credit volume.

## Left to the builder

- Doctype names (English). Python package of the core: `datev`.
- Scheduler details, backoff values, overview layout.
- How connector interfaces split into classes/hooks.
- Detailed test strategy. Required: unit tests for the EXTF generator against reference files, for tax template → BU key mapping, and for document type resolution.
