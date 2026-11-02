import frappe
from frappe import _


def _resolve(hook: str, label: str | None, client):
	paths = {}
	hooks = frappe.get_hooks(hook)
	for entry in [hooks] if isinstance(hooks, dict) else hooks:
		for name, value in entry.items():
			paths[name] = value[-1] if isinstance(value, list | tuple) else value

	if label not in paths:
		frappe.throw(_("Unknown connector: {0}").format(label), frappe.ValidationError)
	return frappe.get_attr(paths[label])(client)


def get_storage(client):
	return _resolve("datev_storage_connectors", client.storage_connector, client)


def get_export(client, label: str | None = None):
	return _resolve("datev_export_connectors", label or client.export_connector, client)


def get_master_data(client):
	return _resolve("datev_master_data_connectors", client.master_data_connector, client)
