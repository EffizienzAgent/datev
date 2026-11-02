frappe.ui.form.on("DATEV Client", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.storage_connector === "Business OS") {
			frm.add_custom_button(__("Fetch document types"), () => {
				frm.call("refresh_document_types").then((r) => {
					frappe.show_alert({
						message: __("{0} document types loaded.", [r.message]),
						indicator: "green",
					});
					frm.reload_doc();
				});
			});
			frm.add_custom_button(__("Pick Business OS connection"), () => frm.trigger("pick_connection"));
		}

		if (frm.doc.master_data_connector === "DATEVconnect") {
			frm.add_custom_button(__("Match DATEV accounts"), () => {
				frm.call("import_parties").then((r) => frm.events.show_match_result(r.message));
			});
		}
		frm.add_custom_button(__("Match from file"), () => frm.trigger("match_from_file"));
	},

	match_from_file(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Match from file"),
			fields: [
				{
					fieldname: "file_url",
					fieldtype: "Attach",
					label: __("DATEV debtor/creditor export"),
					reqd: 1,
				},
			],
			primary_action_label: __("Match"),
			primary_action({ file_url }) {
				frm.call("import_parties_from_file", { file_url }).then((r) => {
					dialog.hide();
					frm.events.show_match_result(r.message);
				});
			},
		});
		dialog.show();
	},

	show_match_result(result) {
		frappe.msgprint(
			__("{0} imported, {1} to clarify, {2} unmatched.", [
				result.imported,
				result.to_clarify,
				result.unmatched,
			]),
			__("Match DATEV accounts")
		);
	},

	pick_connection(frm) {
		frm.call("business_os_connections").then((r) => {
			const rows = r.message || [];
			const dialog = new frappe.ui.Dialog({
				title: __("Pick Business OS connection"),
				fields: [
					{
						fieldname: "connection",
						fieldtype: "Select",
						label: __("Connection"),
						reqd: 1,
						options: rows.map((row) => ({
							value: row.id,
							label: [row.bezeichnung, row.id].filter(Boolean).join(" - "),
						})),
						default: (rows.find((row) => row.suggested) || rows[0] || {}).id,
					},
				],
				primary_action_label: __("Select"),
				primary_action({ connection }) {
					frm.set_value("bos_connection_id", connection);
					dialog.hide();
				},
			});
			dialog.show();
		});
	},
});
