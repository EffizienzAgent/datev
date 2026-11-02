frappe.ui.form.on("DATEV Batch", {
	refresh(frm) {
		if (frm.doc.status !== "Awaiting Approval") return;

		frm.add_custom_button(__("Approve and send"), () => {
			frappe.confirm(__("Approve this batch and send it to DATEV?"), () => {
				frappe
					.call("datev.dispatch.approve", { batch: frm.doc.name })
					.then(() => frm.reload_doc());
			});
		}).addClass("btn-primary");
	},
});
