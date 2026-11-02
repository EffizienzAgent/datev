frappe.query_reports["DATEV Status"] = {
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		return data && !data.enabled ? `<span class="text-muted">${value}</span>` : value;
	},
};
