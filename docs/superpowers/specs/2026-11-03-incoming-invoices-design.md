# Incoming Invoices – Design

Date: 2026-11-03 · Status: draft for approval · Extends `2026-11-02-datev-app-design.md` and `2026-11-03-payments-design.md` (core `datev` ≥ 0.2.0, adapter `datev_erpnext` ≥ 0.2.0).

## Intent

Supplier invoices posted in ERPNext must reach DATEV like sales invoices do: one booking per voucher with its document image and Beleglink, creditor against expense accounts, input tax handled by automatic accounts or BU keys. Together with sales invoices and payments, DATEV then receives the complete picture.

How supplier invoices get into ERPNext is **not** part of this work: e-mail, API, e-invoice import (for example with `eu_einvoice`), manual entry from a PDF, later OCR. The DATEV apps start from the posted (submitted) Purchase Invoice, which means it was checked and approved in ERPNext.

## Goals

- Every submitted Purchase Invoice (not opening) reaches DATEV once, with the supplier's original file as document image and Beleglink.
- Creditor (party account with the supplier number) against the expense/asset accounts ERPNext chose; one gross line per account, tax code and rate.
- Input tax and reverse charge through the client's tax-key table (empty BU key = automatic account); unknown tax codes are rejected.
- Belegfeld 1 = the supplier's invoice number, identical for the invoice and for payments that clear it.
- Same dispatch, approval, duplicate protection, catch-up, report, notices and cancel guard as sales invoices.

## Non-goals

- Invoice intake, e-invoice parsing, OCR, supplier/item matching.
- Purchase orders, receipts and stock valuation as separate bookings.
- Landed cost distribution, deferred expense, write-offs (rejected with a notice for now).

## Anti-goals

- A purchase invoice in DATEV without its original document.
- Input tax booked although the line is tax-free, or missing although it is taxable (wrong or guessed tax key).
- Belegfeld 1 differing between invoice and payment, so DATEV cannot clear the open item.

## Decisions

- **Source:** Purchase Invoice, submitted, `is_opening != "Yes"`, posting date ≥ `sync_from_date`; adapter setting `sync_purchase_invoices` (Check, default off).
- **Order of work:** built after the payments design; enabling purchase invoices releases the supplier-payment lines the payments design holds.
- **Document image (in this order):** the adapter hook `datev_erpnext_document_image` (sites can supply their own file); the original file of an e-invoice import linked to the invoice (if `eu_einvoice` is installed: the file of the "E Invoice Import" that created the invoice — XML or ZUGFeRD PDF); else the newest attached PDF or XML file on the invoice. No file → the invoice is not transferred, it gets a notice ("no supplier document attached"). Never render a print format for incoming invoices.
- **Document type:** direction Incoming (core default "Rechnungseingang"; client rules may override, e.g. for debit notes).
- **Lines:** same grouping as sales invoices (account, tax code, rate; gross; rounding tolerance; zero lines dropped; unsupported charges rejected), using the item's `expense_account` (asset account for fixed-asset items as ERPNext books it). Party side: creditor credit (H) for an invoice, debit (S) for a return/debit note (`is_return`); a line against the invoice direction gets the opposite side.
- **Tax:** tax code = item tax template, else the invoice's purchase taxes template; expected rate from the template restricted to accounts present on the invoice (same rule as sales). Reverse charge (§13b) and intra-EU acquisitions are expressed by their template and its tax-key row; the invoice-level tax rows with `add_deduct_tax = "Deduct"` or "Actual" charges on non-tax accounts are rejected for now. "Valuation" or "Valuation and Total" tax categories are rejected.
- **Belegfeld 1:** the supplier invoice number (`bill_no`), sanitized by the core like every Belegfeld 1. Missing `bill_no` → rejected with a notice. Payments that reference a Purchase Invoice use the same value (completes the open point in the payments design: those lines are no longer held once purchase invoices are synced).
- **Dates:** Belegdatum = posting date (period consistency with ERPNext); `bill_date` goes into the posting text only.
- **Reverse charge / intra-EU acquisition** (Claude's call): the common ERPNext setup is a template with an input-tax row ("Add") and an output-tax row ("Deduct") at the same rate that cancel out. Such a balanced pair is recognized as reverse charge: the line goes out with its **net** amount, the tax code's tax-key row must carry a non-empty BU key (DATEV computes both taxes from it), otherwise the invoice is rejected. Other "Deduct" rows stay rejected.
- **Goods receipts and assets** (Claude's call): lines on "Stock Received But Not Billed", "Asset Received But Not Billed" or stock accounts are rejected with a notice ("goods receipts are not supported yet"), because receipts are not sent to DATEV and the clearing account would stay open there. Direct expense and asset accounts are fine.
- **Debit notes** (`is_return`, Claude's call): document image is an attached file if present, else the invoice's print-format PDF (the debit note is our own document). Belegfeld 1 = the debit note's `bill_no`, falling back to the original invoice's `bill_no` (`return_against`), so the open item clears.
- **Paid on the invoice** (`is_paid`, cash/card purchases, Claude's call): rejected with a notice for now (the payment part lives inside the invoice and is not covered by the payments design). Advances allocated on the invoice are covered by the payments design (re-allocation of the advance's payment entry).
- **Posting text:** supplier name (template like sales: `{supplier_name}`, `{bill_no}`, `{name}`).
- **Supplier number:** adapter setting `supplier_number_field` (field on Supplier holding the creditor number); creditor numbering per client as configured in the core.
- **Cancel:** like sales invoices — hard cancel blocked once the document is uploaded; corrections via a debit note (return) in ERPNext.

## Core changes (`datev`)

Essentially none: incoming invoice bookings, creditor party accounts, DATEVconnect incoming posting proposals and EXTF already exist. Check and fix only what the adapter work reveals (e.g. document type rules for returns of incoming invoices).

## Adapter changes (`datev_erpnext`)

- Settings: `sync_purchase_invoices`, `supplier_number_field`, `purchase_posting_text_template`.
- `purchase_invoice_lines(invoice)` alongside the sales version, sharing the grouping/tax/rounding code.
- Hooks on Purchase Invoice: on_submit (queue), before_cancel (guard); catch-up, report "not in DATEV" and status indicator extended to Purchase Invoices.
- Supplier party accounts (creditor) via the core intake, locked like customers.
- Document image resolution as above.

## Constraints

Same conventions as the existing apps (licenses, English code, German translations, no client or customer names, tests, release-please). Works with and without `eu_einvoice`.

## Left to the builder

Field names, how the e-invoice import is found from the Purchase Invoice (check the installed `eu_einvoice` version), report layout, test data.
