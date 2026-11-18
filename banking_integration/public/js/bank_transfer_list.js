frappe.listview_settings["Bank Transfer"] = {
    onload(listview) {
        if (!frappe.user.has_role("Bank Transfer Approver")) return;

        listview.page.add_action_item(__("NCBA H2H Payment File"), () => {
            const names = listview.get_checked_items(true);
            frappe.call({
                method: "banking_integration.services.ncba_h2h.generate_h2h_file",
                args: { bank_transfers: names },
                freeze: true,
                callback(r) {
                    if (!r.message) return;
                    const { filename, content, count } = r.message;
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(a.href);
                    frappe.show_alert({ message: __("{0} payments in {1}", [count, filename]), indicator: "green" });
                },
            });
        });
    },
};
