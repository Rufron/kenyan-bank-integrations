frappe.ui.form.on("Payment Entry", {
    before_submit(frm) {

        // Banking integration only applies to money leaving a company
        // bank account. Skip Cash / Receive and any non-bank payment.
        if (
            frm.doc.payment_type !== "Pay"
            || frm.doc.paid_from_account_type !== "Bank"
            || !frm.doc.bank_account
            || ["Cash", "Cheque"].includes(frm.doc.mode_of_payment)
        ) {
            return;
        }

        // ...and only when that bank account has integration enabled, so
        // ordinary bank payments are untouched.
        return frappe.db.get_value(
            "Bank Account",
            frm.doc.bank_account,
            "custom_integration_enabled"
        ).then((r) => {

            if (!r.message || !r.message.custom_integration_enabled) {
                return;
            }

            return new Promise((resolve, reject) => {

                const dialog = new frappe.ui.Dialog({
                    title: __("Bank Transfer Type"),
                    fields: [
                        {
                            fieldname: "transfer_type",
                            label: __("Transaction Type"),
                            fieldtype: "Select",
                            options: [
                                "EFT",
                                "RTGS",
                                "Pesalink Bank",
                                "Pesalink Mobile",
                                "SWIFT",
                                "Mobile Wallet",
                                "Internal Bank Transfer"
                            ],
                            reqd: 1
                        }
                    ],

                    primary_action_label: __("Continue"),

                    primary_action(values) {

                        if (!values.transfer_type) {
                            frappe.msgprint(
                                __("Please select a transaction type.")
                            );
                            return;
                        }

                        // Store the selection on the Payment Entry
                        frm.set_value(
                            "custom_bank_transfer_type",
                            values.transfer_type
                        );

                        dialog.hide();

                        resolve();
                    }
                });

                dialog.show();

                // If the user closes the dialog without selecting
                dialog.$wrapper.on("hidden.bs.modal", function () {

                    if (!frm.doc.custom_bank_transfer_type) {
                        reject();
                    }

                });
            });
        });
    }
});
