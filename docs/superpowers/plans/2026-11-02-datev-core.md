# DATEV Core App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first release of the Frappe-only core app `datev`: neutral data model, Business OS and e-mail document storage, DATEVconnect and EXTF export, party accounts with initial matching, scheduled dispatch with approval, visibility, intake API, German translations.

**Architecture:** One Frappe app (`datev`, module `DATEV`) in this repo. Source systems (the ERPNext adapter, planned separately) call `datev.intake` to register documents, bookings and parties. Connectors are classes resolved through hooks by the label stored on the DATEV Client. A scheduler collects pending bookings into batches, waits for approval if configured, and hands batches to the export connector; documents are uploaded immediately after intake.

**Tech Stack:** Frappe v16, Python 3.14, `requests` (ships with Frappe), `babel` (ships with Frappe) for translation tests, MariaDB 11.8, Docker dev bench.

**Spec:** `docs/superpowers/specs/2026-11-02-datev-app-design.md`

**Reference material (local, not committed):**
- DATEV format description (EXTF header, Buchungsstapel v13 with 125 columns, Debitoren/Kreditoren v5, charset): `/Users/tobi/Projekte/Fleetgroup/datev-recherche/datev-format-spec.json` (public DATEV developer portal content; extract column names from it, do not commit the file).
- DATEVconnect accounting OpenAPI 1.7.4: `/Users/tobi/Projekte/Fleetgroup/datev-recherche/openapi/datevconnect-accounting-1.7.4.json`.
- Business OS OpenAPI: https://docs.business-os.de/api-reference/openapi.json
- Repo conventions: `/Users/tobi/Projekte/frappe-docarchive` (pyproject, CI, release, README shape).

## Global Constraints

- Code, identifiers, doctype/field names, docs and commit messages in English. Every user-facing string (doctype/field labels, Select options, descriptions, `_()` messages, notifications) has a German translation in `datev/locale/de.po`.
- No client or customer names anywhere in the repo (code, tests, fixtures, docs, commits). Use DATEV's sample numbers (consultant 29098, client 55003) in tests.
- No code copied from other DATEV or e-invoicing apps. EXTF code is written from DATEV's format description.
- Frappe v16 only, `requires-python = ">=3.14"`, flit build, ruff config identical to docarchive (tabs, line length 110, same select/ignore lists), `app_license = "mit"`, publisher "EffizienzAgent (Tobias Mende)", email `oss@effizienzagent.de`.
- Core depends on Frappe only. No `import erpnext` anywhere.
- EXTF: cp1252, `;` separator, decimal comma, CRLF after every line, text fields quoted with inner quotes doubled, no control characters in text fields. Header row starts `"EXTF";700;`. Buchungsstapel = category 21, format version 13; Debitoren/Kreditoren = category 16, format version 5.
- Beleglink in EXTF: field content `BEDI "<GUID>"`. In DATEVconnect: `document_system` = program code, `document_link` = ID upper-cased.
- Business OS: base `https://api.business-os.de`, header `x-api-key`, query `connectionId` on every call and `quelle=frappe-datev`.
- DATEVconnect: base URL per client (e.g. `https://host:58452/datev/api/accounting/v1`), HTTP basic auth, TLS verification switchable per client.
- Credentials are `Password` fields; never logged, never stored in the event log.
- A transferred or exported booking is never sent again.
- Tests: `frappe.tests.IntegrationTestCase`, under `datev/tests/`, HTTP mocked by patching `datev.http.requests.request`. Run via `dev/test.sh`.

## Review Focus

1. **Concurrent or repeated dispatch** (scheduler fires twice, user clicks "Send" during a scheduled run, retry after a partially rejected DATEVconnect batch): a booking must reach DATEV exactly once. Pinned in Task 13 (`test_send_twice_sends_once`, `test_partial_failure_resends_only_failed`).
2. **A batch spanning two fiscal years** (bookings from Dec and Jan in one weekly batch): DATEVconnect needs one call per fiscal year, EXTF one file per fiscal year with matching header dates. Pinned in Task 9 (`test_split_by_fiscal_year`) and Task 11 (`test_groups_by_fiscal_year_and_direction`).
3. **Hostile text** in posting texts, party names and invoice numbers (quotes, semicolons, line breaks, emoji, characters outside cp1252, overlong values, invoice numbers with spaces or `#`): EXTF must stay parseable and DATEV's length/charset limits must hold. Pinned in Task 9 (`test_text_sanitising`, `test_document_field_1_charset`).
4. **Cancellation invoices** (credit notes): sign/direction must invert correctly in EXTF (S/H flip, positive amount) and DATEVconnect (negative amount). Pinned in Task 9 and Task 11 (`test_credit_note_sign`). Sign behaviour in DATEVconnect is confirmed at the trial import (spec open question 3).
5. **Business OS rate limit or exhausted credits** (HTTP 429, 402/403 with credit code, 5xx, timeouts): the document stays pending and retries with backoff; it is only "Stuck" after the last attempt, and low credits raise a notification. Pinned in Task 6 (`test_rate_limit_is_retryable`) and Task 8 (`test_backoff_then_stuck`).

---

## File Structure

```
pyproject.toml, license.txt, README.md, .gitignore, .editorconfig, renovate.json,
release-please-config.json, .release-please-manifest.json, CHANGELOG.md (generated later)
.github/versions.env, .github/workflows/{test.yml,release.yml}
dev/compose.yaml, dev/bootstrap.sh, dev/test.sh
docs/{concepts.md,connectors.md,extf.md,intake-api.md,development.md}
datev/__init__.py                 version
datev/hooks.py                    app metadata, connector registry hooks, scheduler, roles
datev/modules.txt                 "DATEV"
datev/patches.txt
datev/http.py                     thin requests wrapper -> HttpResponse / TransportError
datev/events.py                   append-only event log helper
datev/beleglink.py                GUIDs and Beleglink formatting
datev/fiscal.py                   fiscal year start / id per client
datev/document_types.py           Belegtyp resolution
datev/intake.py                   Python API for source systems (documents, bookings, parties)
datev/api.py                      whitelisted HTTP wrappers around intake
datev/documents.py                upload pipeline with retry/backoff
datev/parties.py                  number assignment, DATEV sync, initial matching
datev/dispatch.py                 batching, preflight, approval, sending, scheduler entry
datev/notify.py                   notifications to responsible user/role
datev/clients/business_os.py      Business OS HTTP client
datev/clients/datevconnect.py     DATEVconnect HTTP client
datev/connectors/base.py          connector interfaces + result types + ConnectorError
datev/connectors/registry.py      label -> class resolution via hooks
datev/connectors/business_os.py   Business OS storage
datev/connectors/duo_email.py     e-mail to DUo storage
datev/connectors/datevconnect.py  DATEVconnect export + master data
datev/connectors/extf.py          EXTF download/e-mail export + EXTF master data
datev/extf/format.py              field formatting + encoding
datev/extf/header.py              EXTF header row
datev/extf/booking_batch.py       Buchungsstapel (21/13)
datev/extf/master_data.py         Debitoren/Kreditoren (16/5) build + parse
datev/datev/doctype/<doctypes>/   DATEV Client (+5 child tables), DATEV Document, DATEV Booking,
                                  DATEV Batch, DATEV Party Account, DATEV Event Log
datev/datev/workspace/datev/datev.json
datev/datev/report/datev_status/  script report: overview across clients
datev/locale/main.pot, datev/locale/de.po
datev/tests/factories.py + test_*.py
```

Roles (created in `datev/setup.py` `after_install`/`after_migrate`): `DATEV Manager` (configure clients, everything), `DATEV User` (see, approve, hold, retry).

---

### Task 1: App scaffold, dev bench, CI

**Files:**
- Create: everything top-level from the File Structure list except `docs/*.md` content beyond a stub `docs/development.md`; `datev/__init__.py` (`__version__ = "0.1.0"`), `datev/hooks.py`, `datev/modules.txt`, `datev/patches.txt`, `datev/datev/__init__.py`, `datev/setup.py`, `datev/tests/__init__.py`, `datev/tests/test_install.py`
- Copy-adapt from `/Users/tobi/Projekte/frappe-docarchive`: `pyproject.toml` (name `datev`, description "DATEV integration for the Frappe Framework: documents with Beleglink, EXTF and DATEVconnect", no extra dependencies), `.editorconfig`, `.gitignore` (`datev/public/dist/`), `renovate.json` (only the frappe + frappe-bench managers), `release-please-config.json`, `.release-please-manifest.json` (`{".": "0.1.0"}`), `.github/workflows/release.yml` (unchanged), `.github/workflows/test.yml` (no MinIO/s3storage/JS steps, `SITE: datev.test`, app `datev`), `.github/versions.env` (only `FRAPPE_BRANCH=version-16` and the same `FRAPPE_COMMIT` as docarchive), `license.txt` (MIT, "EffizienzAgent (Tobias Mende)").
- Copy-adapt from `/Users/tobi/Projekte/inocea-crm/dev/`: `dev/compose.yaml` (project name `frappe-datev`, mount `..:/workspace/frappe-datev`, port `8010:8000`), `dev/bootstrap.sh` (no pinned extra apps; `bench get-app --soft-link /workspace/frappe-datev`; site `datev.test`; install `datev`; `allow_tests`, `developer_mode`), `dev/test.sh` (runs `bench --site datev.test run-tests --app datev "$@"`).

**Interfaces:**
- Produces: `datev.hooks` keys `app_name = "datev"`, `app_title = "DATEV"`, `after_install = after_migrate = "datev.setup.apply_defaults"`; `datev.setup.apply_defaults() -> None` creates roles `DATEV Manager`, `DATEV User` if missing.

- [ ] **Step 1: Write the scaffold files listed above.** `hooks.py` holds only metadata and the setup hooks for now.
- [ ] **Step 2: Write `datev/tests/test_install.py`**

```python
class TestInstall(IntegrationTestCase):
	def test_app_installed(self):
		self.assertIn("datev", frappe.get_installed_apps())

	def test_roles_created(self):
		for role in ("DATEV Manager", "DATEV User"):
			self.assertTrue(frappe.db.exists("Role", role))
```

- [ ] **Step 3: Start the bench and bootstrap**

Run: `docker compose -f dev/compose.yaml up -d && docker compose -f dev/compose.yaml exec bench bash /workspace/frappe-datev/dev/bootstrap.sh`
Expected: ends without error; `bench --site datev.test list-apps` shows `datev`.

- [ ] **Step 4: Run tests** — `dev/test.sh` → 2 tests PASS.
- [ ] **Step 5: Lint** — `ruff check . && ruff format --check .` → clean.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "chore: scaffold datev app with dev bench and CI"` (spec/plan docs included).

---

### Task 2: DATEV Client and its child tables

**Files:**
- Create: `datev/datev/doctype/datev_client/{datev_client.json,datev_client.py,datev_client.js,__init__.py}`; child doctypes `datev_document_type_option`, `datev_document_type_rule`, `datev_tax_key_rule`, `datev_account_mapping` (each `.json`, `.py`, `__init__.py`, `istable: 1`); `datev/tests/factories.py`; `datev/tests/test_client.py`

**Interfaces:**
- Produces doctype `DATEV Client` (naming `field:title`, permissions: DATEV Manager full, DATEV User read) with fields:
  - `title` Data reqd unique · `enabled` Check default 1 · `company` Data ("Company in the source system")
  - `consultant_number` Int reqd · `client_number` Int reqd · `account_length` Int default 4 · `chart` Select `SKR03\nSKR04` reqd · `fiscal_year_start_month` Int default 1
  - `storage_connector` Select `Business OS\nE-mail to DUo` · `export_connector` Select `DATEVconnect\nEXTF download\nEXTF e-mail` · `master_data_connector` Select `DATEVconnect\nEXTF`
  - Business OS: `bos_api_key` Password · `bos_connection_id` Data · `bos_id_mode` Select `Send own ID\nUse returned ID` default `Send own ID` · `bos_credits` Int read_only · `bos_credit_warning` Int default 100
  - `duo_email` Data (options Email) · `extf_email` Data (options Email)
  - DATEVconnect: `dc_base_url` Data · `dc_username` Data · `dc_password` Password · `dc_verify_tls` Check default 1 · `dc_client_id` Data read_only
  - `document_type_options` Table `DATEV Document Type Option` (`document_type` Data reqd, `category` Data) · `document_type_rules` Table `DATEV Document Type Rule` (`direction` Select `Outgoing\nIncoming\nOther` reqd, `source_doctype` Data, `is_return` Select `Any\nYes\nNo` default Any, `document_type` Data reqd)
  - Dispatch: `dispatch_frequency` Select `Daily\nWeekly\nMonthly` default Weekly · `dispatch_weekday` Select `Monday\n…\nSunday` default Monday · `dispatch_day_of_month` Int default 1 · `approval_required` Check default 1 · `responsible_role` Link Role · `responsible_user` Link User · `require_document_link` Check default 1 · `last_dispatch_on` Datetime read_only
  - Accounts: `debtor_numbering` Select `Source system\nDATEV next available` default `Source system` · `creditor_numbering` same, default `DATEV next available` · `match_debtors_by_number` Check default 1 · `match_creditors_by_number` Check default 0 · `tax_keys` Table `DATEV Tax Key Rule` (`tax_code` Data reqd, `bu_key` Data) · `account_mappings` Table `DATEV Account Mapping` (`source_account` Int reqd, `datev_account` Int reqd)
- `DATEVClient.validate()` rules (each a `frappe.ValidationError` with a translated message):
  - `4 <= account_length <= 8`; `1 <= fiscal_year_start_month <= 12`; `1 <= dispatch_day_of_month <= 28`.
  - `storage_connector == "Business OS"` requires `bos_api_key`, `bos_connection_id`; `"E-mail to DUo"` requires `duo_email`.
  - `export_connector == "DATEVconnect"` or `master_data_connector == "DATEVconnect"` require `dc_base_url`, `dc_username`, `dc_password`; `"EXTF e-mail"` requires `extf_email`.
  - `master_data_connector == "EXTF"` forbids `"DATEV next available"` in either numbering field.
  - `bu_key` matches `^\d{0,4}$`; duplicate `tax_code` or `source_account` rows rejected.
- `datev/tests/factories.py`: `make_client(**overrides) -> Document` — inserted client with consultant 29098, client 55003, SKR04, storage "Business OS" (dummy key/connection), export "EXTF download", master data "EXTF", unique title.

- [ ] **Step 1: Write failing tests** in `test_client.py`: `test_defaults` (account_length 4, Weekly, approval on, strict link on), one test per validation rule above (`test_account_length_range`, `test_business_os_requires_credentials`, `test_datevconnect_requires_credentials`, `test_extf_master_data_forbids_datev_numbering`, `test_bu_key_digits_only`, `test_duplicate_tax_code_rejected`).
- [ ] **Step 2: Run** `dev/test.sh --module datev.tests.test_client` → FAIL (doctype missing).
- [ ] **Step 3: Create doctype JSONs and `DATEVClient.validate`**; `datev_client.js` hides connector sections that don't match the selected connector (`depends_on` in JSON is enough; JS only for the buttons added in Tasks 7 and 12).
- [ ] **Step 4: Run** → PASS. `bench --site datev.test migrate` clean.
- [ ] **Step 5: Commit** `feat: add DATEV Client with connector, dispatch and account settings`

---

### Task 3: Documents, bookings, batches, party accounts, event log

**Files:**
- Create doctypes: `datev_document`, `datev_booking`, `datev_batch`, `datev_party_account`, `datev_event_log`; `datev/events.py`; `datev/beleglink.py`; `datev/fiscal.py`; `datev/tests/test_records.py`

**Interfaces:**
- `DATEV Document` (autoname `hash`): `client` Link reqd · `source_key` Data reqd unique · `direction` Select `Outgoing\nIncoming\nOther` · `document_type` Data · `is_return` Check · `source_doctype` Link DocType · `source_name` Dynamic Link · `file` Attach · `file_name` Data · `document_date` Date · `document_number` Data · `amount` Currency · `currency` Data default EUR · `guid` Data · `external_id` Data · `program_code` Data · `status` Select `Pending\nUploaded\nNo Link\nStuck\nDiscarded` default Pending · `attempts` Int · `next_attempt` Datetime · `message` Small Text.
- `DATEV Booking` (autoname `hash`): `client` Link reqd · `document` Link `DATEV Document` reqd · `line_key` Data reqd (unique together with `document`, enforced in `validate`) · `kind` Select `Outgoing Invoice\nIncoming Invoice\nPayment` · `party_account` Link `DATEV Party Account` · `contra_account` Int · `debit_credit` Select `S\nH` · `amount` Currency (> 0) · `currency` Data · `bu_key` Data · `tax_rate` Float · `posting_text` Data · `document_date` Date · `document_field_1` Data · `due_date` Date · `fiscal_year` Data (`YYYYMMDD` of FY start, set in `validate` via `datev.fiscal.fiscal_year_id`) · `status` Select `Pending\nBatched\nStuck\nTransferred\nExported\nFailed` default Pending · `held` Check · `batch` Link `DATEV Batch` · `message` Small Text.
- `DATEV Batch` (naming `DATEV-BATCH-.YYYY.-.#####`): `client` Link reqd · `export_connector` Data · `status` Select `Awaiting Approval\nApproved\nSending\nSent\nPartially Sent\nFailed` · `approved_by` Link User · `approved_on` Datetime · `sent_on` Datetime · `file` Attach (first EXTF file; further files as attachments) · `notified_on` Datetime · `message` Small Text.
- `DATEV Party Account` (autoname `hash`): `client` Link reqd · `kind` Select `Debtor\nCreditor` reqd · `party_type` Link DocType · `party` Dynamic Link · `party_name` Data reqd · `vat_id` Data · `iban` Data · `preferred_number` Int · `account_number` Int · `sync_status` Select `New\nCreated\nImported\nTo Clarify\nError` default New · `message` Small Text. `validate`: unique (`client`, `kind`, `account_number`) when set; unique (`client`, `kind`, `party_type`, `party`) when party set; debtor numbers within `10000..69999`, creditor numbers within `70000..99999` scaled to `account_length + 1` digits (for length 4: 5 digits; length n: `10**n` to `7*10**n - 1` and `7*10**n` to `10**(n+1) - 1`).
- `DATEV Event Log` (autoname `hash`, `in_create: 1`, no edit/delete for anyone): `reference_doctype` Link DocType · `reference_name` Dynamic Link · `event` Data · `message` Small Text · `response` Code (JSON).
- `datev.events.log_event(reference_doctype: str, reference_name: str, event: str, message: str = "", response: object = None) -> None` — JSON-dumps `response` (truncated to 20 000 chars).
- `datev.beleglink.new_guid() -> str` (uuid4, lowercase, with dashes); `datev.beleglink.extf_value(program_code: str, external_id: str) -> str` returns `f'{program_code} "{external_id}"'`.
- `datev.fiscal.fiscal_year_start(client, on: date) -> date`; `datev.fiscal.fiscal_year_id(client, on: date) -> str` (`"%Y%m%d"` of the start).
- Extend `datev/tests/factories.py` with `make_document(client, **overrides) -> Document`, `make_party_account(client, kind="Debtor", **overrides) -> Document`, `make_booking(document, party_account, **overrides) -> Document` (inserted with valid defaults; later tasks reuse them).

- [ ] **Step 1: Failing tests** in `test_records.py`: `test_booking_line_key_unique_per_document`, `test_booking_amount_positive`, `test_booking_sets_fiscal_year` (start month 1, date 2027-01-05 → `"20270101"`; start month 7, date 2027-03-01 → `"20260701"`), `test_party_account_number_unique`, `test_party_account_number_ranges` (debtor 10000 ok, 9999 rejected, creditor 70000 ok, 69999 rejected), `test_event_log_not_editable`, `test_extf_beleglink_value` (`extf_value("BEDI", "ABC") == 'BEDI "ABC"'`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement doctypes and helpers.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: add document, booking, batch, party account and event log doctypes`

---

### Task 4: Document type resolution

**Files:** Create `datev/document_types.py`, `datev/tests/test_document_types.py`

**Interfaces:**
- `DEFAULT_TYPES: dict[str, str] = {"Outgoing": "Rechnungsausgang", "Incoming": "Rechnungseingang", "Other": "Sonstige"}`
- `class DocumentTypeError(frappe.ValidationError)`
- `resolve(client, direction: str, source_doctype: str | None, is_return: bool) -> str` — first matching rule in table order wins (rule matches when `direction` equal, `source_doctype` empty or equal, `is_return` Any or equal); no rule → `DEFAULT_TYPES[direction]`. If `client.document_type_options` is non-empty, the result must be one of its `document_type` values, else `DocumentTypeError` naming the type.

- [ ] **Step 1: Failing tests:** `test_default_per_direction`, `test_rule_order_first_match`, `test_return_rule` (rule Outgoing/Yes → "Gutschrift/Storno" wins only for returns), `test_unknown_type_rejected_when_options_present`, `test_any_type_allowed_without_options`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: resolve document types per client`

---

### Task 5: HTTP wrapper, connector interfaces, registry

**Files:** Create `datev/http.py`, `datev/connectors/__init__.py`, `datev/connectors/base.py`, `datev/connectors/registry.py`, `datev/tests/test_registry.py`; Modify `datev/hooks.py`

**Interfaces:**
- `datev.http`: `@dataclass HttpResponse(status: int, data: object | None, text: str)`; `class TransportError(Exception)`; `request(method: str, url: str, *, headers=None, params=None, json=None, auth=None, verify: bool = True, timeout: int = 30) -> HttpResponse` — wraps `requests.request`; `data` is parsed JSON or `None`; `requests.RequestException` → `TransportError`.
- `datev.connectors.base`:
  - `class ConnectorError(Exception)` with `__init__(self, message: str, *, retryable: bool = False, response: object = None)`.
  - `@dataclass StorageResult(external_id: str | None, program_code: str | None, response: object = None)`
  - `@dataclass ExportItemResult(booking: str, ok: bool, message: str = "")`
  - `@dataclass ExportResult(items: list[ExportItemResult], files: list[str] = field(default_factory=list), response: object = None)` (`files` = File URLs)
  - `@dataclass RemoteParty(kind: str, account_number: int, name: str, vat_id: str | None = None, iban: str | None = None)`
  - Abstract classes, each constructed with the client doc: `StorageConnector.upload(self, document) -> StorageResult`; `StorageConnector.document_types(self) -> list[dict]` (default `[]`); `ExportConnector.export(self, batch, bookings: list) -> ExportResult` with class attribute `final_status: str = "Transferred"` (status a successfully exported booking gets); `MasterDataConnector.fetch_parties(self) -> list[RemoteParty]`, `.next_number(self, kind: str) -> int`, `.create_party(self, party_account) -> None`, class attributes `creates_via_export: bool = False` (True: new parties travel in the next export file) and `can_fetch: bool = True`.
- `datev.connectors.registry`: `get_storage(client) -> StorageConnector`, `get_export(client) -> ExportConnector`, `get_master_data(client) -> MasterDataConnector`. Resolution: merge `frappe.get_hooks("datev_storage_connectors" | "datev_export_connectors" | "datev_master_data_connectors")` (dicts label → dotted class path, last one wins), look up the client's selected label, `frappe.get_attr(path)(client)`. Unknown label → `frappe.ValidationError`.
- `hooks.py` registers built-ins (classes created in Tasks 7 and 11):
  `datev_storage_connectors = {"Business OS": "datev.connectors.business_os.BusinessOSStorage", "E-mail to DUo": "datev.connectors.duo_email.DuoEmailStorage"}`;
  `datev_export_connectors = {"DATEVconnect": "datev.connectors.datevconnect.DATEVconnectExport", "EXTF download": "datev.connectors.extf.ExtfDownloadExport", "EXTF e-mail": "datev.connectors.extf.ExtfEmailExport"}`;
  `datev_master_data_connectors = {"DATEVconnect": "datev.connectors.datevconnect.DATEVconnectMasterData", "EXTF": "datev.connectors.extf.ExtfMasterData"}`.

- [ ] **Step 1: Failing tests:** `test_http_parses_json`, `test_http_transport_error` (patch `datev.http.requests.request` to raise `requests.ConnectionError`), `test_registry_resolves_hook_class` (patch `frappe.get_hooks` to add `{"Fake": "datev.tests.test_registry.FakeStorage"}` and set the client's label via `client.storage_connector = "Fake"` without saving), `test_unknown_label_raises`.
- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS (registry tests on built-in labels wait for Tasks 7/11; test only the fake).
- [ ] **Step 5: Commit** `feat: add http wrapper and connector registry`

---

### Task 6: Business OS client

**Files:** Create `datev/clients/__init__.py`, `datev/clients/business_os.py`, `datev/tests/test_business_os_client.py`

**Interfaces:**
- `class BusinessOSError(ConnectorError)` with extra attribute `code: str | None`, `status: int | None`.
- `class BusinessOS(api_key: str, connection_id: str | None = None, base_url: str = "https://api.business-os.de")`
  - `connections(module: str = "datev-duo") -> list[dict]` → `GET /v2/{module}/connections` → `data["connections"]`
  - `companies() -> list[dict]` → `GET /v2/datev/companies` → `data["data"]`
  - `document_types() -> list[dict]` → `GET /v2/datev-duo/belegtypen` → `data["data"]` (each `{belegtyp, belegkreis}`)
  - `upload_document(content: bytes, filename: str, document_type: str, id: str | None = None, note: str | None = None) -> dict` → `POST /v2/datev-duo/beleg-bereitstellen` with body `{"datei": {"content": b64, "dateiname": basename}, "belegtyp": …, "id"?: …, "belegnotiz"?: note[:60]}` → returns parsed body (`id`, `dateiname`, `business-os.neuesGuthaben`).
  - Every call: header `x-api-key`, params `connectionId` (when set) and `quelle="frappe-datev"`, timeout 60.
  - Non-2xx → `BusinessOSError(message=body.error or text, code=body.code, status=…, retryable=status in (408, 425, 429) or status >= 500)`; `TransportError` → `BusinessOSError(retryable=True)`.

- [ ] **Step 1: Failing tests** (mock `datev.http.requests.request`, assert call args): `test_upload_body_and_params` (base64 content, `dateiname` basename only, `id` included only when given, `belegnotiz` cut to 60, params contain `connectionId` and `quelle`), `test_document_types_parsed`, `test_error_code_exposed` (400 `{"error":"x","code":"BELEGTYP_UNKNOWN"}` → `code == "BELEGTYP_UNKNOWN"`, not retryable), `test_rate_limit_is_retryable` (429 → retryable), `test_timeout_is_retryable`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add Business OS API client`

---

### Task 7: Storage connectors (Business OS, e-mail to DUo) and document type refresh

**Files:** Create `datev/connectors/business_os.py`, `datev/connectors/duo_email.py`, `datev/tests/test_storage_connectors.py`; Modify `datev/datev/doctype/datev_client/datev_client.py` + `.js`

**Interfaces:**
- `BusinessOSStorage(StorageConnector)`:
  - `upload(document)`: reads file bytes via `frappe.get_doc("File", {"file_url": document.file}).get_content()`. With `bos_id_mode == "Send own ID"`: uses `document.guid` (caller guarantees it is set) as `id`, returns `StorageResult(external_id=document.guid, program_code="BEDI")`. With `"Use returned ID"`: no `id` sent, returns `external_id=response["id"]`. Stores `response["business-os"]["neuesGuthaben"]` into `client.bos_credits` via `frappe.db.set_value`.
  - `document_types()` → `BusinessOS.document_types()`.
- `DuoEmailStorage(StorageConnector)`: `upload(document)` sends `frappe.sendmail(recipients=[client.duo_email], subject=document.file_name, attachments=[{"fname": file_name, "fcontent": bytes}], now=True)`; returns `StorageResult(None, None)`.
- `DATEVClient` whitelisted methods (DATEV Manager only):
  - `refresh_document_types(self) -> int`: replaces `document_type_options` with the connector's `document_types()` (`document_type=belegtyp`, `category=belegkreis`), saves, returns count.
  - `business_os_connections(self) -> list[dict]`: returns `[{id, bezeichnung, status}]` plus, from `companies()`, the entry whose `consultant_number`/`client_number` match, so the form can preselect the connection. JS: buttons "Fetch document types" and "Pick Business OS connection" (dialog with a Select).

- [ ] **Step 1: Failing tests:** `test_send_own_id` (request body `id == document.guid`, result `external_id == guid`, `program_code == "BEDI"`), `test_use_returned_id` (no `id` in body, result from response), `test_credits_stored`, `test_duo_email_has_no_link` (patch `frappe.sendmail`, result `external_id is None`), `test_refresh_document_types` (options replaced, count returned).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add Business OS and DUo e-mail storage connectors`

---

### Task 8: Intake (documents) and upload pipeline

**Files:** Create `datev/intake.py` (document + discard part), `datev/documents.py`, `datev/tests/test_documents.py`; Modify `datev/hooks.py` (scheduler)

**Interfaces:**
- `datev.intake.upsert_document(client: str, source_key: str, *, direction: str, file_name: str, content: bytes, document_date, document_number: str, amount, currency: str = "EUR", source_doctype: str | None = None, source_name: str | None = None, is_return: bool = False) -> str`
  - Existing `source_key` with status other than `Pending`/`Stuck` → returns its name unchanged (idempotent, no new upload). Otherwise creates/updates: saves the file as a private `File` attached to the DATEV Document, resolves `document_type` via `datev.document_types.resolve`, sets `guid = new_guid()` if empty, then `frappe.enqueue("datev.documents.upload", name=…, enqueue_after_commit=True, queue="short")`. Returns the document name.
  - `DocumentTypeError` is not raised to the caller: the document is saved with status `Stuck` and the message.
- `datev.intake.discard_document(source_key: str) -> None`: status `Pending`/`Stuck` → `Discarded` (and its Pending bookings deleted); `Uploaded`/`No Link` → `frappe.ValidationError(_("The document is already in DATEV. Create a cancellation invoice instead."))`.
- `datev.documents.BACKOFF_MINUTES = (5, 30, 120, 720)`; `MAX_ATTEMPTS = 5`
- `datev.documents.upload(name: str) -> None`: skip unless status `Pending`; call `registry.get_storage(client).upload(doc)`; success → `external_id`/`program_code` set, status `Uploaded` (or `No Link` when `external_id` is None), event `uploaded`; `ConnectorError(retryable=True)` and attempts < MAX_ATTEMPTS → attempts+1, `next_attempt = now + BACKOFF_MINUTES[attempts-1]`, stays Pending, event `upload_retry`; non-retryable or last attempt → `Stuck` with message, event `upload_failed`. Row is locked with `frappe.db.get_value(..., for_update=True)` before calling the connector.
- `datev.documents.retry_due() -> None`: uploads Pending documents with `next_attempt <= now` or `next_attempt` null and older than 10 minutes. Scheduler: `"cron": {"*/5 * * * *": ["datev.documents.retry_due"]}`.
- `datev.documents.retry(name: str)` whitelisted (DATEV User): `Stuck` → `Pending`, attempts 0, upload now.

- [ ] **Step 1: Failing tests** (fake storage via patched registry): `test_upsert_is_idempotent_after_upload`, `test_upsert_sets_guid_and_type`, `test_unknown_type_marks_stuck`, `test_upload_success_sets_link`, `test_email_upload_sets_no_link`, `test_backoff_then_stuck` (retryable error 5 times → attempts 1..4 with increasing `next_attempt`, then `Stuck`), `test_non_retryable_is_stuck_immediately`, `test_discard_before_upload`, `test_discard_after_upload_rejected`, `test_retry_resets_stuck`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add document intake and upload pipeline with backoff`

---

### Task 9: EXTF writer

**Files:** Create `datev/extf/__init__.py`, `format.py`, `header.py`, `booking_batch.py`, `master_data.py`, `datev/tests/test_extf.py`, `datev/tests/fixtures/extf_booking_batch_expected.csv` (hand-written reference, cp1252)

**Interfaces:**
- `datev.extf.format`:
  - `text(value: str | None, max_len: int | None = None) -> str`: `None` → `""`; control chars (incl. CR/LF/TAB) → single space; characters not encodable in cp1252 → NFKD-decomposed, combining marks stripped, anything still not encodable dropped; collapse whitespace; strip; truncate to `max_len`; double `"`; wrap in quotes.
  - `document_field(value: str) -> str`: keep only `[A-Za-z0-9$&%*+\-/]`, max 36, returns unquoted content (caller passes to `text`).
  - `amount(value) -> str`: absolute value, 2 decimals, `,` separator, no thousands separator (`Decimal("1234.5")` → `"1234,50"`).
  - `ddmm(d: date) -> str` (`"0503"` for 5 March); `yyyymmdd(d: date) -> str`.
  - `encode(lines: list[str]) -> bytes`: `"\r\n".join(lines) + "\r\n"`, encoded cp1252; characters not encodable are NFKD-decomposed and stripped of combining marks, anything left becomes `?`.
- `datev.extf.header.row(client, *, category: int, format_name: str, format_version: int, created: datetime, fiscal_year_start: date | None, date_from: date | None, date_to: date | None, description: str) -> str` — 31 fields in DATEV order: `"EXTF"`, `700`, category, `"<format_name>"`, version, created as `%Y%m%d%H%M%S` + 3-digit ms, empty, `"RE"`, `""`, `""`, consultant, client, FY start, account_length, date_from, date_to, `"<description[:30]>"`, `""`, `1` (category 21 only, else empty), `0` (21 only), `0` (Festschreibung, 21 only), `"EUR"` (21 only, else `""`), empty, `""`, empty, empty, `"03"`/`"04"` from chart, empty, empty, `""`, `""`. Expected for category 21 with the sample values: `"EXTF";700;21;"Buchungsstapel";13;20270105103000000;;"RE";"";"";29098;55003;20270101;4;20270101;20270131;"Week 1";"";1;0;0;"EUR";;"";;;"04";;;"";""`.
- `datev.extf.booking_batch.COLUMNS: tuple[str, ...]` — the 125 column names of Buchungsstapel v13, verbatim from the DATEV format description (row 2 of the sample file). Assert `len(COLUMNS) == 125`.
- `datev.extf.booking_batch.build(client, bookings: list, created: datetime, description: str) -> list[tuple[str, bytes]]` — groups bookings by `fiscal_year`, one file per group, filename `EXTF_Buchungsstapel_<client_number>_<from:%Y%m%d>_<to:%Y%m%d>.csv` where from/to = min/max `document_date` of the group. Filled columns per booking: Umsatz (`amount`), Soll/Haben-Kennzeichen (`debit_credit`), WKZ Umsatz (`currency`), Konto (party account number), Gegenkonto (contra account, remapped via `account_mappings`), BU-Schlüssel, Belegdatum (`ddmm`), Belegfeld 1 (`document_field`), Buchungstext (max 60), Beleglink (`extf_value(program_code, external_id)` when the document has an `external_id`), EU-Steuersatz left empty, Festschreibung `0`. All other columns empty.
- `datev.extf.master_data.COLUMNS` — Debitoren/Kreditoren v5 column names verbatim from the format description; `build(client, party_accounts: list, created: datetime) -> bytes` fills Konto, Name (Adressatentyp Unternehmen), Unternehmensgegenstand empty, EU-Land/EU-UStID split from `vat_id` (first two letters / rest), IBAN-Nr. 1; `parse(content: bytes) -> list[RemoteParty]` reads EXTF or DTVF files of category 16 (header row skipped, column names from row 2; kind by number range per Task 3).

- [ ] **Step 1: Failing tests:** `test_header_row` (string above), `test_columns_count` (125), `test_booking_line_matches_reference` (two bookings incl. one with Beleglink, compare bytes to the fixture), `test_text_sanitising` (`'Miete "Mai";\r\nX😀'` → `'"Miete ""Mai""; X"'` after encoding round-trip no `\r\n` inside, emoji gone), `test_document_field_1_charset` (`"RE 2027#0042/ä"` → `"RE20270042/"`), `test_amount_format`, `test_credit_note_sign` (booking `debit_credit="H"` amount 50 → Umsatz `50,00`, Kz `H`), `test_split_by_fiscal_year` (Dec 2026 + Jan 2027 → two files with headers `20260101` and `20270101`), `test_cp1252_umlauts` (`"Müller"` encodes to `b"M\xfcller"`), `test_master_data_roundtrip` (build → parse → same numbers/names).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add EXTF writer for booking batches and party master data`

---

### Task 10: DATEVconnect client

**Files:** Create `datev/clients/datevconnect.py`, `datev/tests/test_datevconnect_client.py`

**Interfaces:**
- `class DATEVconnectError(ConnectorError)` with `status: int | None`.
- `class DATEVconnect(base_url: str, username: str, password: str, verify: bool = True)` — basic auth, `Accept: application/json`, timeout 60:
  - `clients() -> list[dict]` (`GET /clients`)
  - `client_id_for(client_number: int, consultant_number: int) -> str` — matches `number`/`consultant_number` fields of `/clients` items (check names in the OpenAPI file); none → `DATEVconnectError`.
  - `fiscal_years(client_id: str) -> list[dict]` (`GET /clients/{id}/fiscal-years`)
  - `post_proposals(client_id: str, fiscal_year_id: str, direction: str, items: list[dict]) -> list[dict]` — `direction` in `("outgoing", "incoming")` → `POST …/fiscal-years/{fy}/posting-proposals-{direction}-invoices/batch`; returns the response list (one entry per item, may carry notes/errors).
  - `post_accounting_sequence(client_id: str, fiscal_year_id: str, payload: dict) -> dict`
  - `parties(client_id, fiscal_year_id, kind) -> list[dict]` (`kind` in `("debitors", "creditors")`), `next_available(client_id, fiscal_year_id, kind) -> int`, `put_party(client_id, fiscal_year_id, kind, payload: dict) -> dict` (`PUT …/{kind}` with `Content-Type: application/merge-patch+json`).
  - Non-2xx → `DATEVconnectError(retryable=status >= 500)`; `TransportError` → retryable.

- [ ] **Step 1: Failing tests:** `test_basic_auth_and_tls_flag`, `test_client_id_lookup`, `test_post_proposals_path` (outgoing path exact), `test_put_party_content_type`, `test_server_error_retryable`.
- [ ] **Step 2–4:** FAIL → implement (field names checked against `datevconnect-accounting-1.7.4.json`) → PASS.
- [ ] **Step 5: Commit** `feat: add DATEVconnect API client`

---

### Task 11: Export and master data connectors

**Files:** Create `datev/connectors/datevconnect.py`, `datev/connectors/extf.py`, `datev/tests/test_export_connectors.py`

**Interfaces:**
- `datev.connectors.datevconnect.proposal_item(booking, document, party_account, client) -> dict`: `amount` (positive for outgoing+S or incoming+H, else negative), `date` (ISO document_date), `debitor_account_number` or `creditor_account_number` (party number), `account_number` (contra, remapped), `accounting_transaction_key` (bu_key, omitted when empty), `tax_rate`, `document_field1` (`document_field`), `document_system` (`program_code`), `document_link` (`external_id.upper()`), `due_date`, `name` (party_name[:40]), `posting_description` (posting_text[:60]), `currency_code`. `document_system`/`document_link` omitted when the document has no link.
- `DATEVconnectExport.export(batch, bookings) -> ExportResult`: resolves and caches `dc_client_id`; groups by (`fiscal_year`, kind) → one `post_proposals` call per group (`Outgoing Invoice` → outgoing, `Incoming Invoice` → incoming); maps the response list index-wise to `ExportItemResult` (`ok` unless the entry carries an error per the OpenAPI schema); a failed call marks all items of that group not ok with the error message. `Payment` bookings: `ConnectorError(_("Payments are not supported yet"))`.
- `DATEVconnectMasterData`: `fetch_parties()` (debitors + creditors of the current fiscal year → `RemoteParty`), `next_number(kind)`, `create_party(party_account)` → `put_party` into every fiscal year returned by `fiscal_years` that is not locked, payload with `account_number`, `caption`/name and VAT ID fields per OpenAPI.
- `ExtfDownloadExport` has `final_status = "Exported"`. `ExtfDownloadExport.export(batch, bookings)`: builds files via `booking_batch.build`; adds a master data file (`master_data.build`) for the party accounts of these bookings with `sync_status == "New"`; saves all as private Files attached to the batch; all items ok; `ExportResult.files` = URLs.
- `ExtfEmailExport(ExtfDownloadExport)`: same, then `frappe.sendmail(recipients=[client.extf_email], subject=_("DATEV booking batch {0}").format(batch.name), attachments=…)`.
- `ExtfMasterData` (`creates_via_export = True`, `can_fetch = False`): `fetch_parties()` raises `ConnectorError(_("Upload a master data export from DATEV to match existing accounts."))`; `next_number` raises `ConnectorError`; `create_party` is a no-op (the party goes out with the next EXTF export; `datev.dispatch` marks it `Created` after export).

- [ ] **Step 1: Failing tests:** `test_proposal_item_outgoing` (exact dict), `test_credit_note_sign` (outgoing H → negative amount), `test_no_link_fields_without_external_id`, `test_groups_by_fiscal_year_and_direction` (3 bookings, 2 FYs → 2 calls, correct paths), `test_partial_error_maps_to_items`, `test_extf_download_attaches_files` (booking file + master data file for New party), `test_extf_email_sends`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Registry tests for all built-in labels added to `test_registry.py`.
- [ ] **Step 5: Commit** `feat: add DATEVconnect and EXTF export and master data connectors`

---

### Task 12: Party accounts — intake, numbering, DATEV sync, initial matching

**Files:** Create `datev/parties.py`, `datev/tests/test_parties.py`; Modify `datev/intake.py`, `datev_client.py` + `.js`, `datev/datev/doctype/datev_party_account/*.js`

**Interfaces:**
- `datev.intake.ensure_party_account(client: str, kind: str, *, party_type: str | None, party: str | None, party_name: str, vat_id: str | None = None, iban: str | None = None, preferred_number: int | None = None) -> str` — finds by (`client`, `kind`, `party_type`, `party`) or creates (`sync_status="New"`); updates name/VAT/IBAN/preferred number. Returns the name.
- `datev.parties.assign_number(party_account) -> int` — keeps an existing `account_number`; `Source system` numbering → `preferred_number` (missing → `frappe.ValidationError`); `DATEV next available` → `get_master_data(client).next_number(kind)`.
- `datev.parties.ensure_in_datev(party_account) -> bool` — `To Clarify`/`Error` → False; `Imported`/`Created` → True; `New` → `assign_number`, then: if the connector `can_fetch`, check the number against `fetch_parties()` (cached per client in `frappe.local` for the request/job) — the number exists remotely with a different normalized name → `To Clarify` with message, returns False (never overwrite an unrelated DATEV account); same name → link as `Imported`; absent → `create_party` → `Created`. If `creates_via_export`: stays `New`, returns True (goes out with the file). Errors → `Error` + message, returns False.
- `datev.parties.import_existing(client: str, remote: list[RemoteParty] | None = None) -> dict` — `remote` defaults to `get_master_data(client).fetch_parties()`; matches each local party account without `Imported`/`Created` status: by number (only if `match_debtors_by_number`/`match_creditors_by_number` and `preferred_number == remote.account_number`), else by normalized VAT ID, else IBAN, else normalized name (casefold, legal-form suffixes `gmbh, e.k., ug, ag, kg, ohg, gbr, mbh, & co.` and punctuation stripped). Exactly one candidate → set `account_number`, `Imported`; several or a remote already linked → `To Clarify` with the candidates in `message`; none → unchanged. Returns `{"imported": n, "to_clarify": n, "unmatched": n}`.
- `DATEVClient.import_parties(self)` whitelisted (DATEV Manager): runs `import_existing` via API connector. `DATEVClient.import_parties_from_file(self, file_url: str)` parses an uploaded master data file with `master_data.parse`. JS buttons "Match DATEV accounts" and "Match from file".
- `DATEV Party Account` form: buttons "Mark resolved" (sets `account_number` from the dialog, status `Imported`) for `To Clarify`.

- [ ] **Step 1: Failing tests:** `test_ensure_party_account_idempotent`, `test_source_numbering_uses_preferred`, `test_datev_numbering_uses_next_available`, `test_number_collision_to_clarify`, `test_import_by_number`, `test_import_by_vat_id_when_number_matching_off`, `test_import_ambiguous_name_to_clarify`, `test_name_normalisation` (`"Muster GmbH & Co. KG"` matches `"muster"`), `test_import_from_file`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add party accounts with numbering, DATEV sync and initial matching`

---

### Task 13: Booking intake, batching, approval, dispatch

**Files:** Create `datev/dispatch.py`, `datev/tests/test_dispatch.py`; Modify `datev/intake.py`, `datev/hooks.py`, batch/booking doctype `.js` (buttons)

**Interfaces:**
- `datev.intake.add_booking(document: str, line_key: str, *, kind: str, party_account: str, contra_account: int, amount, debit_credit: str, tax_rate: float = 0, bu_key: str = "", posting_text: str = "", due_date=None) -> str` — idempotent per (`document`, `line_key`): existing `Pending`/`Stuck` booking is updated, `Batched`/`Transferred`/`Exported` returned unchanged. Copies `client`, `currency`, `document_date`, `document_field_1` (= document_number) from the document; `bu_key` falls back to the client's `tax_keys` row for the given code only when the adapter passes `tax_code=` (keyword argument `tax_code: str | None = None`, looked up in `tax_keys`, row missing → empty key).
- `datev.dispatch.preflight(booking) -> str | None` — message when: party account not ready (`datev.parties.ensure_in_datev` False); `require_document_link` and document status not `Uploaded` (or `external_id` empty); document status `Stuck`/`Pending`; mandatory fields missing (`contra_account`, `document_field_1`, `document_date`). `None` = ready.
- `datev.dispatch.is_due(client, now: datetime) -> bool` — Daily: last dispatch before today; Weekly: today is `dispatch_weekday` and last dispatch before today; Monthly: day ≥ `dispatch_day_of_month` and no dispatch this month. Never dispatched → due.
- `datev.dispatch.collect(client: str) -> str | None` — under a per-client row lock on `DATEV Client` (`for_update`): preflight all `Pending`/`Stuck` not-held bookings; failures → `Stuck` + message; ready ones → new `DATEV Batch` (`Awaiting Approval` if `approval_required`, else `Approved`), bookings `Batched`. No ready bookings → `None`. Sets `last_dispatch_on`.
- `datev.dispatch.approve(batch: str)` whitelisted (DATEV User or the client's responsible role/user): `Awaiting Approval` → `Approved`, `approved_by/on`, then `send`.
- `datev.dispatch.hold(booking: str, held: bool = True)` whitelisted: allowed for `Pending` bookings and `Batched` ones in an `Awaiting Approval` batch (removed from the batch, back to `Pending`, `held=1`). Held bookings are skipped by `collect` until un-held.
- `datev.dispatch.send(batch: str) -> None` — locks the batch row; only `Approved` proceeds (else return); sets `Sending` and commits; calls the export connector; per item: ok → booking gets the connector's `final_status` (`Transferred` for API connectors, `Exported` for EXTF), not ok → `Failed` + message, `batch` cleared so it re-enters the next `collect` as `Pending`. Party accounts exported via EXTF → `Created`. Batch → `Sent` / `Partially Sent` / `Failed`, `sent_on`, event log with response. Connector exception → all bookings back to `Pending`, batch `Failed`.
- `datev.dispatch.run() -> None` — scheduler `"cron": {"0 * * * *": ["datev.dispatch.run"]}`: for enabled clients `is_due` → `collect`; then `send` every `Approved` batch.
- `datev.dispatch.send_now(client: str)` whitelisted (DATEV User): `collect` regardless of schedule, then send if approved.

- [ ] **Step 1: Failing tests** (fake export connector via patched registry): `test_add_booking_idempotent`, `test_tax_code_maps_bu_key`, `test_preflight_requires_link_when_strict`, `test_preflight_allows_no_link_when_lenient`, `test_collect_creates_batch_awaiting_approval`, `test_collect_without_approval_sends_on_run`, `test_is_due_weekly_and_monthly`, `test_hold_moves_booking_out_of_batch`, `test_send_twice_sends_once` (call `send` twice → connector called once), `test_partial_failure_resends_only_failed` (2 bookings, 1 fails → next collect/send only sends the failed one), `test_exported_bookings_never_recollected`, `test_connector_exception_resets_bookings`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add booking intake, batching, approval and dispatch`

---

### Task 14: Intake HTTP API

**Files:** Create `datev/api.py`, `datev/tests/test_api.py`, `docs/intake-api.md`

**Interfaces:**
- Whitelisted, `methods=["POST"]`, require role `DATEV Manager` or `DATEV User`:
  - `submit_document(client, source_key, direction, file_name, content_base64, document_date, document_number, amount, currency="EUR", is_return=0, source_doctype=None, source_name=None) -> {"name": str, "status": str}`
  - `submit_party(client, kind, party_name, party_type=None, party=None, vat_id=None, iban=None, preferred_number=None) -> {"name": str}`
  - `submit_booking(document, line_key, kind, party_account, contra_account, amount, debit_credit, tax_rate=0, bu_key="", tax_code=None, posting_text="", due_date=None) -> {"name": str, "status": str}`
  - `status(source_key) -> {"document": {...status, external_id, program_code}, "bookings": [{line_key, status, batch, message}]}`
- `docs/intake-api.md` documents the four calls with a curl example (API key auth, `/api/v2/method/datev.api.submit_document`).

- [ ] **Step 1: Failing tests:** `test_submit_document_roundtrip` (base64 → document with file), `test_permission_required` (Guest → `frappe.PermissionError`), `test_status_reports_bookings`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add intake HTTP API`

---

### Task 15: Overview, notifications, credits warning

**Files:** Create `datev/notify.py`, `datev/datev/report/datev_status/{datev_status.json,datev_status.py,__init__.py}`, `datev/datev/workspace/datev/datev.json`, `datev/tests/test_notify.py`; Modify `datev/hooks.py`

**Interfaces:**
- Script report `DATEV Status` (roles DATEV Manager/User): one row per enabled client with columns `client`, `transferred_30d`, `awaiting_approval`, `stuck_bookings`, `stuck_documents`, `documents_without_link`, `held`, `overdue` (Check), `bos_credits`; rows with problems first.
- `datev.dispatch.is_overdue(client, now) -> bool` (add to dispatch.py): a dispatch date has passed since `last_dispatch_on` (per `is_due` rules evaluated for the previous due date) and there are `Pending`/`Stuck` bookings or an `Awaiting Approval` batch older than 24 h.
- `datev.notify.recipients(client) -> list[str]` — `responsible_user` if set, else enabled users with `responsible_role`, else users with `DATEV Manager`.
- `datev.notify.check() -> None` — scheduler hourly (after `dispatch.run`): creates one `Notification Log` (type `Alert`, document link to batch/report) per recipient for: batch awaiting approval (once per batch, `notified_on`), overdue client (at most once per day per client), stuck documents/bookings appearing since the last check (one summary per client per day), `bos_credits` below `bos_credit_warning` (once per day). Uses a cache key per (client, reason, date) to avoid repeats.
- Workspace `DATEV` with shortcuts: DATEV Status report, DATEV Client, DATEV Batch (filter Awaiting Approval), DATEV Booking (filter Stuck), DATEV Document (filter Stuck), DATEV Party Account (filter To Clarify).

- [ ] **Step 1: Failing tests:** `test_recipients_fallback_order`, `test_awaiting_approval_notified_once`, `test_overdue_detection`, `test_credit_warning`, `test_report_flags_problems_first`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: add status report, workspace and notifications`

---

### Task 16: German translations, docs, README

**Files:** Create `datev/locale/main.pot`, `datev/locale/de.po`, `datev/tests/test_translations.py`, `README.md` (final), `docs/concepts.md`, `docs/connectors.md`, `docs/extf.md`, `docs/development.md`

**Interfaces:**
- `test_translations.py`: `test_every_message_translated` — loads `datev/locale/main.pot` and `de.po` with `babel.messages.pofile.read_po`; every msgid in the pot has a non-empty msgstr in de.po; `test_pot_is_current` — regenerates the pot into a temp dir (`frappe.gettext.translate.generate_pot` for app `datev`, check the exact function in the installed Frappe) and asserts the msgid sets are equal.
- German terms: Client → Mandant, Document → Beleg, Booking → Buchung, Batch → Buchungsstapel, Party Account → Personenkonto, Debtor/Creditor → Debitor/Kreditor, Document type → Belegtyp, Document link → Beleglink, BU key → BU-Schlüssel, Consultant number → Beraternummer, Client number → Mandantennummer, Account length → Sachkontenlänge, Chart of accounts → Kontenrahmen, Stuck → Hängt, Awaiting Approval → Wartet auf Freigabe, Held → Zurückgehalten, To Clarify → Zu klären, Transferred → Übertragen, Exported → Exportiert.
- README in docarchive's shape: one-line description, Status, "No compliance promise" (DATEV, GoBD, tax advice), Features with links to docs, Install (`bench get-app https://github.com/EffizienzAgent/datev --branch version-16`), Professional setup & support (EffizienzAgent), License MIT. No client names.

- [ ] **Step 1: Write failing `test_translations.py`** → FAIL (no pot).
- [ ] **Step 2: Generate** `bench --site datev.test generate-pot-file --app datev` (inside the container), create `de.po` (`bench update-po-files --app datev --locale de` if available), translate every entry.
- [ ] **Step 3: Run** `dev/test.sh` (full suite) → all PASS; `ruff check . && ruff format --check .` clean.
- [ ] **Step 4: Write README and docs.**
- [ ] **Step 5: Commit** `docs: add German translations, README and docs`
