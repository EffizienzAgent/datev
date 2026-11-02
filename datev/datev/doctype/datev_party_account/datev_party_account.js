frappe.ui.form.on("DATEV Party Account", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.sync_status !== "To Clarify") return;

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
