frappe.ui.form.on("Bank Account", {
    refresh(frm) {
        if (!frm.is_new() && frappe.user.has_role("Bank Transfer Approver")) {
            frm.add_custom_button(__("Refresh Balance"), () => {
                frappe.call({
                    method: "banking_integration.services.bank_account.refresh_balance",
                    args: {
                        bank_account: frm.doc.name
                    },
                    freeze: true,
                    freeze_message: __("Fetching balance from bank..."),
                    callback(r) {
                        if (!r.exc) {
                            frm.reload_doc();
                            frappe.show_alert({
                                message: __("Balance updated successfully"),
                                indicator: "green"
                            });
                        }
                    }
                });
            });
        }

        if (!frm.is_new() && frm.doc.is_company_account && (frm.doc.bank || "").toLowerCase().includes("stanbic")) {
            add_stanbic_buttons(frm);
        }
    }
});


const STANBIC_API = "banking_integration.api.stanbic.";
const stanbic_esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
const stanbic_show_json = (title, data) => frappe.msgprint({
    title, message: `<pre style="white-space:pre-wrap">${stanbic_esc(JSON.stringify(data, null, 2))}</pre>`,
});

function add_stanbic_buttons(frm) {
    const group = __("Stanbic");

    frm.add_custom_button(__("Fetch Sort Codes"), () => {
        frappe.prompt(
            { fieldname: "transaction_type", fieldtype: "Select", label: __("Transaction Type"),
              options: ["PESALINK", "EFT", "RTGS"], default: "PESALINK", reqd: 1 },
            ({ transaction_type }) => frappe.call({
                method: STANBIC_API + "fetch_sort_codes",
                args: { bank_account: frm.doc.name, transaction_type },
                freeze: true, freeze_message: __("Asking Stanbic..."),
                callback(r) {
                    const rows = (r.message || []).map(c =>
                        `<tr><td>${stanbic_esc(c.sortCodeId)}</td><td>${stanbic_esc(c.bankName)}</td></tr>`).join("");
                    frappe.msgprint({
                        title: __("{0} sort codes ({1})", [transaction_type, (r.message || []).length]),
                        message: `<table class="table table-bordered"><tr><th>${__("Code")}</th><th>${__("Bank")}</th></tr>${rows}</table>`,
                        wide: true,
                    });
                },
            }),
            __("Fetch Sort Codes"), __("Fetch")
        );
    }, group);

    frm.add_custom_button(__("M-PESA STK Push"), () => {
        frappe.prompt([
            { fieldname: "mobile_number", fieldtype: "Data", label: __("Customer Mobile (2547XXXXXXXX)"), reqd: 1 },
            { fieldname: "amount", fieldtype: "Currency", label: __("Amount"), reqd: 1 },
            { fieldname: "account_reference", fieldtype: "Data", label: __("Account Reference (e.g. invoice no.)"), reqd: 1 },
        ], (values) => frappe.call({
            method: STANBIC_API + "stk_push",
            args: { bank_account: frm.doc.name, ...values, mobile_number: values.mobile_number.replace(/\D/g, "") },
            freeze: true, freeze_message: __("Sending payment prompt..."),
            callback: (r) => stanbic_show_json(__("STK Push response"), r.message),
        }), __("M-PESA STK Push"), __("Send Prompt"));
    }, group);

    frm.add_custom_button(__("Register Notifications"), () => {
        frappe.confirm(
            __("Ask Stanbic to send credit and debit alerts for this account to this site?"),
            () => frappe.call({
                method: STANBIC_API + "register_notifications",
                args: { bank_account: frm.doc.name },
                freeze: true, freeze_message: __("Registering with Stanbic..."),
                callback: (r) => stanbic_show_json(__("Notification registration"), r.message),
            })
        );
    }, group);
}
