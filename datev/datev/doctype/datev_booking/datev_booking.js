frappe.ui.form.on("DATEV Booking", {
	refresh(frm) {
		const open = ["Pending", "Stuck", "Failed"].includes(frm.doc.status);
		if (frm.doc.held && open) {
			frm.add_custom_button(__("Release"), () => frm.events.hold(frm, false));
		} else if (open || frm.doc.status === "Batched") {
			frm.add_custom_button(__("Hold"), () => frm.events.hold(frm, true));
		}
	},

	hold(frm, held) {
		frappe
			.call("datev.dispatch.hold", { booking: frm.doc.name, held: held ? 1 : 0 })
			.then(() => frm.reload_doc());
	},
});
