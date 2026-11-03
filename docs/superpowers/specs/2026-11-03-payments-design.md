# Payments and Bank Bookings – Design

Date: 2026-11-03 · Status: draft for approval · Extends `2026-11-02-datev-app-design.md` (core `datev` ≥ 0.2.0, adapter `datev_erpnext` ≥ 0.2.0).

## Intent

After invoices, bookings of money movements must reach DATEV too: customer payments, supplier payments, bank fees, interest and transfers. Then the debtor and creditor accounts in DATEV match the open items, and the tax office gets complete bank bookings without typing them in.

How bank transactions get into ERPNext and how they are matched to invoices is **not** part of this work. That can be a CSV or API import, a separate app or manual entry; whatever it is, it ends in standard ERPNext documents. The DATEV apps start from what is posted in ERPNext.

## Goals

- Every posted ERPNext money movement that touches a bank or cash account reaches DATEV once, with the accounts ERPNext already chose.
- Payments against invoices carry the invoice number in Belegfeld 1, so DATEV clears the open item itself.
- One payment for several invoices becomes one booking line per invoice; an unallocated remainder becomes its own line.
- Deductions on a payment (bank fee, discount, rounding) and bank-only movements (account fees, interest, transfers between own accounts) are booked against the account chosen in ERPNext, with or without a party.
- Same dispatch, approval, duplicate protection, notifications and export paths (EXTF, DATEVconnect) as invoices.

## Non-goals

- Bank import, statement parsing, matching rules, approval of matches.
- Booking rules inside the DATEV apps. ERPNext decides accounts; the apps only translate them.
- Document images for payments (see Constraints).
- Payment initiation, SEPA files, dunning.

## Anti-goals

- A payment reaching DATEV twice, or a cancelled payment staying in DATEV without a reversal.
- A payment booked against an account other than the one in ERPNext.
- Invoice numbers in Belegfeld 1 that differ from what was sent with the invoice, so DATEV cannot clear the open item.
- Payments silently missing (same visibility rules as invoices: catch-up, report, notices).

## Decisions

- **Source of truth for accounts:** ERPNext. Every ERPNext account maps to its DATEV account through its account number (existing core account mapping table for exceptions). Customers → debtor (party account, customer number), suppliers → creditor (party account, supplier number). No booking rules in the DATEV apps.
- **What counts as approved:** a submitted ERPNext document. Drafts never go out.
- **Sources (adapter):**
  - *Payment Entry* (submitted, not opening).
  - *Journal Entry* (submitted, not opening) that touches at least one account of type Bank or Cash.
- **Translation from the GL entries of the voucher** (one method for both sources):
  - The *pivot* is the voucher's bank/cash account (Payment Entry: `paid_to` for Receive, `paid_from` for Pay, the bank side for Internal Transfer; Journal Entry: the first line on a Bank/Cash account).
  - Every other GL entry of the voucher becomes one booking: Konto = the party account (if the GL entry has a party) or the line's account number; Gegenkonto = pivot account number; amount = |debit − credit| in company currency; side from the line's perspective (debit → S, credit → H); Belegfeld 1 = the referenced invoice (`against_voucher` of the GL entry, as sent with that invoice) or else the voucher name; posting text from the party name or the account name.
  - ERPNext already splits party GL entries per allocated invoice and books deductions to their own accounts, so multi-invoice payments, remainders and deductions follow without special cases.
  - Additional bank/cash lines in a Journal Entry (e.g. transfer between own banks) become bookings against the pivot like any other line.
- **No document image for payments** (Claude's call): in DATEV the bank statement is the voucher for bank bookings. Payment documents are containers without a file; the "no booking without Beleglink" rule applies to invoice kinds only.
- **Cancellation:** before transfer, a cancelled payment or journal entry discards its pending bookings (as for invoices). After transfer, cancelling is allowed (reconciliation must stay undoable in ERPNext) and the adapter sends **reversal bookings**: the same lines with the opposite side, Belegfeld 1 unchanged, posting text prefixed "Storno". Amended documents are new documents. (Claude's call: payments have no Beleg to protect, unlike invoices.)
- **Fiscal year:** by the voucher's posting date, like invoices.
- **Re-allocation after transfer** (Payment Reconciliation, Unreconcile Payment, advance allocation change a submitted Payment Entry's GL entries without cancel): the adapter recomputes the voucher's lines after such changes (hooks on Unreconcile Payment / Payment Reconciliation completion, plus the daily catch-up comparing current lines with sent lines). Lines that changed get a reversal of the sent line and a new line; unchanged lines stay. DATEV thus always mirrors ERPNext. (Claude's call)
- **Order:** a payment line that references an invoice waits until that invoice's bookings are Transferred/Exported. Exception: invoices dated before `sync_from_date` (DATEV already has them from the old system) — the line goes out with their number. A referenced invoice that is Stuck or Discarded keeps the payment line waiting with a message. (Claude's call)
- **Supplier payments:** lines referencing Purchase Invoices are held until incoming invoices are synced (separate design, which also fixes their Belegfeld 1). Supplier payments without invoice reference (e.g. advances) follow the general rule. (Claude's call)
- **Party vs. account:** a line books to the party account (debtor/creditor) only if its GL account is of type Receivable/Payable and the party is a Customer/Supplier. Otherwise (separate advance accounts, Employee, Shareholder, …) it books to the line's GL account number. (Claude's call)
- **Reversals** use DATEV's general reversal flag (EXTF column "Generalumkehr (GU)", DATEVconnect `general_reversal`), same side as the original; posting text prefixed "Storno". Cancelling a payment whose bookings are Batched or uncertain is blocked until the batch is sent or resolved. (Claude's call)

## Core changes (`datev`)

- **Bookings without party:** a booking has either a party account (Konto = party number) or a GL account number (`account`), never both; preflight and EXTF/DATEVconnect use whichever is set. Party creation in DATEV still applies when a party account is present.
- **Kinds:** keep `Outgoing Invoice`, `Incoming Invoice`; `Payment` is used for every bank/cash booking (party or not).
- **Documents without file:** a document may have no file; it gets status "Not Required" (new), is never uploaded and never blocks preflight. The Beleglink requirement only applies to invoice kinds.
- **Reversals:** intake can add bookings to an already transferred document as new lines (new line keys); they go into the next batch like any other booking.
- **Dependencies between bookings:** a booking may name the document it depends on (`depends_on_document`); preflight keeps it waiting (Pending with message) until all bookings of that document are Transferred/Exported. Used by the adapter for payment lines that reference invoices.
- **General reversal:** bookings get a `general_reversal` flag, written as "Generalumkehr (GU)" = 1 in EXTF and `general_reversal: true` for DATEVconnect.
- **DATEVconnect export of payments:** `POST …/fiscal-years/{fy}/accounting-sequences` with `record_type: financial_accounting`, `is_committed: false`, one sequence per calendar month per batch (the API requires `date_from`/`date_to` within one accounting period; monthly sequences are the proven pattern). The endpoint answers per sequence without per-item results: a 4xx rejects the whole sequence (all its bookings Failed with the response message), so preflight validates lines strictly before sending; items with account and contra account (technical length), amount, side, date, Belegfeld 1, posting text. Result handling follows the existing rules: certain success/failure per sequence; timeouts/5xx → uncertain, never auto-resent. (Field names and response shape per the DATEVconnect accounting OpenAPI; verify the partial-failure behaviour at the first real DATEVconnect run.)
- **EXTF:** already generic; Konto from party or account.
- **Account mapping:** the existing per-client account mapping table applies to GL accounts on both sides.

## Adapter changes (`datev_erpnext`)

- Settings: `sync_payments` (Check, default off) switches the new sources on; the existing `sync_from_date`, catch-up job and "not in DATEV" report cover Payment Entries and Journal Entries too.
- Hooks: `on_submit` and `before_cancel`/`on_cancel` for Payment Entry and Journal Entry (the latter only when a Bank/Cash account is involved), same queueing, dedup and rollback behaviour as Sales Invoices.
- Source keys `Payment Entry:<name>`, `Journal Entry:<name>`; line keys stable per GL entry (e.g. account + party + against voucher); reversal line keys prefixed `storno:`.
- Status indicator on Payment Entry and Journal Entry forms, like on invoices.
- Validation: GL accounts without numeric account number → rejected with a comment (as for invoices); multi-currency uses company-currency amounts.

## Constraints

- Same conventions as the existing apps (licenses, English code, German translations, no client names, tests, release-please).
- Payments are only correct in DATEV if the invoices they clear were sent with the same Belegfeld 1. For incoming invoices the Belegfeld-1 choice (ERPNext name vs. supplier invoice number) is made in the incoming-invoice design and must be used here too.


## Left to the builder

Doctype field names, report layout, line-key format details, how the pivot is detected for exotic Journal Entries (document the rule and reject what it cannot handle with a clear comment).
