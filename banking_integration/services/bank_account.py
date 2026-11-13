import frappe
from frappe import _

from frappe.utils import flt

from banking_integration.clients.jenga import JengaClient
from banking_integration.clients.ncba import NCBAClient
from banking_integration.clients.stanbic import StanbicClient
from banking_integration.services.providers import get_bank_account_credentials


# Balance refresh is implemented for Equity (Jenga), NCBA and Stanbic.
JENGA_BANKS = {"Equity Bank", "Equity Bank Kenya"}
NCBA_BANKS = {"NCBA", "NCBA Bank", "NCBA Bank Kenya"}
STANBIC_BANKS = {"Stanbic Bank", "Stanbic Bank Kenya"}


def _refresh_stanbic_balance(doc):
    # The balance API takes no account number: Stanbic resolves the account from
    # the API key, which StanbicClient picks per Bank Account (Bank API Keys row).
    client = StanbicClient(credentials=get_bank_account_credentials(doc), bank_account=doc.name)

    # Stanbic's gateway can take ~60s to answer, so allow 75s (see fetch_transactions).
    result = client.request("GET", StanbicClient.BALANCE_PATH, timeout=75)
    data = result.get("data") or {}

    if not result.get("success") or not isinstance(data, dict) or data.get("errorCode"):
        message = (
            data.get("errorMessage") or data.get("moreInformation") or data.get("httpMessage")
            if isinstance(data, dict) else None
        )
        frappe.throw(
            _("Stanbic balance error (HTTP {0}): {1}").format(
                result.get("status_code"), message or _("no details returned")
            )
        )

    balance = _parse_stanbic_balance(data.get("availableBalance"))

    if balance is None:
        frappe.throw(_("Could not read the balance from Stanbic's response: {0}").format(data))

    doc.custom_reported_balance = balance
    doc.save(ignore_permissions=True)

    return {
        "account": doc.name,
        "balance": balance,
        "currency": frappe.db.get_value("Account", doc.account, "account_currency"),
        "response": data,
    }


def _parse_stanbic_balance(value):
    """Stanbic documents availableBalance as "[100000.00]": accept a list, that string, or a plain number."""
    if isinstance(value, list):
        value = value[0] if value else None

    if isinstance(value, str):
        value = value.strip().strip("[]").strip().strip('"').replace(",", "")

    if value in (None, ""):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _refresh_ncba_balance(doc):
    client = NCBAClient(get_bank_account_credentials(doc))
    result = client.account_details(doc.bank_account_no)

    if result.get("ErrorCode") != "000" or result.get("AvailableBalance") in (None, ""):
        frappe.throw(
            _("NCBA balance error {0}: {1}").format(
                result.get("ErrorCode"), result.get("ErrorMessage")
            )
        )

    doc.custom_reported_balance = flt(result["AvailableBalance"])
    doc.save(ignore_permissions=True)

    return {
        "account": doc.name,
        "balance": doc.custom_reported_balance,
        "currency": result.get("Currency"),
        "response": result,
    }


@frappe.whitelist()
def refresh_balance(bank_account):
    doc = frappe.get_doc("Bank Account", bank_account)

    if not doc.bank_account_no:
        frappe.throw(_("Bank Account Number is required."))

    if doc.bank in NCBA_BANKS:
        return _refresh_ncba_balance(doc)

    if doc.bank in STANBIC_BANKS:
        return _refresh_stanbic_balance(doc)

    if doc.bank not in JENGA_BANKS:
        frappe.throw(
            _("Balance refresh is not supported for {0} yet.").format(doc.bank)
        )

    client = JengaClient(bank_account=doc)

    result = client.get_balance(doc.bank_account_no)

    balances = result.get("data", {}).get("balances", [])

    balance = None

    for item in balances:
        if item.get("type") == "Available":
            balance = float(item.get("amount", 0))
            break

    if balance is None and balances:
        balance = float(balances[0].get("amount", 0))

    if balance is None:
        frappe.throw(
            _("Could not find balance in bank API response.")
        )

    doc.custom_reported_balance = balance
    doc.save(ignore_permissions=True)

    return {
        "account": doc.name,
        "balance": balance,
        "currency": result.get("data", {}).get("currency"),
        "response": result,
    }