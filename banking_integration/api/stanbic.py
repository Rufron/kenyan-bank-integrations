import re
from datetime import datetime
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import flt

from banking_integration.api.callbacks.kcb import json_response
from banking_integration.utils.reference import generate_reference
from banking_integration.services.auth import authenticate
from banking_integration.services.balances import refresh_balance
from banking_integration.services.providers import (
    get_bank_account_credentials,
)
from banking_integration.services.statements import (
    stanbic_transaction_id,
    sync_stanbic_online_statement,
    sync_stanbic_statement,
)
from banking_integration.clients.stanbic import StanbicClient


def _client(bank_account):
    return StanbicClient(get_bank_account_credentials(bank_account), bank_account=bank_account)


def _parse_amount(text):
    """"KES 1,234.00" -> 1234.0"""
    return flt(str(text or "").replace(",", "").split()[-1]) if text else 0


@frappe.whitelist(allow_guest=True)
def ipn():
    """Stanbic transaction notification (register with register_notifications). Creates a Bank Transaction."""
    ok = {"Result": {"ResultCode": "0", "ResultDesc": "Accepted"}}
    payload = frappe.request.get_json(silent=True) or frappe.request.form.to_dict() or {}
    letter = payload.get("Letter") or payload

    try:
        bank_account = frappe.db.get_value(
            "Bank Account", {"bank_account_no": letter.get("AccountNo"), "is_company_account": 1}, "name"
        )
        # Guest endpoint: only trust a notification carrying the API key of the account it names.
        if not bank_account or letter.get("ApiKey") != _client(bank_account).api_credentials["client_id"]:
            frappe.log_error(title="Stanbic IPN rejected", message=frappe.as_json(
                {"AccountNo": letter.get("AccountNo"), "reason": "unknown account or API key mismatch"}))
            return json_response({"Result": {"ResultCode": "1", "ResultDesc": "Rejected"}}, status=403)

        amount = _parse_amount(letter.get("TxnAmount"))
        is_credit = (letter.get("ActionType") or "").upper() == "CREDIT"
        # "FT20070YCTF8999\BNK" -> "FT20070YCTF8999"; same id scheme as the online statement.
        tx_id = stanbic_transaction_id(
            (letter.get("OurTxnReference") or "").split("\\")[0], amount if is_credit else -amount
        )
        if not tx_id or frappe.db.exists("Bank Transaction", {"bank_account": bank_account, "transaction_id": tx_id}):
            return json_response(ok)

        bt = frappe.get_doc({
            "doctype": "Bank Transaction",
            "date": datetime.strptime(letter["ValueDate"], "%d/%m/%Y").date() if letter.get("ValueDate") else frappe.utils.today(),
            "bank_account": bank_account,
            "deposit": amount if is_credit else 0,
            "withdrawal": 0 if is_credit else amount,
            "currency": str(letter.get("TxnAmount") or "KES").split()[0],
            "description": " ".join(str(letter.get("Narrative") or letter.get("TxnDescr") or "").replace("\\", " ").split()),
            "reference_number": letter.get("ThirdPartyRef") or tx_id,
            "transaction_id": tx_id,
        })
        bt.insert(ignore_permissions=True)
        bt.submit()
        frappe.db.commit()
        return json_response(ok)

    except Exception:
        frappe.log_error(title="Stanbic IPN Error", message=frappe.get_traceback() + "\n" + frappe.as_json(payload))
        return json_response({"Result": {"ResultCode": "1", "ResultDesc": "Error"}}, status=500)


@frappe.whitelist()
def register_notifications(bank_account: str):
    """Ask Stanbic to send CREDIT and DEBIT alerts for this account to our ipn endpoint."""
    frappe.has_permission("Bank Account", "write", bank_account, throw=True)
    client = _client(bank_account)
    url = frappe.utils.get_url("/api/method/banking_integration.api.stanbic.ipn")
    results = {}
    for kind in ("CREDIT", "DEBIT"):
        r = client.post(client.REGISTER_URL_PATH, {
            "ReferenceId": generate_reference(),
            "ApiKey": client.api_credentials["client_id"],
            "CallBackUrl": url,
            "NotificationType": kind,
            "ProfileApprover": "ERPNext",
            "Channel": "APGW",
        })
        results[kind] = r.get("data")
    return {"callback_url": url, "results": results}


@frappe.whitelist()
def fetch_sort_codes(bank_account: str, transaction_type: str = "PESALINK"):
    frappe.has_permission("Bank Account", "read", bank_account, throw=True)
    r = _client(bank_account).fetch_sort_codes({"transactionType": transaction_type})
    if not r.get("success") or not isinstance(r.get("data"), list):
        frappe.throw(_("Stanbic sort codes error (HTTP {0}): {1}").format(r.get("status_code"), r.get("data")))
    return r["data"]


@frappe.whitelist()
def stk_push(bank_account: str, mobile_number: str, amount: float, account_reference: str):
    """Send an M-PESA payment prompt to a customer's phone; the money lands in this Stanbic account."""
    frappe.has_permission("Bank Account", "write", bank_account, throw=True)
    if not re.fullmatch(r"254\d{9}", mobile_number or ""):
        frappe.throw(_("Enter the mobile number as 2547XXXXXXXX (12 digits)."))
    if flt(amount) <= 0:
        frappe.throw(_("Amount must be greater than zero."))

    reference = generate_reference()
    r = _client(bank_account).mpesa_checkout({
        "dbsReferenceId": reference,
        "billAccountRef": account_reference,
        "amount": f"{flt(amount):.2f}",
        "mobileNumber": mobile_number,
    })
    frappe.log_error(title="Stanbic STK Push", message=frappe.as_json({"reference": reference, "response": r}))
    return {"reference": reference, **r}


@frappe.whitelist()
def check_transfer_status(bank_transfer: str):
    """Ask Stanbic for a transfer's outcome and update the Bank Transfer when it is final."""
    frappe.has_permission("Bank Transfer", "read", bank_transfer, throw=True)
    doc = frappe.get_doc("Bank Transfer", bank_transfer)
    if not doc.transaction_reference:
        frappe.throw(_("This transfer was never sent, so there is nothing to check."))

    client = _client(doc.source_account)
    r = client.get(f"{client.TRANSACTION_STATUS_PATH}?dbsReferenceId={quote(doc.transaction_reference)}")
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    bank_status = (data.get("bankStatus") or "").upper()

    if r.get("success") and bank_status:
        if bank_status == "PROCESSED":
            doc.status = "Successful"
        elif bank_status in ("REJECTED", "FAILED", "DECLINED"):
            doc.status = "Failed"
        doc.bank_reference = data.get("bankReferenceId") or doc.bank_reference
        doc.save(ignore_permissions=True)

    return {"http": r.get("status_code"), "bank_status": bank_status, "reason": data.get("reasonText"),
            "bank_reference": data.get("bankReferenceId"), "status": doc.status, "response": r.get("data")}


@frappe.whitelist()
def fetch_statements(account: str, from_date: str | None = None, to_date: str | None = None, source: str | None = None):
    # source "statement" = Online Account Statement (date range); default = latest transactions.
    if source == "statement":
        return sync_stanbic_online_statement(account, from_date, to_date)
    return sync_stanbic_statement(account, from_date, to_date)

@frappe.whitelist()
def authenticate_stanbic(account: str):
    credentials = get_bank_account_credentials(account)

    client = StanbicClient(credentials, bank_account=account)

    client.get_access_token(force_refresh=True)

    return {
        "success": True,
        "message": "Stanbic authentication successful",
        "expires_at": client.token_expiry,
    }
