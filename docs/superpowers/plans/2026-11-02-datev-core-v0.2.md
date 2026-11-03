# DATEV Core v0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps the pre-go-live review found in `datev` v0.1.0: nothing may be lost, sent twice, sent half, or booked with a guessed tax key, and the EXTF path must tell people when a file is ready.

**Architecture:** Changes stay inside the existing modules (`dispatch.py`, `parties.py`, `documents.py`, `intake.py`, connectors, doctypes). No new doctypes; new fields on DATEV Batch and DATEV Client. Release via release-please as 0.2.0 (feat commits).

**Tech Stack:** Frappe v16, Python 3.14, existing dev bench (`dev/compose.yaml`, `dev/test.sh`).

**Spec:** `docs/superpowers/specs/2026-11-02-datev-app-design.md` (goals, anti-goals, duplicate protection, visibility). Review source: the pre-go-live review of v0.1.0.

## Global Constraints

- All constraints of the v0.1 plan still apply (English code/docs/commits, every UI string translated in `datev/locale/de.po`, no client names, no third-party DATEV code, Frappe v16, ruff config, tests under `datev/tests` with `IntegrationTestCase`, HTTP mocked via `datev.http.requests.request`).
- Commit messages: conventional commits (`feat:`/`fix:`/`docs:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backwards compatible for existing sites: new fields get defaults, a patch migrates data where needed (`datev/patches.txt`).
- German glossary changes: "Stuck" → "Blockiert", "Release" (un-hold) → "Zurückhalten aufheben", "Mandate" → "Mandantendaten"; keep the rest of the v0.1 glossary.

## Review Focus

1. A weekly EXTF batch nobody downloads: the responsible person must be notified and the batch must stay visible until confirmed (Task 4: `test_file_ready_notifies`, `test_unconfirmed_in_report`).
2. A collective invoice where one line is blocked: no line of that document may be sent (Task 2: `test_document_all_or_nothing`).
3. A tax code without a tax-key row: the booking must not go out (Task 1: `test_missing_tax_key_blocks`).
4. Debtor matched by number with a different name: must go to "To Clarify" (Task 3: `test_number_match_name_mismatch_to_clarify`).
5. A big dispatch (many parties/bookings) must not die at the 300 s cron timeout (Task 2: `test_run_enqueues_per_client`).

---

### Task 1: Booking integrity

**Files:** Modify `datev/datev/doctype/datev_booking/datev_booking.json|.py`, `datev_batch.json`, `datev_party_account.json`, `datev_client.json`, `datev/intake.py`, `datev/dispatch.py` (`MANDATORY_FIELDS`), tests `datev/tests/test_records.py`, `test_dispatch.py`, `test_protect.py`

**Interfaces / decisions:**
- `DATEV Booking.kind` and `debit_credit`: `reqd: 1`; add both to `MANDATORY_FIELDS` in preflight.
- Permissions: DATEV User gets read only on DATEV Booking and DATEV Batch (no write). Hold/approve/send go through the existing whitelisted methods (they already check roles). `held` becomes `read_only` in the JSON (only `dispatch.hold` changes it via `db_set`).
- `track_changes: 1` on DATEV Booking, DATEV Batch, DATEV Party Account, DATEV Client.
- Tax keys: `intake.add_booking(..., tax_code=...)`: if `tax_code` is given and the client has no `tax_keys` row for it → `frappe.ValidationError(_("Tax code {0} has no row in the DATEV Client's tax keys. Add it; leave the BU key empty for automatic accounts."))`. An explicit row with empty `bu_key` stays "automatic account".

- [ ] Tests: `test_kind_and_side_required`, `test_user_cannot_write_booking` (user with DATEV User only → PermissionError on save), `test_held_only_via_hold`, `test_missing_tax_key_blocks`, `test_empty_bu_key_row_allowed` → FAIL → implement → PASS → commit `feat: enforce booking integrity and strict tax keys`.

### Task 2: Dispatch in the background, all-or-nothing per document

**Files:** Modify `datev/dispatch.py`, `datev/hooks.py`, `datev/datev/doctype/datev_client/datev_client.py|.js` (send_now), tests `test_dispatch.py`

**Interfaces / decisions:**
- `dispatch.run()` (cron hourly) only decides what is due and enqueues `datev.dispatch.process_client(client: str)` per due client and per Approved batch `datev.dispatch.send(batch)`, both on queue `long`, `job_id=f"datev-client-{client}"` / `f"datev-send-{batch}"`, `deduplicate=True`, timeout 3600.
- `process_client(client)` = `collect(client)` then `send` for the resulting batch if Approved.
- `send_now(client)` enqueues `process_client` (no DATEV calls inside the web request).
- All-or-nothing: in `collect`, group candidate bookings by `document`; if any booking of a document fails preflight or is held, none of that document's bookings are batched; the ready ones get status Pending with message `_("Waiting for line {0} of this document")`. `hold` holds all bookings of the same document together.
- Whitelisted state-changing functions accept POST only: `send_now`, `approve`, `hold`, `resolve_batch`, `reset_booking`, `documents.retry`, `parties.retry_party`, client `import_parties`, `import_parties_from_file`, `refresh_document_types`.

- [ ] Tests: `test_run_enqueues_per_client` (no connector call inside run), `test_send_now_enqueues`, `test_document_all_or_nothing`, `test_hold_holds_whole_document`, `test_post_only` (check `frappe.whitelist` metadata `allowed_http_methods`) → implement → commit `feat: dispatch in background jobs and all-or-nothing per document`.

### Task 3: Safer party matching

**Files:** Modify `datev/parties.py`, `datev/extf/master_data.py` (`parse` returns header info), `datev_client.py`, `datev_party_account.js|.py`, tests `test_parties.py`, `test_extf.py`

**Interfaces / decisions:**
- Number matching (`match_*_by_number`) links only if the normalized names agree or the VAT IDs agree; otherwise "To Clarify" with message naming both names.
- `master_data.parse(content) -> tuple[dict, list[RemoteParty]]` where the dict holds `consultant_number`, `client_number`, `account_length` from the header; `import_parties_from_file` rejects a file whose consultant/client/account length differ from the client (`frappe.ValidationError` naming both values). Adjust callers.
- "Mark resolved" (party account form): only DATEV Manager; writes an event log entry; may set Imported (existing account) or Created (new account confirmed).
- `retry_party` on a name-mismatch To Clarify does not loop silently: it keeps To Clarify and re-states the mismatch.

- [ ] Tests: `test_number_match_name_mismatch_to_clarify`, `test_number_match_vat_agrees_links`, `test_file_from_other_client_rejected`, `test_mark_resolved_logs`, `test_mark_resolved_manager_only` → implement → commit `fix: match parties by number only when names agree`.

### Task 4: EXTF lifecycle

**Files:** Modify `datev_batch.json|.py|.js`, `datev/dispatch.py` (`record`, `resolve_batch`), `datev/connectors/extf.py`, `datev/notify.py`, `datev/datev/report/datev_status/*`, tests `test_dispatch.py`, `test_notify.py`, `test_export_connectors.py`

**Interfaces / decisions:**
- New DATEV Batch fields: `confirmed_on` Datetime, `confirmed_by` Link User (read only).
- `dispatch.confirm_import(batch: str)` whitelisted POST, DATEV User or Manager: only for status Sent/Partially Sent with an EXTF connector; sets confirmed fields, event `import_confirmed`. Button "Confirm import by tax office" on the batch form.
- `dispatch.resend_extf_email(batch: str)` whitelisted POST: re-sends the attached files via the client's `extf_email` (only EXTF e-mail connector batches). Button on the form.
- After an EXTF export: Notification Log to `notify.recipients(client)` "DATEV file ready: {batch}" with link to the batch (once per batch).
- `resolve_batch` for connectors with `remote = False` (EXTF): "delivered" is not allowed (`frappe.ValidationError`); "not delivered" resets bookings to Pending as today.
- Report: new column `unconfirmed_batches` (EXTF batches Sent/Partially Sent without `confirmed_on`, older than 7 days count as problem); include Failed and Partially Sent batches in counts; include disabled clients greyed (row flag `enabled`).
- Daily stuck summary mentions unconfirmed EXTF batches older than 7 days.

- [ ] Tests: `test_file_ready_notifies`, `test_confirm_import`, `test_resend_extf_email`, `test_resolve_extf_delivered_rejected`, `test_unconfirmed_in_report` → implement → commit `feat: track EXTF files until the tax office confirms the import`.

### Task 5: Upload robustness

**Files:** Modify `datev/documents.py`, `datev/connectors/business_os.py`, `datev/clients/business_os.py`, `datev_document.js` (new), tests `test_documents.py`, `test_storage_connectors.py`, `test_business_os_client.py`

**Interfaces / decisions:**
- `mark_stuck`/backoff writes only `where status = 'Pending'` (use `frappe.db.set_value` with a status condition via SQL `UPDATE … WHERE name=%s AND status='Pending'`); an Uploaded document is never downgraded.
- `documents.retry(name)` re-resolves the document type, resets to Pending and enqueues the upload (queue short); Button "Retry upload" on Stuck documents (DATEV User/Manager).
- `documents.mark_uploaded(name, external_id)` whitelisted POST, DATEV Manager: for a Stuck document that is already in DUo, sets external_id/program_code (`BEDI`)/Uploaded with an event log reason (required).
- Business OS returned-ID mode: HTTP ≥ 500 on upload → non-retryable, Stuck with message "Upload may have reached DATEV. Check DUo before retrying." (same as maybe_sent).
- Business OS own-ID mode: if the response `id` exists and differs from the sent GUID (case-insensitive) → Stuck with message naming both IDs (Beleglink would be wrong).
- Throttle: at most one Business OS upload per 5 s per client (`frappe.cache` key with timestamp; if too early, re-enqueue with `next_attempt = now + 5 s` instead of sleeping).

- [ ] Tests: `test_stuck_never_overwrites_uploaded`, `test_retry_enqueues_and_reresolves`, `test_mark_uploaded`, `test_returned_id_5xx_stuck`, `test_own_id_mismatch_stuck`, `test_throttle_defers` → implement → commit `fix: harden document uploads`.

### Task 6: Translations, docs, release notes

**Files:** `datev/locale/main.pot|de.po`, `datev/notify.py` / event log (human-readable event labels: map event codes to translated labels in the Event Log list via a `title` or `label` field set on insert), `README.md`, `docs/*.md`

**Decisions:**
- Glossary changes from Global Constraints; event log gets a translated `label` (Data) filled from a mapping in `datev/events.py`.
- Docs fixes: `intake-api.md` status example `BEDI`; integration role table (DATEV User read-only + methods); concepts ("DATEV is called only in background jobs" now true); EXTF "Exported ≠ imported; confirm import"; master data must be imported before bookings; retry button; notification wording ("once per batch").
- Full suite + ruff green; commit `docs: update docs and translations for 0.2.0`.
