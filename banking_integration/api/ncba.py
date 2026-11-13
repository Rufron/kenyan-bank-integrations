import frappe
from frappe import _

from banking_integration.clients.ncba import NCBAClient
from banking_integration.services.providers import get_bank_account_credentials
from banking_integration.services.statements import sync_ncba_statement


@frappe.whitelist()
def fetch_statements(account: str, from_date: str, to_date: str):
    frappe.has_permission("Bank Transaction", "create", throw=True)
    return sync_ncba_statement(account, from_date, to_date)


@frappe.whitelist()
def check_transfer_status(bank_transfer: str):
    """Ask NCBA whether a sent transfer went through and update the Bank Transfer."""
    doc = frappe.get_doc("Bank Transfer", bank_transfer)
    doc.check_permission("write")

    if not doc.transaction_reference:
        frappe.throw(_("This transfer has not been sent to NCBA yet."))

    client = NCBAClient(get_bank_account_credentials(doc.source_account))
    data = client.transaction_status(doc.transaction_reference)

    # Only a confirmed "000" changes the status; any other answer is reported
    # back without marking the transfer Failed, since NCBA's failure codes are undocumented.
    if data.get("ErrorCode") == "000" and doc.status != "Successful":
        doc.status = "Successful"
        doc.bank_reference = data.get("CoreReference") or doc.bank_reference
        doc.save(ignore_permissions=True)

    return {
        "status": doc.status,
        "code": data.get("ErrorCode"),
        "message": data.get("ErrorMessage"),
        "core_reference": data.get("CoreReference"),
    }
