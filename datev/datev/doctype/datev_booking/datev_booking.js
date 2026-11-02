frappe.ui.form.on("DATEV Booking", {
	async refresh(frm) {
		const resettable = ["Transferred", "Exported", "Failed"].includes(frm.doc.status);
		if (resettable && frappe.user.has_role("DATEV Manager")) {
			frm.add_custom_button(__("Reset to Pending"), () => frm.events.reset(frm));
		}
		if (frm.doc.held) {
			frm.add_custom_button(__("Release"), () => frm.events.hold(frm, false));
			return;
		}
		let holdable = frm.doc.status === "Pending";
		if (frm.doc.status === "Batched" && frm.doc.batch) {
			const { message } = await frappe.db.get_value("DATEV Batch", frm.doc.batch, "status");
			holdable = message?.status === "Awaiting Approval";
		}
		if (holdable) {
			frm.add_custom_button(__("Hold"), () => frm.events.hold(frm, true));
		}
	},

	reset(frm) {
		frappe.prompt(
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			({ reason }) => {
				frappe
					.call("datev.dispatch.reset_booking", { booking: frm.doc.name, reason })
					.then(() => frm.reload_doc());
			},
			__("Reset to Pending"),
			__("Reset")
		);
	},

	hold(frm, held) {
		frappe
			.call("datev.dispatch.hold", { booking: frm.doc.name, held: held ? 1 : 0 })
			.then(() => frm.reload_doc());
	},
});
