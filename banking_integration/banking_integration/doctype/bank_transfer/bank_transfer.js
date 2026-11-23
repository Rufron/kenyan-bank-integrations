// Copyright (c) 2026, Nathan Wagacha and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bank Transfer", {

    refresh(frm) {
        const is_new = frm.is_new();

        // Bank Transfer is a tracking document.
        // Users should not create one directly.
        if (is_new) {
            frm.disable_save();
        }

        // Only a Bank Transfer Approver can send it.
        if (
            !is_new &&
            ["Draft", "Pending Approval", "Failed"].includes(frm.doc.status)
            && frappe.user.has_role("Bank Transfer Approver")
        ) {
            frm.add_custom_button(
                __("Approve & Send"),
                () => {
                    confirm_and_send_transfer(frm);
                }
            );
        }

        if (!is_new && frm.doc.transaction_reference && (frm.doc.bank || "").toLowerCase().includes("stanbic")) {
            frm.add_custom_button(__("Check Status with Stanbic"), () => {
                frappe.call({
                    method: "banking_integration.api.stanbic.check_transfer_status",
                    args: { bank_transfer: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Asking Stanbic..."),
                    callback(r) {
                        const s = r.message || {};
                        const esc = frappe.utils.escape_html;
                        frappe.msgprint({
                            title: __("Stanbic Transaction Status"),
                            indicator: s.bank_status === "PROCESSED" ? "green" : "orange",
                            message: `
                                <p><strong>${__("Stanbic says")}:</strong> ${esc(s.bank_status || "-")} ${esc(s.reason || "")} (HTTP ${esc(String(s.http || ""))})</p>
                                <p><strong>${__("Bank reference")}:</strong> ${esc(s.bank_reference || "-")}</p>
                                <p><strong>${__("Transfer status")}:</strong> ${esc(s.status || "")}</p>`,
                        });
                        frm.reload_doc();
                    },
                });
            });
        }

        if (
            !is_new
            && frm.doc.transaction_reference
            && (frm.doc.bank || "").toUpperCase().startsWith("NCBA")
        ) {
            frm.add_custom_button(__("Check Status with NCBA"), () => {
                frappe.call({
                    method: "banking_integration.api.ncba.check_transfer_status",
                    args: { bank_transfer: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Asking NCBA..."),
                    callback(r) {
                        const s = r.message || {};
                        frappe.msgprint({
                            title: __("NCBA Transaction Status"),
                            indicator: s.code === "000" ? "green" : "orange",
                            message: `
                                <p><strong>${__("NCBA says")}:</strong> ${frappe.utils.escape_html(s.message || "")} (${frappe.utils.escape_html(s.code || "")})</p>
                                <p><strong>${__("Core reference")}:</strong> ${frappe.utils.escape_html(s.core_reference || "-")}</p>
                                <p><strong>${__("Transfer status")}:</strong> ${frappe.utils.escape_html(s.status || "")}</p>`,
                        });
                        frm.reload_doc();
                    },
                });
            });
        }
    },
});


const BT_METHOD = "banking_integration.banking_integration.doctype.bank_transfer.bank_transfer.";

function confirm_and_send_transfer(frm) {
    frappe.call(BT_METHOD + "authenticator_is_set_up").then(r => {
        r.message ? show_approve_dialog(frm) : setup_authenticator(frm);
    });
}


// One-time: password, then a code emailed to the approver, then link the authenticator app.
function setup_authenticator(frm) {
    frappe.prompt(
        { fieldname: "password", fieldtype: "Password", label: __("Your ERPNext password"), reqd: 1 },
        ({ password }) => frappe.call({
            method: BT_METHOD + "send_setup_email_code", args: { password },
            freeze: true, freeze_message: __("Emailing you a code..."),
        }).then(() => frappe.prompt(
            { fieldname: "email_code", fieldtype: "Data", label: __("Code we just emailed you"), reqd: 1 },
            ({ email_code }) => show_authenticator_qr(frm, email_code),
            __("Check your email"),
            __("Continue")
        )),
        __("Set up approval codes"),
        __("Continue")
    );
}


function show_authenticator_qr(frm, email_code) {
    frappe.call(BT_METHOD + "start_authenticator_setup", { email_code }).then(r => {
        const dialog = new frappe.ui.Dialog({
            title: __("Link your authenticator app"),
            fields: [
                {
                    fieldtype: "HTML",
                    options: `<p>${__("Scan this with your authenticator app:")}</p>
                        <ul class="small">
                            <li>${__("Google Authenticator: tap +, then Scan a QR code")}</li>
                            <li>${__("Microsoft Authenticator: tap +, then Other account (Google, Facebook, etc.)")}</li>
                        </ul>
                        <p style="text-align:center"><img src="data:image/svg+xml;base64,${r.message}"></p>
                        <p class="text-muted small">${__("Make sure nobody else can see your screen. This code works for 10 minutes, only on this attempt.")}</p>`,
                },
                { fieldname: "otp", fieldtype: "Data", label: __("Code now shown in the app"), reqd: 1 },
            ],
            primary_action_label: __("Confirm"),
            primary_action({ otp }) {
                frappe.call(BT_METHOD + "finish_authenticator_setup", { otp }).then(() => {
                    dialog.hide();
                    frappe.show_alert({ message: __("Authenticator linked"), indicator: "green" });
                    show_approve_dialog(frm);
                });
            },
        });
        dialog.show();
    });
}


function show_approve_dialog(frm) {
    const d = frm.doc;
    const esc = frappe.utils.escape_html;
    const dialog = new frappe.ui.Dialog({
        title: __("Approve Bank Transfer"),

        fields: [
            {
                fieldtype: "HTML",
                options: `
                    <p><strong>${__("Amount")}:</strong> ${format_currency(d.amount, d.currency)}</p>
                    <p><strong>${__("To")}:</strong> ${esc(d.beneficiary_name || "")}</p>
                    <p><strong>${__("Account")}:</strong> ${esc(d.beneficiary_account_number || "")} (${esc(d.destination_account || "")})</p>
                    <p><strong>${__("From")}:</strong> ${esc(d.source_account || "")}</p>
                    <p><strong>${__("Method")}:</strong> ${esc(d.transfer_type || "")}</p>`,
            },
            {
                fieldname: "otp",
                fieldtype: "Data",
                label: __("6-digit code from your authenticator app"),
                reqd: 1,
            },
        ],

        primary_action_label: __(
            "Approve & Send"
        ),

        primary_action(values) {
            dialog.hide();

            frappe.call({
                method:
                    "banking_integration.banking_integration.doctype.bank_transfer.bank_transfer.approve_and_send",

                args: {
                    bank_transfer:
                        frm.doc.name,

                    otp: values.otp,
                    modified: frm.doc.modified,
                },

                freeze: true,

                freeze_message: __(
                    "Sending transfer..."
                ),

                callback(r) {
                    if (!r.exc) {
                        frm.reload_doc();
                    }
                },
            });
        },
    });

    dialog.show();
}
