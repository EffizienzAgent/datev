import frappe


def make_client(**overrides):
	values = {
		"doctype": "DATEV Client",
		"title": f"Test Client {frappe.generate_hash(length=8)}",
		"consultant_number": 29098,
		"client_number": 55003,
		"chart": "SKR04",
		"storage_connector": "Business OS",
		"bos_api_key": "dummy-key",
		"bos_connection_id": "dummy-connection",
		"export_connector": "EXTF download",
		"master_data_connector": "EXTF",
		"creditor_numbering": "Source system",
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()
