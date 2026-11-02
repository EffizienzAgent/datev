# Connectors

Each client selects one connector per area. Credentials are password fields and never appear in logs or the event log.

## Document storage

Uploads a document and returns program code and external ID, which form the Beleglink.

### Business OS

Uploads to DATEV Unternehmen online (DUo) through the [Business OS](https://docs.business-os.de) API (`https://api.business-os.de`, header `x-api-key`).

| Setting | Meaning |
| --- | --- |
| API Key | Business OS key |
| Connection ID | DATEV connection; "Pick Business OS connection" lists the connections and suggests the one matching consultant and client number |
| ID Mode | `Send own ID` (the document GUID is sent, so the Beleglink is known before upload and a retry reuses it) or `Use returned ID` |
| Credits, Credit Warning Threshold | Remaining credits are stored from each response; a notification warns below the threshold |

Program code is `BEDI`. Every call carries `connectionId` and `quelle=frappe-datev`. Each upload costs one credit.

### E-mail to DUo

Sends the file to the client's DUo address. There is no external ID, so the document is "No Link". If the client requires a Beleglink, its bookings stay stuck.

### Document types

Resolved per client when a document is created: the first matching rule (direction, source DocType, cancellation invoice yes/no/any) wins, otherwise the default per direction: `Rechnungsausgang`, `Rechnungseingang`, `Sonstige`. If the client lists document type options, the result must be one of them, otherwise the document is rejected. "Fetch document types" loads the options from Business OS.

## Booking export

| Connector | What it does | Final booking status |
| --- | --- | --- |
| DATEVconnect | Posting proposals for outgoing and incoming invoices | Transferred |
| EXTF download | EXTF files attached to the batch | Exported |
| EXTF e-mail | As download, plus e-mail to the client's EXTF address | Exported |

### DATEVconnect

Settings: base URL (for example `https://host:58452/datev/api/accounting/v1`), user name, password, "Verify TLS Certificate" (switch off for self-signed certificates) and the DATEVconnect client ID, which is looked up by client number and saved on first use. One call per direction and fiscal year; the response is evaluated per proposal. The Beleglink goes as `document_system` (program code) and `document_link` (ID in upper case). Payments are not supported yet.

### EXTF

See [EXTF](extf.md). The EXTF files of an e-mail batch are also stored on the batch.

## Master data

| Connector | Fetch existing accounts | Next number | Create |
| --- | --- | --- | --- |
| DATEVconnect | yes | yes | through the API |
| EXTF | no, upload a master data export to match | no | as Debitoren/Kreditoren file in the next EXTF batch |

The EXTF master data connector requires an EXTF export connector.

## Adding a connector

Connectors are classes registered through hooks, so other apps can contribute them. Subclass the base classes in `datev.connectors.base`:

- `StorageConnector.upload(document) -> StorageResult` (and optionally `document_types()`)
- `ExportConnector.export(batch, bookings) -> ExportResult`; set `final_status` (`Transferred` or `Exported`). Report per booking through `ExportItemResult`; list bookings whose delivery is unknown in `ExportResult.uncertain`. Raise `ConnectorError(message, retryable=...)` for failures.
- `MasterDataConnector` with `fetch_parties`, `next_number` and `create_party`

Register in your app's `hooks.py`; the key is the label shown in the client's connector fields:

```python
datev_storage_connectors = {"My DMS": "my_app.connectors.MyStorage"}
datev_export_connectors = {"My Export": "my_app.connectors.MyExport"}
datev_master_data_connectors = {"My Master Data": "my_app.connectors.MyMasterData"}
```

Connectors receive the DATEV Client document as `self.client` and make HTTP calls through `datev.http` so tests can mock them. Add the label to the Select options of the client's connector field with a Property Setter.
