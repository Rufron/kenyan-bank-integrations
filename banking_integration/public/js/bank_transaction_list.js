// "Get Statement" button on the Bank Transaction list.
// Duplicated from the Account Statement form (account_statement.js) for
// easier access; the Account Statement copy will be removed later.

// Extend (don't replace) ERPNext's settings, so its get_indicator
// (Reconciled / Unreconciled) and add_fields are preserved.
frappe.listview_settings["Bank Transaction"] =
    frappe.listview_settings["Bank Transaction"] || {};

const _bt_prev_onload =
    frappe.listview_settings["Bank Transaction"].onload;

frappe.listview_settings["Bank Transaction"].onload = function (listview) {
    if (_bt_prev_onload) {
        _bt_prev_onload(listview);
    }

    listview.page.add_inner_button(
        __("Get Statement"),
        () => bt_show_statement_dialog(listview)
    );
};


function bt_show_statement_dialog(listview) {
    const dialog = new frappe.ui.Dialog({
        title: __("Get Bank Statement"),

        fields: [
            {
                fieldname: "account",
                fieldtype: "Link",
                label: __("Bank Account"),
                options: "Bank Account",
                reqd: 1,
            },
            {
                fieldname: "from_date",
                fieldtype: "Date",
                label: __("From Date"),
                reqd: 1,
                default: frappe.datetime.month_start(),
            },
            {
                fieldname: "to_date",
                fieldtype: "Date",
                label: __("To Date"),
                reqd: 1,
                default: frappe.datetime.get_today(),
            },
            {
                fieldname: "stanbic_source",
                fieldtype: "Select",
                label: __("Stanbic Source"),
                options: [
                    { value: "transactions", label: __("Latest transactions") },
                    { value: "statement", label: __("Online Account Statement (date range)") },
                ],
                default: "transactions",
                hidden: 1,
            },
        ],

        primary_action_label: __("Import"),

        primary_action(values) {
            if (values.from_date > values.to_date) {
                frappe.msgprint({
                    title: __("Invalid Date Range"),
                    message: __("From Date cannot be later than To Date."),
                    indicator: "red",
                });
                return;
            }

            dialog.hide();

            frappe.db.get_value(
                "Bank Account",
                values.account,
                "bank"
            ).then(response => {
                const bank = response.message?.bank;

                if (!bank) {
                    frappe.msgprint({
                        title: __("Bank Not Set"),
                        message: __(
                            "The selected Bank Account does not have a Bank specified."
                        ),
                        indicator: "red",
                    });
                    return;
                }

                const bank_lower = bank.toLowerCase();
                const method =
                    bank_lower.includes("stanbic")
                        ? "banking_integration.api.stanbic.fetch_statements"
                        : bank_lower.startsWith("ncba")
                            ? "banking_integration.api.ncba.fetch_statements"
                            : bank_lower.includes("equity")
                                ? "banking_integration.api.jenga.import_jenga_statement"
                                : null;

                if (!method) {
                    frappe.msgprint({
                        title: __("Not Supported"),
                        message: __("Statement import is not available for {0} yet.", [bank]),
                        indicator: "orange",
                    });
                    return;
                }

                frappe.call({
                    method,

                    args: {
                        account: values.account,
                        from_date: values.from_date,
                        to_date: values.to_date,
                        ...(bank_lower.includes("stanbic") ? { source: values.stanbic_source } : {}),
                    },

                    freeze: true,
                    freeze_message: __("Importing statement..."),

                    callback(r) {
                        bt_show_import_result(r.message, bank);
                        listview.refresh();
                    },
                });
            });
        },
    });

    // Show the Stanbic source choice only for Stanbic accounts.
    dialog.fields_dict.account.df.onchange = () => {
        const account = dialog.get_value("account");
        if (!account) return dialog.set_df_property("stanbic_source", "hidden", 1);
        frappe.db.get_value("Bank Account", account, "bank").then((r) => {
            const is_stanbic = ((r.message && r.message.bank) || "").toLowerCase().includes("stanbic");
            dialog.set_df_property("stanbic_source", "hidden", is_stanbic ? 0 : 1);
        });
    };

    dialog.show();
}


function bt_show_import_result(result, bank) {
    if (!result) return;

    frappe.msgprint({
        title: __("Statement Import Complete"),
        indicator: "green",

        message: `
            <p><strong>Bank:</strong> ${bank}</p>
            <p><strong>Account:</strong> ${result.account || ""}</p>
            <p><strong>Transactions:</strong> ${result.transactions || 0}</p>
            <p><strong>Created:</strong> ${result.created || 0}</p>
            <p><strong>Skipped:</strong> ${result.skipped || 0}</p>
            ${result.message ? `<p><strong>Note:</strong> ${frappe.utils.escape_html(result.message)}</p>` : ""}
            ${
                result.balance !== undefined
                    ? `<p><strong>Balance:</strong> ${result.balance} ${result.currency || ""}</p>`
                    : ""
            }
        `,
    });
}
