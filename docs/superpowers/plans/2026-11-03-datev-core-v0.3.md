# DATEV Core v0.3.0 Implementation Plan (payments)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The core can carry bank and cash bookings: bookings without a party, documents without a file, dependencies on invoice documents, general reversals, and the DATEVconnect payment export.

**Architecture:** Extend the existing doctypes, intake, preflight, EXTF writer and DATEVconnect connector. Release 0.3.0 via release-please (feat commits).

**Spec:** `docs/superpowers/specs/2026-11-03-payments-design.md` ("Core changes"), base spec `2026-11-02-datev-app-design.md`. Incoming invoices need no core change beyond what the adapter work reveals (`2026-11-03-incoming-invoices-design.md`).

## Global Constraints

- All constraints of the earlier core plans apply (English, German translations for every new string, no client/customer names, no local paths, no third-party DATEV code, Frappe v16, ruff, `IntegrationTestCase`, HTTP mocked via `datev.http.requests.request`, DDL in `setUpClass`).
- Backwards compatible: existing invoice bookings and documents keep working; patches for new fields where needed.
- Conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A payment line that references an invoice still Pending in DATEV must wait (Task 2: `test_dependency_waits`).
2. A cancelled, already transferred payment must produce reversal lines exactly once (Task 3: `test_reversal_lines_once`).
3. A DATEVconnect payment sequence rejected with 4xx marks all its bookings Failed; a timeout keeps them uncertain (Task 4: `test_sequence_rejected`, `test_sequence_timeout_uncertain`).
4. Bookings spanning two months/fiscal years go into separate sequences/files (Task 4: `test_sequences_per_month`).
5. A booking with both or neither of party account and account is rejected (Task 1: `test_party_or_account_required`).

---

### Task 1: Bookings without party, documents without file

**Files:** `datev_booking.json|.py`, `datev_document.json|.py`, `datev/intake.py`, `datev/dispatch.py` (preflight), `datev/extf/booking_batch.py`, `datev/connectors/datevconnect.py` (`proposal_item` stays invoice-only), tests `test_records.py`, `test_dispatch.py`, `test_extf.py`, `test_api.py`

**Decisions:**
- New Booking field `account` (Int, GL account number). Validation: exactly one of `party_account` / `account` (`test_party_or_account_required`); `party_account` required for invoice kinds; `account` allowed only for kind `Payment`. Account remapping applies to `account` too.
- `intake.add_booking(..., party_account=None, account=None, depends_on_document=None, general_reversal=False)`; HTTP API `submit_booking` accepts the same.
- New Document status `Not Required`: `intake.upsert_document(...)` accepts `content=None`/no file → status Not Required, never uploaded; preflight treats it as linked-not-needed.
- Beleglink requirement (`require_document_link`) applies only to invoice kinds.
- EXTF: Konto = party number or `account`; Beleglink column empty when no link.
- [ ] Tests: `test_party_or_account_required`, `test_account_only_for_payment`, `test_document_without_file_not_required`, `test_payment_ignores_link_requirement`, `test_extf_account_line` → commit `feat: support bookings without party and documents without file`.

### Task 2: Dependencies between bookings

**Files:** `datev_booking.json`, `datev/dispatch.py`, tests `test_dispatch.py`

**Decisions:**
- Booking field `depends_on_document` (Link DATEV Document). Preflight: if set and not all bookings of that document are Transferred/Exported → keep Pending with message `_("Waiting for document {0} to reach DATEV")`; if that document is Stuck/Discarded → same, message names the status. All-or-nothing per document still applies (a waiting line keeps its whole document waiting).
- [ ] Tests: `test_dependency_waits`, `test_dependency_released_after_transfer`, `test_dependency_on_discarded_waits_with_message` → commit `feat: let bookings wait for the documents they depend on`.

### Task 3: General reversal and reversal lines

**Files:** `datev_booking.json`, `datev/intake.py`, `datev/extf/booking_batch.py`, `datev/connectors/datevconnect.py`, tests

**Decisions:**
- Booking field `general_reversal` (Check). EXTF column "Generalumkehr (GU)" = `1` when set (find the exact column in `COLUMNS`); DATEVconnect: `general_reversal: true` on the item.
- `intake.add_booking` on a document whose other bookings are already Transferred/Exported is allowed (new line keys); idempotent per line key as before (`test_reversal_lines_once`).
- `intake.discard_document` unchanged for invoices; new `intake.discard_open_bookings(source_key)` deletes only open bookings and leaves transferred ones (used for payment cancels); raises a translated error if any booking is Batched in an Approved/Sending batch.
- [ ] Tests: `test_general_reversal_extf`, `test_general_reversal_datevconnect_item`, `test_reversal_lines_once`, `test_discard_open_bookings_blocks_batched` → commit `feat: general reversal flag and reversal lines`.

### Task 4: DATEVconnect payment export

**Files:** `datev/connectors/datevconnect.py`, `datev/clients/datevconnect.py`, tests `test_export_connectors.py`, `test_datevconnect_client.py`

**Decisions:**
- `Payment` bookings are exported via `post_accounting_sequence(client_id, fiscal_year_id, payload)`: one sequence per calendar month (and fiscal year) per batch, payload per the DATEVconnect accounting OpenAPI (`record_type: "financial_accounting"`, `is_committed: false`, `date_from`/`date_to` = month bounds clipped to the bookings, `accounting_records` with account/contra account at technical length, amount, debit/credit, date, `document_field1`, `posting_description`, `general_reversal`, document system/link when present). Read the OpenAPI in the repo's reference location used before (ask the controller if not available) for exact field names.
- Result mapping per sequence: 2xx → all its bookings ok; 4xx → all Failed with the response message; timeout/≥500/maybe_sent → uncertain (existing rule). Invoice proposals unchanged.
- Remove the "Payments are not supported yet" rejection; preflight check `check_booking` validates payment lines strictly (account numbers present, amount > 0, fiscal year open).
- [ ] Tests: `test_sequences_per_month`, `test_sequence_payload`, `test_sequence_rejected`, `test_sequence_timeout_uncertain`, `test_mixed_batch_invoices_and_payments` → commit `feat: export payments to DATEVconnect as accounting sequences`.

### Task 5: Docs, translations

`docs/concepts.md` (payments, dependencies, reversals), `docs/connectors.md` (DATEVconnect payments), `docs/extf.md` (GU column), `docs/intake-api.md` (new parameters), README feature list; translations; full suite + ruff → commit `docs: payments in the core`.
