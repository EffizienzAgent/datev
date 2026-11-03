# Intake HTTP API

Source systems push documents, parties and bookings to DATEV through four whitelisted methods. Authenticate with an API key and secret of a user holding the role `DATEV Manager` or `DATEV User`:

```
Authorization: token <api_key>:<api_secret>
```

All submit calls are idempotent: repeating a call with the same `source_key` (documents), `client` + `kind` + party (parties) or `document` + `line_key` (bookings) updates the open record. Records that were already uploaded or transferred are returned unchanged.

## `datev.api.submit_document` (POST)

| Parameter | Notes |
| --- | --- |
| `client` | Name of the DATEV Client |
| `source_key` | Unique key of the document in the source system |
| `direction` | `Outgoing`, `Incoming` or `Other` |
| `file_name`, `content_base64` | File and its base64 content (strictly validated). Leave both empty for a payment (direction `Other` only): the document gets status `Not Required` and is never uploaded |
| `document_date`, `document_number`, `amount` | Header data |
| `currency` | Default `EUR` |
| `is_return` | `1` for cancellation invoices |
| `source_doctype`, `source_name` | Optional reference to the source record. `source_doctype` must be an existing DocType name; sources outside Frappe leave both empty |

Returns `{"name": "...", "status": "Pending"}`.

```
curl -X POST https://erp.example.com/api/v2/method/datev.api.submit_document \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{
    "client": "Example Client 29098-55003",
    "source_key": "SINV-0001",
    "direction": "Outgoing",
    "file_name": "SINV-0001.pdf",
    "content_base64": "JVBERi0xLjQK...",
    "document_date": "2027-01-05",
    "document_number": "RE-1",
    "amount": "119.00"
  }'
```

## `datev.api.submit_party` (POST)

Parameters: `client`, `kind` (`Debtor` or `Creditor`), `party_name`, and optionally `party_type`, `party`, `vat_id`, `iban`, `preferred_number`.

Returns `{"name": "..."}`.

## `datev.api.submit_booking` (POST)

Parameters: `document` (name returned by `submit_document`), `line_key`, `kind` (`Outgoing Invoice`, `Incoming Invoice` or `Payment`), either `party_account` (name returned by `submit_party`) or `account` (general ledger account number, payments only), `contra_account`, `amount`, `debit_credit` (`S` or `H`), and optionally `tax_rate`, `bu_key`, `tax_code`, `posting_text`, `due_date`, `depends_on_document` (name of a document whose bookings must reach DATEV first) and `general_reversal` (`1` for a line that reverses an earlier one). A `tax_code` must have a row in the client's tax keys, otherwise the call fails with 417.

Lines can be added to a document whose other lines are already in DATEV, for example reversal lines with a new `line_key`.

Returns `{"name": "...", "status": "Pending"}`.

```
curl -X POST https://erp.example.com/api/v2/method/datev.api.submit_booking \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"document": "<name>", "line_key": "1", "kind": "Outgoing Invoice",
       "party_account": "<name>", "contra_account": 8400, "amount": "119.00",
       "debit_credit": "S", "tax_rate": 19}'
```

A bank fee without party:

```
curl -X POST https://erp.example.com/api/v2/method/datev.api.submit_booking \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"document": "<name>", "line_key": "fee", "kind": "Payment",
       "account": 6855, "contra_account": 1800, "amount": "2.50", "debit_credit": "S"}'
```

## `datev.api.status` (GET)

```
curl "https://erp.example.com/api/v2/method/datev.api.status?source_key=SINV-0001" \
  -H "Authorization: token <api_key>:<api_secret>"
```

Returns the document state and its bookings:

```json
{
  "document": {"status": "Uploaded", "external_id": "...", "program_code": "BEDI"},
  "bookings": [{"line_key": "1", "status": "Pending", "batch": null, "message": null}]
}
```

An unknown `source_key` answers 404.

## Errors

| Case | Response |
| --- | --- |
| Missing role | 403 |
| Invalid base64 or number | 417 with a message |
| Unknown `source_key` on `status` | 404 |
