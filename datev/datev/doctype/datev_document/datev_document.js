frappe.ui.form.on("DATEV Document", {
	refresh(frm) {
		if (frm.doc.status !== "Stuck") return;

		if (frappe.user.has_role(["DATEV User", "DATEV Manager"])) {
			frm.add_custom_button(__("Retry upload"), () => {
				frappe
					.call("datev.documents.retry", { name: frm.doc.name })
					.then(() => frm.reload_doc());
			});
		}

		if (frappe.user.has_role("DATEV Manager")) {
			frm.add_custom_button(__("Mark as uploaded"), () => {
				frappe.prompt(
					[
						{
							fieldname: "external_id",
							fieldtype: "Data",
							label: __("Document ID in DUo"),
							reqd: 1,
						},
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
					],
					({ external_id, reason }) => {
						frappe
							.call("datev.documents.mark_uploaded", { name: frm.doc.name, external_id, reason })
							.then(() => frm.reload_doc());
					},
					__("Mark as uploaded"),
					__("Save")
				);
			});
		}
	},
});
