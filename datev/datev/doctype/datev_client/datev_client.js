frappe.ui.form.on("DATEV Client", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Fetch document types"), () => {
			frm.call("refresh_document_types").then((r) => {
				frappe.show_alert({
					message: __("{0} document types loaded.", [r.message]),
					indicator: "green",
				});
				frm.reload_doc();
			});
		});

		if (frm.doc.storage_connector === "Business OS") {
			frm.add_custom_button(__("Pick Business OS connection"), () => frm.trigger("pick_connection"));
		}
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
						default: (rows.find((row) => row.status === null) || rows[0] || {}).id,
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
