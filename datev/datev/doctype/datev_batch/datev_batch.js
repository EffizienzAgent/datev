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

		const extf = (frm.doc.export_connector || "").startsWith("EXTF");
		const exported = ["Sent", "Partially Sent"].includes(frm.doc.status);
		const dispatcher = frappe.user.has_role(["DATEV User", "DATEV Manager"]);
		if (extf && exported && !frm.doc.confirmed_on && dispatcher) {
			frm.add_custom_button(__("Confirm import by tax office"), () => {
				frappe.confirm(__("Has the tax office imported these files into DATEV?"), () => {
					frappe
						.call("datev.dispatch.confirm_import", { batch: frm.doc.name })
						.then(() => frm.reload_doc());
				});
			}).addClass("btn-primary");
		}
		if (frm.doc.export_connector === "EXTF e-mail" && exported && dispatcher) {
			frm.add_custom_button(__("Send e-mail again"), () => {
				frappe
					.call("datev.dispatch.resend_extf_email", { batch: frm.doc.name })
					.then(() => frappe.show_alert({ message: __("E-mail sent."), indicator: "green" }));
			});
		}

		if (frm.doc.status === "Sending" && frappe.user.has_role("DATEV Manager")) {
			if (!extf) {
				frm.add_custom_button(
					__("Arrived in DATEV"),
					() => frm.events.resolve(frm, true),
					__("Resolve")
				);
			}
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
