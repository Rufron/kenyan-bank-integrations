frappe.ui.form.on("User", {
    refresh(frm) {
        const is_approver = (frm.doc.roles || []).some(r => r.role === "Bank Transfer Approver");
        if (frm.is_new() || !is_approver || !frappe.user.has_role("System Manager")) return;

        frm.add_custom_button(__("Reset Approval Authenticator"), () => {
            frappe.confirm(
                __("Unlink {0}'s authenticator app? They will scan a new QR code on their next Approve & Send.", [frm.doc.full_name || frm.doc.name]),
                () => frappe.call({
                    method: "banking_integration.banking_integration.doctype.bank_transfer.bank_transfer.reset_approver_authenticator",
                    args: { user: frm.doc.name },
                    callback: () => {
                        frappe.show_alert({ message: __("Authenticator reset"), indicator: "green" });
                        frm.reload_doc();
                    },
                })
            );
        }, __("Password"));
    },
});
