// "Get Statement" button on the Bank Transaction list.
// Duplicated from the Account Statement form (account_statement.js) for
// easier access; the Account Statement copy will be removed later.

frappe.listview_settings["Bank Transaction"] = {
    onload(listview) {
        listview.page.add_inner_button(
            __("Get Statement"),
            () => bt_show_statement_dialog(listview)
        );
    },
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

                const method =
                    bank.toLowerCase().includes("stanbic")
                        ? "banking_integration.api.stanbic.fetch_statements"
                        : "banking_integration.api.jenga.import_jenga_statement";

                frappe.call({
                    method,

                    args: {
                        account: values.account,
                        from_date: values.from_date,
                        to_date: values.to_date,
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
            ${
                result.balance !== undefined
                    ? `<p><strong>Balance:</strong> ${result.balance} ${result.currency || ""}</p>`
                    : ""
            }
        `,
    });
}
