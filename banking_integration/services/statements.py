import frappe
from frappe import _
from datetime import datetime

from frappe.utils import flt, getdate

from banking_integration.clients.jenga import JengaClient
from banking_integration.clients.ncba import NCBAClient
from banking_integration.clients.stanbic import StanbicClient
from banking_integration.services.providers import get_bank_account_credentials


def sync_jenga_statement(bank_account, from_date, to_date):
    """
    Fetch a Jenga bank statement and synchronize it into
    the Account Statement DocType.

    Existing transactions are skipped using the Jenga reference
    as the Account Statement document name.
    """

    if isinstance(bank_account, str):
        bank_account = frappe.get_doc("Bank Account", bank_account)

    if not bank_account:
        frappe.throw(_("Bank Account is required"))

    if not bank_account.bank_account_no:
        frappe.throw(
            _("Bank Account {0} has no account number").format(bank_account.name)
        )

    credentials = get_bank_account_credentials(bank_account)

    if not credentials:
        frappe.throw(
            _("No Bank Integration Credentials found for {0}").format(bank_account.bank)
        )

    client = JengaClient(credentials=credentials)

    payload = {
        "accountNumber": bank_account.bank_account_no.strip(),
        "countryCode": "KE",
        "fromDate": str(from_date),
        "toDate": str(to_date),
        "limit": 100,
        "reference": "",
        "serial": "",
        "postedDateTime": "",
        "date": "",
        "runningBalance": {
            "currency": "",
            "amount": 0.0,
        },
    }

    result = client.fetch_statement(payload)

    if not result:
        frappe.throw(_("Jenga returned an empty response"))

    if not result.get("status"):
        frappe.throw(
            _("Jenga statement request failed: {0}").format(
                result.get("message") or "Unknown error"
            )
        )

    data = result.get("data") or {}
    transactions = data.get("transactions") or []

    created = 0
    skipped = 0
    failed = 0

    for transaction in transactions:
        reference = transaction.get("reference")

        if not reference:
            failed += 1
            continue

        reference = str(reference).strip()

        if frappe.db.exists("Account Statement", reference):
            skipped += 1
            continue

        running_balance = transaction.get("runningBalance") or {}

        statement = frappe.get_doc(
            {
                "doctype": "Account Statement",
                "name": reference,
                "account": bank_account.name,
                "reference": reference,
                "date": _parse_datetime(
                    transaction.get("date") or transaction.get("postedDateTime")
                ),
                "amount": transaction.get("amount") or 0,
                "serial": transaction.get("serial"),
                "description": transaction.get("description"),
                "type": transaction.get("type"),
                "new_amount": running_balance.get("amount") or 0,
                "currency": (
                    running_balance.get("currency") or data.get("currency") or "KES"
                ),
                "transaction_id": transaction.get("transactionId"),
            }
        )

        statement.insert(ignore_permissions=True)
        created += 1

    frappe.db.commit()

    return {
        "status": True,
        "message": "Statement synchronized successfully",
        "account": bank_account.name,
        "account_number": bank_account.bank_account_no,
        "currency": data.get("currency"),
        "balance": data.get("balance"),
        "transactions_returned": len(transactions),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }

def sync_stanbic_statement(account, date_from, date_to):
    bank_account = frappe.get_doc("Bank Account", account)

    account_number = bank_account.bank_account_no

    if not account_number:
        frappe.throw(
            f"Bank Account {account} does not have an account number."
        )

    client = StanbicClient(
        credentials=get_bank_account_credentials(bank_account),
        bank_account=bank_account.name,
    )

    # fetchTransactions takes only accountNumber; date_from/date_to are
    # unused (the sandbox endpoint exposes no date filter).
    response = client.fetch_transactions(
        {"accountNumber": account_number}
    )

    data = response.get("data", {}) if response else {}

    if response and response.get("status_code", 0) >= 500:
        frappe.throw(
            "Stanbic's server is not responding right now. "
            "This is on the bank's side; please try again in a few minutes."
        )

    if not response or not response.get("success"):
        frappe.throw(
            (data.get("moreInformation") if isinstance(data, dict) else None)
            or "Stanbic statement request failed "
            f"(HTTP {response.get('status_code') if response else 'n/a'})."
        )

    # The gateway returns business outcomes at HTTP 200 with an errorCode.
    error_code = data.get("errorCode") if isinstance(data, dict) else None

    if error_code and not data.get("transaction-items"):
        # 2001 = no records for this account; not an error.
        if str(error_code) == "2001":
            return {
                "status": "success",
                "account": account_number,
                "transactions": 0,
                "created": 0,
                "skipped": 0,
                "message": "No transactions found for this account.",
            }

        frappe.throw(
            data.get("errorMessage")
            or f"Stanbic statement error {error_code}."
        )

    transactions = data.get("transaction-items", []) or []

    created = 0
    skipped = 0
    failed = 0

    for tx in transactions:
        tx_id = tx.get("id")

        amount_info = tx.get("transactionAmountCurrency") or {}
        amount = amount_info.get("amount")
        currency = amount_info.get("currencyCode") or "KES"

        booking_date = tx.get("bookingDate")
        indicator = (tx.get("creditDebitIndicator") or "").upper()

        if not tx_id or amount is None or not booking_date:
            skipped += 1
            continue

        try:
            amount = abs(float(amount))
        except (TypeError, ValueError):
            skipped += 1
            continue

        # shortcut: credit when the indicator starts with "C"
        # (CRDT/CREDIT/CR/C); everything else is a debit. Revisit if
        # Stanbic uses a different encoding.
        is_credit = indicator.startswith("C")
        deposit = amount if is_credit else 0
        withdrawal = 0 if is_credit else amount

        existing = frappe.db.exists(
            "Bank Transaction",
            {
                "bank_account": bank_account.name,
                "transaction_id": tx_id,
            },
        )

        if existing:
            skipped += 1
            continue

        try:
            bank_transaction = frappe.new_doc("Bank Transaction")

            bank_transaction.update({
                "date": getdate(booking_date),
                "bank_account": bank_account.name,
                "company": bank_account.company,
                "deposit": deposit,
                "withdrawal": withdrawal,
                "currency": currency,
                "description": (
                    tx.get("transactionInformation")
                    or tx.get("narrative")
                    or ""
                ),
                "reference_number": tx_id,
                "transaction_id": tx_id,
                "status": "Unreconciled",
            })

            bank_transaction.insert(ignore_permissions=True)
            bank_transaction.submit()

            created += 1

        except Exception:
            failed += 1

            frappe.log_error(
                frappe.get_traceback(),
                f"Stanbic Bank Transaction Import Failed: {tx_id}",
            )

    frappe.db.commit()

    return {
        "status": "success",
        "account": account_number,
        "transactions": len(transactions),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "message": "Bank transactions imported successfully.",
    }

def _parse_datetime(value):
    """
    Convert Jenga's ISO datetime into a Frappe-compatible datetime.
    """

    if not value:
        return None

    try:
        return frappe.utils.get_datetime(value)
    except Exception:
        return None


def sync_ncba_statement(account, date_from, date_to):
    """Import an NCBA account statement into Bank Transactions."""
    bank_account = frappe.get_doc("Bank Account", account)

    if not bank_account.bank_account_no:
        frappe.throw(_("Bank Account {0} has no account number.").format(account))

    client = NCBAClient(get_bank_account_credentials(bank_account))

    data = client.account_statement(
        bank_account.bank_account_no,
        getdate(date_from).strftime("%d%m%Y"),
        getdate(date_to).strftime("%d%m%Y"),
    )

    if data.get("ErrorCode") != "000":
        frappe.throw(
            _("NCBA statement error {0}: {1}").format(
                data.get("ErrorCode"), data.get("ErrorMessage")
            )
        )

    transactions = data.get("AccountList") or []
    created = skipped = failed = 0

    for tx in transactions:
        # One NCBA transaction has several legs (amount, charge, excise duty);
        # the leg number is the unique key.
        tx_id = tx.get("UniqueTransactionLegNo")
        deposit = flt(tx.get("CreditAmount"))
        withdrawal = flt(tx.get("DebitAmount"))

        if not tx_id or not tx.get("ValueDate") or not (deposit or withdrawal):
            skipped += 1
            continue

        if frappe.db.exists(
            "Bank Transaction", {"bank_account": bank_account.name, "transaction_id": tx_id}
        ):
            skipped += 1
            continue

        try:
            bank_transaction = frappe.new_doc("Bank Transaction")
            bank_transaction.update({
                "date": datetime.strptime(tx["ValueDate"], "%d%m%Y").date(),
                "bank_account": bank_account.name,
                "company": bank_account.company,
                "deposit": deposit,
                "withdrawal": withdrawal,
                "currency": tx.get("TransactionCurrency") or data.get("Currency"),
                "description": " | ".join(
                    filter(None, (
                        (tx.get("Narrative") or "").strip(),
                        (tx.get("PaymentDetails") or "").strip(),
                    ))
                ),
                "reference_number": tx.get("InstrumentReferenceNumber") or tx_id,
                "transaction_id": tx_id,
                "status": "Unreconciled",
            })
            bank_transaction.insert(ignore_permissions=True)
            bank_transaction.submit()
            created += 1

        except Exception:
            failed += 1
            frappe.log_error(
                frappe.get_traceback(), f"NCBA Bank Transaction Import Failed: {tx_id}"
            )

    frappe.db.commit()

    return {
        "status": "success",
        "account": bank_account.bank_account_no,
        "transactions": len(transactions),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "balance": data.get("AvailableBalance"),
        "currency": data.get("Currency"),
    }


STANBIC_STATEMENT_LIMIT = 500


def stanbic_transaction_id(reference, signed_amount):
    """One id per statement line, shared by the online statement and IPN alerts.

    A payment and its fee share one T24 reference (FT...), as do month-end charges, so the
    reference alone is not unique. shortcut: two lines with the same reference AND the same
    signed amount would collide; revisit if Stanbic exposes a per-line id.
    """
    if not reference or isinstance(reference, dict):
        return None
    return f"{str(reference).strip()}/{flt(signed_amount):.2f}"


def sync_stanbic_online_statement(account, date_from, date_to):
    """Import Stanbic's Online Account Statement (fetchStatements) for a date range."""
    bank_account = frappe.get_doc("Bank Account", account)
    if not date_from or not date_to:
        frappe.throw("From Date and To Date are required for the online statement.")

    client = StanbicClient(
        credentials=get_bank_account_credentials(bank_account),
        bank_account=bank_account.name,
    )
    # No account number in the request: Stanbic picks the account from the API key.
    response = client.request(
        "POST",
        client.STATEMENTS_PATH,
        payload={
            "FromDate": getdate(date_from).strftime("%Y%m%d"),
            "ToDate": getdate(date_to).strftime("%Y%m%d"),
            "NoOfTxns": str(STANBIC_STATEMENT_LIMIT),
        },
        timeout=75,
    )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}

    if not response.get("success") or data.get("ResponseCode") not in (None, "00"):
        frappe.throw(
            f"Stanbic online statement error (HTTP {response.get('status_code')}): "
            f"{data.get('ResponseMessage') or data.get('moreInformation') or data.get('errorMessage') or data}"
        )

    # The live API returns "TransactionHistory"; the spec documents "transaction-items".
    transactions = data.get("TransactionHistory") or data.get("transaction-items") or []
    created = skipped = 0
    # Stanbic ignores FromDate and returns the latest NoOfTxns lines, so filter here.
    start, end = getdate(date_from), getdate(date_to)
    in_range = [
        tx for tx in transactions
        if tx.get("TransactionDate") and start <= datetime.strptime(tx["TransactionDate"], "%Y%m%d").date() <= end
    ]
    # Hitting the cap while every line is still inside the range means older lines were cut off.
    truncated = len(transactions) >= STANBIC_STATEMENT_LIMIT and len(in_range) == len(transactions)

    for tx in in_range:
        signed = flt(str(tx.get("TxnAmount") or "0").replace(",", ""))
        tx_id = stanbic_transaction_id(tx.get("T24UniqRef"), signed)
        if not tx_id or not signed or not tx.get("TransactionDate"):
            skipped += 1
            continue
        if frappe.db.exists("Bank Transaction", {"bank_account": bank_account.name, "transaction_id": tx_id}):
            skipped += 1
            continue

        amount = abs(signed)
        is_credit = signed > 0  # debits come back negative
        bt = frappe.get_doc({
            "doctype": "Bank Transaction",
            "date": datetime.strptime(tx["TransactionDate"], "%Y%m%d").date(),
            "bank_account": bank_account.name,
            "company": bank_account.company,
            "deposit": amount if is_credit else 0,
            "withdrawal": 0 if is_credit else amount,
            "currency": tx.get("TxnCurrency") or "KES",
            "description": tx.get("TxnDescr") or tx.get("TransactionType") or "",
            "reference_number": tx_id,
            "transaction_id": tx_id,
        })
        bt.insert(ignore_permissions=True)
        bt.submit()
        created += 1

    frappe.db.commit()
    return {
        "status": "success",
        "account": bank_account.bank_account_no,
        "transactions": len(in_range),
        "created": created,
        "skipped": skipped,
        **({"message": f"Stanbic returned its maximum of {STANBIC_STATEMENT_LIMIT} lines, so the "
            "start of this range may be missing. Use a shorter date range."} if truncated else {}),
    }
