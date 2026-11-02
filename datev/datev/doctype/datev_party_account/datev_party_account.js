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

		if (!frappe.user.has_role("DATEV Manager")) return;

		frm.add_custom_button(__("Mark resolved"), () => {
			frappe.prompt(
				[
					{
						fieldname: "account_number",
						fieldtype: "Int",
						label: __("DATEV account number"),
						reqd: 1,
						default: frm.doc.account_number || frm.doc.preferred_number,
					},
					{
						fieldname: "status",
						fieldtype: "Select",
						label: __("Resolution"),
						reqd: 1,
						options: [
							{ value: "Imported", label: __("Account already exists in DATEV") },
							{ value: "Created", label: __("New account confirmed in DATEV") },
						],
						default: "Imported",
					},
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
				],
				({ account_number, status, reason }) => {
					frappe
						.call("datev.parties.mark_resolved", {
							party_account: frm.doc.name,
							account_number,
							status,
							reason,
						})
						.then(() => frm.reload_doc());
				},
				__("Mark resolved"),
				__("Save")
			);
		});
	},
});
