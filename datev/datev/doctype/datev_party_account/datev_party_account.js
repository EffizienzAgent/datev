frappe.ui.form.on("DATEV Party Account", {
	refresh(frm) {
		if (frm.is_new()) return;

		const retryable = ["Error", "To Clarify"].includes(frm.doc.sync_status);
		if (retryable && frappe.user.has_role("DATEV Manager")) {
			frm.add_custom_button(__("Retry"), () => {
				frappe
					.call("datev.parties.retry_party", { party_account: frm.doc.name })
					.then(() => frm.reload_doc());
			});
		}

		if (frm.doc.sync_status !== "To Clarify") return;

		frm.add_custom_button(__("Mark resolved"), () => {
			frappe.prompt(
				{
					fieldname: "account_number",
					fieldtype: "Int",
					label: __("DATEV account number"),
					reqd: 1,
					default: frm.doc.account_number,
				},
				({ account_number }) => {
					frm.set_value({ account_number, sync_status: "Imported", message: "" });
					frm.save();
				},
				__("Mark resolved"),
				__("Save")
			);
		});
	},
});
