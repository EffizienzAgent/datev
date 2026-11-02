frappe.ui.form.on("DATEV Batch", {
	refresh(frm) {
		if (frm.doc.status === "Awaiting Approval") {
			frm.add_custom_button(__("Approve and send"), () => {
				frappe.confirm(__("Approve this batch and send it to DATEV?"), () => {
					frappe
						.call("datev.dispatch.approve", { batch: frm.doc.name })
						.then(() => frm.reload_doc());
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.status === "Sending" && frappe.user.has_role("DATEV Manager")) {
			frm.add_custom_button(
				__("Arrived in DATEV"),
				() => frm.events.resolve(frm, true),
				__("Resolve")
			);
			frm.add_custom_button(
				__("Not in DATEV"),
				() => frm.events.resolve(frm, false),
				__("Resolve")
			);
		}
	},

	resolve(frm, delivered) {
		const question = delivered
			? __("Mark all bookings of this batch as delivered to DATEV?")
			: __("Mark this batch as not delivered? Its bookings will be sent again.");
		frappe.confirm(question, () => {
			frappe
				.call("datev.dispatch.resolve_batch", { batch: frm.doc.name, delivered: delivered ? 1 : 0 })
				.then(() => frm.reload_doc());
		});
	},
});
