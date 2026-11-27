# Copyright (c) 2026, Nathan Wagacha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, today

from banking_integration.clients.jenga import JengaClient
from banking_integration.clients.ncba import NCBAClient
from banking_integration.clients.stanbic import StanbicClient
from banking_integration.clients.kcb import KCBClient
from banking_integration.services.balances import refresh_balance
from banking_integration.services.providers import (
    get_bank_account_credentials,
)
from banking_integration.utils.reference import generate_reference


STANBIC_ENDPOINT_MAP = {
    "Pesalink Bank": "/api/sandbox/payments/pesalink",
    "Pesalink Mobile": "/api/sandbox/payments/pesalink/mobile",
    "RTGS": "/api/sandbox/payments/rtgs",
    "Mobile Wallet": "/api/sandbox/payments/mobile",
    "B2C": "/api/sandbox/payments/b2c",
    "STK Push": "/api/sandbox/payments/stkpush",
}


# =====================================================================
# MAIN TRANSFER DISPATCHER
# =====================================================================


def send_money(bank_transfer: str):
    """
    Send an approved Bank Transfer through its configured provider.

    Bank Transfer is the ERP tracking document.
    The actual money movement is performed by the configured
    banking provider.
    """

    doc = frappe.get_doc(
        "Bank Transfer",
        bank_transfer,
    )

    # Only approve_and_send (approver role + authenticator code) may release money.
    if frappe.flags.get("bank_transfer_approved") != doc.name:
        frappe.throw(_("Bank Transfers can only be sent through Approve & Send."), frappe.PermissionError)

    if doc.status != "Approved":
        frappe.throw(
            _(
                "Bank Transfer must be Approved before it can be sent."
            )
        )

    dispatcher = {
    "Equity Bank Kenya": _send_via_jenga,
    "Stanbic Bank Kenya": _send_via_stanbic,
    "KCB Bank Kenya": _send_via_kcb,
    "Equity Bank": _send_via_jenga,
    "Stanbic Bank": _send_via_stanbic,
    "Kenya Commercial Bank": _send_via_kcb,
    "NCBA": _send_via_ncba,
    "NCBA Bank": _send_via_ncba,
    "NCBA Bank Kenya": _send_via_ncba,
}.get(doc.bank)

    if not dispatcher:
        frappe.throw(
            _(
                "Unsupported bank: {0}"
            ).format(
                doc.bank
            )
        )

    # Prevent accidental double submission.
    if doc.status in (
        "Processing",
        "Successful",
    ):
        frappe.throw(
            _(
                "Bank Transfer {0} has already been submitted."
            ).format(
                doc.name
            )
        )

    doc.status = "Processing"

    doc.save(
        ignore_permissions=True
    )

    try:

        dispatcher(doc)

    except Exception as e:

        doc.status = "Failed"

        doc.response_json = frappe.as_json(
            {
                "error": str(e),
            },
            indent=2,
        )

        doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        raise

    return doc


# =====================================================================
# JENGA
# =====================================================================


def _send_via_jenga(doc):
    """
    Dispatch a Bank Transfer to the correct Jenga transaction API.

    Each transaction type has its own payload builder and its own
    JengaClient method.
    """

    source = frappe.get_doc(
        "Bank Account",
        doc.source_account,
    )

    credentials = get_bank_account_credentials(
        source
    )

    client = JengaClient(
        credentials=credentials
    )

    reference = (
        doc.transaction_reference
        or generate_reference()
    )

    doc.transaction_reference = reference

    # ---------------------------------------------------------------
    # Internal Bank Transfer
    # ---------------------------------------------------------------

    if doc.transfer_type == "Internal Bank Transfer":

        response = _jenga_internal_transfer(
            client=client,
            doc=doc,
            source=source,
            reference=reference,
        )

    # ---------------------------------------------------------------
    # RTGS
    # ---------------------------------------------------------------

    elif doc.transfer_type == "RTGS":

        payload = _build_jenga_rtgs_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.rtgs_transfer(
            payload
        )

    # ---------------------------------------------------------------
    # Pesalink - Bank Account
    # ---------------------------------------------------------------

    elif doc.transfer_type == "Pesalink Bank":

        payload = _build_jenga_pesalink_account_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.pesalink_account_transfer(
            payload
        )

    # ---------------------------------------------------------------
    # Pesalink - Mobile
    # ---------------------------------------------------------------

    elif doc.transfer_type == "Pesalink Mobile":

        payload = _build_jenga_pesalink_mobile_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.pesalink_mobile_transfer(
            payload
        )

    # ---------------------------------------------------------------
    # Mobile Wallet
    # ---------------------------------------------------------------

    elif doc.transfer_type == "Mobile Wallet":

        payload = _build_jenga_mobile_wallet_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.mobile_wallet_transfer(
            payload
        )

    # ---------------------------------------------------------------
    # SWIFT
    # ---------------------------------------------------------------

    elif doc.transfer_type == "SWIFT":

        payload = _build_jenga_swift_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.swift_transfer(
            payload
        )

    # ---------------------------------------------------------------
    # Subsidiary
    # ---------------------------------------------------------------

    elif doc.transfer_type == "Subsidiary":

        payload = _build_jenga_subsidiary_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        doc.request_json = frappe.as_json(
            payload,
            indent=2,
        )

        response = client.subsidiary_transfer(
            payload
        )

    else:

        frappe.throw(
            _(
                "Jenga transfer type {0} is not supported."
            ).format(
                doc.transfer_type
            )
        )

    # ---------------------------------------------------------------
    # Save API response
    # ---------------------------------------------------------------

    doc.response_json = frappe.as_json(
        response,
        indent=2,
    )

    doc.bank_reference = (
        response.get("reference")
        or response.get("transactionId")
        or response.get("data", {}).get("transactionId")
    )

    doc.status = (
        "Successful"
        if response.get("status") is True
        else "Failed"
    )

    doc.save(
        ignore_permissions=True
    )

    frappe.db.commit()

    return response


# =====================================================================
# JENGA - INTERNAL BANK TRANSFER
# =====================================================================


def _jenga_internal_transfer(
    client,
    doc,
    source,
    reference,
):
    """
    Build and send an internal transfer between Equity/Jenga
    supported bank accounts.
    """

    destination = frappe.get_doc(
        "Bank Account",
        doc.destination_account,
    )

    # ---------------------------------------------------------------
    # Capture currently stored balances
    # ---------------------------------------------------------------

    doc.source_balance_before = (
        source.custom_reported_balance
    )


    # ---------------------------------------------------------------
    # Build payload
    # ---------------------------------------------------------------

    payload = {
        "source": {
            "countryCode": _get_country_code(source),
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },
        "destination": {
            "type": "bank",
            "countryCode": _get_country_code(destination),
            "name": destination.account_name,
            "accountNumber": destination.bank_account_no,
        },
        "transfer": {
            "type": "InternalFundsTransfer",
            "amount": str(doc.amount),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }

    doc.request_json = frappe.as_json(
        payload,
        indent=2,
    )

    # ---------------------------------------------------------------
    # Send transfer
    # ---------------------------------------------------------------

    response = client.internal_bank_transfer(
        payload
    )

    # ---------------------------------------------------------------
    # Refresh balances AFTER successful transfer
    # ---------------------------------------------------------------

    if response.get("status") is True:

        try:
            refresh_balance(source.name)
            refresh_balance(destination.name)

            # Reload because refresh_balance() saves the documents
            source.reload()
            destination.reload()

            doc.source_balance_after = (
                source.custom_reported_balance
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Jenga Internal Transfer - Balance Refresh Failed",
            )

    return response

# =====================================================================
# JENGA - RTGS
# =====================================================================


def _build_jenga_rtgs_payload(
    doc,
    source,
    reference,
):
    """
    Build the payload required by Jenga RTGS.
    """

    return {
        "source": {
            "countryCode": _get_country_code(source),
            "currency": doc.currency,
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },

        "destination": {
            "type": "bank",
            "countryCode": doc.beneficiary_country,
            "name": doc.beneficiary_name,
            "bankCode": doc.bank_code,
            "accountNumber": doc.beneficiary_account_number,
        },

        "transfer": {
            "type": "RTGS",
            "amount": str(doc.amount),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
            "purposeOfPaymentCode": (
                doc.purpose_of_payment_code
                if getattr(
                    doc,
                    "purpose_of_payment_code",
                    None,
                )
                else "OTHR"
            ),
        },
    }

# =====================================================================
# JENGA - PESALINK BANK ACCOUNT
# =====================================================================


def _build_jenga_pesalink_account_payload(
    doc,
    source,
    reference,
):
    """
    Build Pesalink-to-bank-account payload.
    """

    return {
        "source": {
            "countryCode": _get_country_code(
                source
            ),
            "currency": doc.currency,
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },
        "destination": {
            "type": "bank",
            "countryCode": doc.beneficiary_country,
            "name": doc.beneficiary_name,
            "bankCode": doc.bank_code,
            "accountNumber": doc.beneficiary_account_number,
        },
        "transfer": {
            "type": "Pesalink",
            "amount": str(
                doc.amount
            ),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }


# =====================================================================
# JENGA - PESALINK MOBILE
# =====================================================================


def _build_jenga_pesalink_mobile_payload(
    doc,
    source,
    reference,
):
    """
    Build Pesalink-to-mobile-number payload.
    """

    return {
        "source": {
            "countryCode": _get_country_code(
                source
            ),
            "currency": doc.currency,
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },
        "destination": {
            "type": "mobile",
            "countryCode": doc.beneficiary_country,
            "name": doc.beneficiary_name,
            "mobileNumber": doc.beneficiary_mobile_number,
        },
        "transfer": {
            "type": "PesalinkMobile",
            "amount": str(
                doc.amount
            ),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }


# =====================================================================
# JENGA - MOBILE WALLET
# =====================================================================


def _build_jenga_mobile_wallet_payload(
    doc,
    source,
    reference,
):
    """
    Build mobile-wallet transfer payload.
    """

    return {
        "source": {
            "countryCode": _get_country_code(
                source
            ),
            "currency": doc.currency,
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },
        "destination": {
            "type": "mobile",
            "countryCode": doc.beneficiary_country,
            "name": doc.beneficiary_name,
            "mobileNumber": doc.beneficiary_mobile_number,
            "walletName": doc.wallet_name,
        },
        "transfer": {
            "type": "MobileWallet",
            "amount": str(
                doc.amount
            ),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }


# =====================================================================
# JENGA - SWIFT
# =====================================================================


def _build_jenga_swift_payload(
    doc,
    source,
    reference,
):
    """
    Build the Jenga SWIFT transfer payload.
    """

    source_currency = frappe.db.get_value(
        "Account",
        source.account,
        "account_currency",
    )

    if not source_currency:
        frappe.throw(
            _(
                "Could not determine the currency for source Account {0}."
            ).format(
                source.account
            )
        )

    return {
        "source": {
            "countryCode": _get_country_code(source),
            "sourceCurrency": source_currency,
            "name": doc.sender_name or source.account_name,
            "accountNumber": source.bank_account_no,
        },

        "destination": {
            "type": "bank",
            "countryCode": doc.beneficiary_country,
            "currency": doc.destination_currency,
            "name": doc.beneficiary_name,
            "bankBic": doc.bank_bic,
            "accountNumber": doc.beneficiary_account_number,
            "addressline1": ", ".join((doc.beneficiary_address or "").splitlines()),
        },

        "transfer": {
            "type": "SWIFT",
            "amount": str(doc.amount),
            "currencyCode": doc.destination_currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }

# =====================================================================
# JENGA - SUBSIDIARY
# =====================================================================


def _build_jenga_subsidiary_payload(
    doc,
    source,
    reference,
):
    """
    Build Equity subsidiary transfer payload.
    """

    return {
        "source": {
            "countryCode": _get_country_code(
                source
            ),
            "currency": doc.currency,
            "name": source.account_name,
            "accountNumber": source.bank_account_no,
        },
        "destination": {
            "type": "bank",
            "countryCode": doc.beneficiary_country,
            "name": doc.beneficiary_name,
            "bankCode": doc.bank_code,
            "accountNumber": doc.beneficiary_account_number,
        },
        "transfer": {
            "type": "Subsidiary",
            "amount": str(
                doc.amount
            ),
            "currencyCode": doc.currency,
            "reference": reference,
            "date": today(),
            "description": doc.description,
        },
    }


# =====================================================================
# STANBIC
# =====================================================================

def _send_via_stanbic(doc):
    """
    Send a Bank Transfer through Stanbic.

    The payment rail is determined by doc.transfer_type and mapped
    to the corresponding Stanbic sandbox API.
    """

    source = frappe.get_doc(
        "Bank Account",
        doc.source_account,
    )

    credentials = get_bank_account_credentials(
        source
    )

    client = StanbicClient(
        credentials,
        bank_account=source.name,
    )

    reference = (
        doc.transaction_reference
        or generate_reference()
    )

    doc.transaction_reference = reference

    # ---------------------------------------------------------------
    # Stanbic payment rail
    # ---------------------------------------------------------------

    if doc.transfer_type == "EFT":

        payload = _build_stanbic_eft_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = "/api/sandbox/eft-payments/"

    elif doc.transfer_type == "Pesalink Bank":

        payload = _build_stanbic_pesalink_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = "/api/sandbox/pesalink-payments/"

    elif doc.transfer_type == "RTGS":

        payload = _build_stanbic_rtgs_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = "/api/sandbox/rtgs-payments/"

    elif doc.transfer_type == "SWIFT":

        payload = _build_stanbic_swift_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = StanbicClient.SWIFT_PATH

    elif doc.transfer_type == "Mobile Wallet":

        payload = _build_stanbic_mobile_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = "/api/sandbox/mobile-payments/"

    elif doc.transfer_type == "Internal Bank Transfer":

        payload = _build_stanbic_internal_transfer_payload(
            doc=doc,
            source=source,
            reference=reference,
        )

        endpoint = StanbicClient.STANBIC_PAYMENT_PATH

    else:

        frappe.throw(
            _(
                "Transfer Type {0} is not supported for Stanbic."
            ).format(
                doc.transfer_type
            )
        )

    # ---------------------------------------------------------------
    # Store request
    # ---------------------------------------------------------------

    doc.request_json = frappe.as_json(
        payload,
        indent=2,
    )

    # ---------------------------------------------------------------
    # Send request
    # ---------------------------------------------------------------

    result = client.post(
        endpoint,
        payload,
    )

    # ---------------------------------------------------------------
    # Store response
    # ---------------------------------------------------------------

    doc.response_json = frappe.as_json(
        result,
        indent=2,
    )

    response_data = result.get(
        "data",
        {},
    )

    tx_details = response_data.get("TransactionDetails") or {}

    doc.bank_reference = (
        response_data.get("dbsReferenceId")
        or response_data.get("bankReferenceId")
        or response_data.get("transactionId")
        or response_data.get("reference")
        or tx_details.get("UTN")
        or tx_details.get("DBPUniqueTransactionNumber")
        or reference
    )

    # Stanbic returns business outcomes at HTTP 200, so result["success"]
    # (HTTP-level) is NOT the payment outcome. Rails differ: RTGS/mobile
    # report bankStatus; EFT reports ResponseCode ("00" = success).
    bank_status = (response_data.get("bankStatus") or "").upper()
    tx_error = (tx_details.get("Error") or "").strip().strip(",")
    tx_status = (tx_details.get("TransactionStatus") or "").strip()
    response_msg = (response_data.get("ResponseMessage") or "").strip().lower()

    # Stanbic returns business outcomes at HTTP 200; each rail reports
    # success differently. NOTE: EFT success is ResponseCode/TxnStatus
    # "001" (not "00" as the Swagger sample shows), so key off TxnStatus +
    # Error, not ResponseCode.
    if not result.get("success"):
        ok = False
    elif bank_status:
        ok = bank_status not in (
            "REJECTED", "FAILED", "DECLINED", "RJCT", "REVERSED",
        )
    elif tx_error and tx_error not in ("ERR00000", "00", "0"):
        ok = False                       # EFT transaction-level error
    elif tx_status:
        ok = tx_status == "001"          # EFT: 001 success, 002 failure
    elif response_msg:
        ok = response_msg == "success"
    else:
        ok = True

    doc.status = "Successful" if ok else "Failed"

    doc.save(ignore_permissions=True)

    frappe.db.commit()

    return result

def _build_stanbic_eft_payload(
    doc,
    source,
    reference,
):
    return {
        "SourceChannel": "BCBC",
        "SourceMsgId": reference,
        "CreatedTime": frappe.utils.now_datetime().strftime(
            "%H:%M"
        ),
        "DebitAccount": source.bank_account_no,
        "DBPUniqueTransactionNumber": reference,
        "BeneficiaryAcctNo": doc.beneficiary_account_number,
        "BeneficiaryName": doc.beneficiary_name,
        "BeneficiaryBankCode": doc.bank_code or "17000",
        "BeneficiaryAddr": "Nairobi",
        "ExchangeRate": "",
        "CreditAmount": str(doc.amount),
        "CreditCurrency": doc.currency or "KES",
        "PaymentDetails": " ".join(
            (doc.description or "EFT Payment").split()
        )[:35],
        "FxDealId": "",
        "Execute": "1",
        "PaymentType": "SBK.BCB.EFT.CREDIT",
        "ExecutionDate": frappe.utils.now_datetime().strftime("%d/%m/%Y"),
        "Attachments": [],
    }

def _stanbic_mobile(number):
    """Stanbic expects 2547XXXXXXXX; accept 07XX / +254 / spaced input."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) == 10 and digits.startswith("0"):
        return "254" + digits[1:]
    return digits


def _stanbic_common(doc, reference, default_narrative):
    """Fields shared by Stanbic's Pesalink, SWIFT and mobile payment requests."""
    narrative = " ".join((doc.description or default_narrative).split())[:35]
    return narrative, {
        "requestedExecutionDate": frappe.utils.today(),
        "dbsReferenceId": reference,
        "txnNarrative": narrative,
        "callBackUrl": frappe.utils.get_url(
            "/api/method/banking_integration.api.callbacks.stanbic.handle_payment_notification"
        ),
    }


def _build_stanbic_pesalink_payload(doc, source, reference):
    narrative, payload = _stanbic_common(doc, reference, "Pesalink Payment")
    if not doc.bank_code:
        frappe.throw(_("The beneficiary's Bank Account needs a Bank Code for Pesalink."))
    payload.update({
        "originatorAccount": {"identification": {"mobileNumber": _stanbic_mobile(source.get("custom_sender_mobile_number"))}},
        "sendMoneyTo": "ACCOUNT.NUMBER",
        "transferTransactionInformation": {
            "instructedAmount": {"amount": f"{flt(doc.amount):.2f}", "currencyCode": doc.currency or "KES"},
            "counterpartyAccount": {
                "identification": {
                    "recipientMobileNo": doc.beneficiary_mobile_number or "",
                    "recipientBankAcctNo": doc.beneficiary_account_number,
                    "recipientBankCode": doc.bank_code,
                }
            },
            "counterparty": {
                "name": doc.beneficiary_name,
                "postalAddress": {"addressLine": "KENYA", "postCode": "00100", "town": "Nairobi", "country": "KE"},
            },
            "remittanceInformation": {"type": "UNSTRUCTURED", "content": narrative},
            "endToEndIdentification": "E2E" + reference,
        },
    })
    return payload


def _party_address(doc):
    """Primary Address of the paid party, via the Payment Entry; {} when there is none."""
    from frappe.contacts.doctype.address.address import get_default_address

    if doc.reference_doctype != "Payment Entry" or not doc.reference_name:
        return {}
    party_type, party = frappe.db.get_value("Payment Entry", doc.reference_name, ["party_type", "party"])
    name = party and get_default_address(party_type, party)
    return frappe.db.get_value(
        "Address", name, ["address_line1", "address_line2", "city", "pincode"], as_dict=True
    ) if name else {}


def _build_stanbic_swift_payload(doc, source, reference):
    narrative, payload = _stanbic_common(doc, reference, "SWIFT Payment")
    if not doc.bank_bic:
        frappe.throw(_("The beneficiary's Bank needs a SWIFT number for SWIFT payments."))
    if not doc.beneficiary_address:
        frappe.throw(_("SWIFT payments need the beneficiary's address: add a primary Address to the supplier."))
    mobile = _stanbic_mobile(source.get("custom_sender_mobile_number"))
    if not mobile:
        frappe.throw(_("Set Sender Mobile Number on Bank Account {0} for SWIFT payments.").format(source.name))
    address = _party_address(doc)
    payload.update({
        "originatorAccount": {
            "identification": {
                "identification": source.bank_account_no,
                "debitCurrency": doc.currency or "KES",
                "mobileNumber": mobile,
            }
        },
        "transferTransactionInformation": {
            "instructedAmount": {
                "amount": f"{flt(doc.amount):.2f}",
                "creditCurrency": doc.destination_currency or doc.currency or "KES",
            },
            "counterpartyAccount": {
                "identification": {
                    "identification": doc.beneficiary_account_number,
                    # Stanbic's sample formats the beneficiary bank as "SW-" + BIC.
                    "beneficiaryBank": "SW-" + doc.bank_bic,
                }
            },
            "counterparty": {
                "name": doc.beneficiary_name,
                # Street in addressLine, town and postCode separately, as in Stanbic's SWIFT spec.
                "postalAddress": {
                    "addressLine": ", ".join(filter(None, [address.get("address_line1"), address.get("address_line2")]))
                    or ", ".join(doc.beneficiary_address.splitlines()),
                    "postCode": address.get("pincode") or "",
                    "town": address.get("city") or "",
                    "country": doc.beneficiary_country or "KE",
                },
            },
            "remittanceInformation": {"type": "UNSTRUCTURED", "content": narrative},
            "endToEndIdentification": "E2E" + reference,
        },
    })
    return payload


def _build_stanbic_mobile_payload(doc, source, reference):
    if not doc.beneficiary_mobile_number:
        frappe.throw(_("A beneficiary mobile number is required for Mobile Wallet payments."))

    narrative, payload = _stanbic_common(doc, reference, "Mobile Payment")
    payload.update({
        "originatorAccount": {"identification": {"mobileNumber": _stanbic_mobile(source.get("custom_sender_mobile_number"))}},
        "transferTransactionInformation": {
            "instructedAmount": {"amount": f"{flt(doc.amount):.2f}", "currencyCode": doc.currency or "KES"},
            "mobileMoneyMno": {"name": doc.wallet_name or "MPESA"},
            "counterparty": {"name": doc.beneficiary_name, "mobileNumber": doc.beneficiary_mobile_number},
            "remittanceInformation": {"type": "UNSTRUCTURED", "content": narrative},
            "endToEndIdentification": "E2E" + reference,
        },
    })
    return payload


def _build_stanbic_internal_transfer_payload(
    doc,
    source,
    reference,
):
    """Stanbic-to-Stanbic payment (stanbic-payments API). The debit account comes from the API key."""
    narrative = " ".join((doc.description or "Internal Transfer").split())[:35]

    return {
        "originatorAccount": {
            "identification": {
                "mobileNumber": _stanbic_mobile(source.get("custom_sender_mobile_number") or doc.sender_mobile_number),
            }
        },
        "requestedExecutionDate": frappe.utils.today(),
        "dbsReferenceId": reference,
        "txnNarrative": narrative,
        "callBackUrl": frappe.utils.get_url(
            "/api/method/banking_integration.api.callbacks.stanbic.handle_payment_notification"
        ),
        "transferTransactionInformation": {
            "instructedAmount": {
                "amount": f"{flt(doc.amount):.2f}",
                "currencyCode": doc.currency or "KES",
            },
            "counterparty": {"name": doc.beneficiary_name},
            "counterpartyAccount": {
                "identification": {"identification": doc.beneficiary_account_number},
            },
            "remittanceInformation": {"type": "UNSTRUCTURED", "content": narrative},
            # Kept distinct from dbsReferenceId, as T24 requires for RTGS.
            "endToEndIdentification": "E2E" + reference,
        },
    }


def _build_stanbic_rtgs_payload(
    doc,
    source,
    reference,
):
    """
    Build the Stanbic RTGS payment payload.
    """

    # T24 rejects payment details over ~35 chars / with newlines
    # ("TOO MANY CHARACTERS", error 999), so collapse whitespace and cap.
    narrative = " ".join(
        (doc.description or "RTGS Payment").split()
    )[:35]

    # T24 blanks DEBIT.THEIR.REF if the originator mobile is empty and
    # rejects with "INPUT MISSING", so stop before sending.
    mobile = _stanbic_mobile(source.get("custom_sender_mobile_number") or doc.sender_mobile_number)
    if not mobile:
        frappe.throw(_("Set Sender Mobile Number on Bank Account {0} for RTGS payments.").format(source.name))

    return {
        "originatorAccount": {
            "identification": {
                "identification": source.bank_account_no,
                "debitCurrency": doc.currency or "KES",
                "mobileNumber": mobile,
            }
        },

        "requestedExecutionDate": (
            frappe.utils.today()
        ),

        "dbsReferenceId": reference,

        "txnNarrative": narrative,

        "callBackUrl": frappe.utils.get_url(
            "/api/method/banking_integration.api.callbacks.stanbic.handle_payment_notification"
        ),

        "transferTransactionInformation": {
            "instructedAmount": {
                "amount": str(doc.amount),
                "creditCurrency": (
                    doc.currency or "KES"
                ),
            },

            "counterpartyAccount": {
                "identification": {
                    "identification": (
                        doc.beneficiary_account_number
                    ),
                    "beneficiaryBank": (
                        doc.bank_code or ""
                    ),
                    "beneficiaryChargeType": "SHA",
                }
            },

            "counterparty": {
                "name": doc.beneficiary_name,

                "postalAddress": {
                    "addressLine": (
                        doc.beneficiary_address
                        or "KENYA"
                    ),
                    "postCode": "00100",
                    "town": "Nairobi",
                    "country": (
                        doc.beneficiary_country
                        or "KE"
                    ),
                }
            },

            "remittanceInformation": {
                "type": "UNSTRUCTURED",
                "content": narrative,
            },

            # Must be DISTINCT from dbsReferenceId — if the two references
            # are identical, T24 blanks DEBIT.THEIR.REF ("INPUT MISSING").
            # Confirmed against a working payload where they differ.
            "endToEndIdentification": "E2E" + reference,
        },
    }

# =====================================================================
# KCB
# =====================================================================

def _send_via_kcb(doc):

    source = frappe.get_doc(
        "Bank Account",
        doc.source_account,
    )

    credentials = get_bank_account_credentials(
        source
    )

    client = KCBClient(
        credentials=credentials
    )

    reference = (
        doc.transaction_reference
        or generate_reference()
    )

    # KCB allows a maximum of 12 characters
    reference = reference[:12]

    doc.transaction_reference = reference

    payload = _build_kcb_funds_transfer_payload(
        doc=doc,
        source=source,
        reference=reference,
        credentials=credentials,
    )

    doc.request_json = frappe.as_json(
        payload,
        indent=2,
    )

    response = client.funds_transfer(
        payload
    )

    doc.response_json = frappe.as_json(
        response,
        indent=2,
    )

    header = response.get(
        "header",
        {},
    )

    doc.bank_reference = (
        header.get("retrievalRefNumber")
        or header.get("messageID")
    )

    doc.status = (
        "Successful"
        if header.get("statusCode") == "0"
        else "Failed"
    )

    doc.save(
        ignore_permissions=True
    )

    frappe.db.commit()

    return response



def _build_kcb_funds_transfer_payload(doc, source, reference, credentials):
    # KE0010001 is KCB's shared sandbox company code; production sets its own.
    company_code = credentials.get("company_code") or "KE0010001"
    return {
        "companyCode": company_code,
        "transactionType": "IF",
        "debitAccountNumber": source.bank_account_no, # Ensure this is the whitelisted one
        "creditAccountNumber": doc.beneficiary_account_number,
        "debitAmount": float(doc.amount),
        "paymentDetails": doc.description[:35], 
        "transactionReference": reference,
        "currency": doc.currency,
        "beneficiaryDetails": doc.beneficiary_name[:35], # Enforcing KCB's 35 char limit
        "beneficiaryBankCode": doc.bank_code,
    }
# =====================================================================
# NCBA
# =====================================================================


def _send_via_ncba(doc):
    source = frappe.get_doc("Bank Account", doc.source_account)
    client = NCBAClient(get_bank_account_credentials(source))

    # NCBA rejects non-alphanumeric TransactionIDs; generate_reference() is digits only.
    # A retried transfer reuses its reference so NCBA can reject the duplicate.
    doc.transaction_reference = doc.transaction_reference or generate_reference()

    builder, path = {
        "Internal Bank Transfer": (_build_ncba_ift_payload, NCBAClient.IFT_PATH),
        "EFT": (_build_ncba_eft_payload, NCBAClient.EFT_PATH),
        "Pesalink Bank": (_build_ncba_pesalink_payload, NCBAClient.PESALINK_PATH),
        "RTGS": (_build_ncba_rtgs_payload, NCBAClient.RTGS_PATH),
    }.get(doc.transfer_type, (None, None))

    if not builder:
        frappe.throw(_("NCBA transfer type {0} is not supported.").format(doc.transfer_type))

    payload = builder(doc, source)
    doc.request_json = frappe.as_json(payload, indent=2)

    response = client.post(path, payload)
    doc.response_json = frappe.as_json(response, indent=2)

    # "000" means NCBA accepted it. EFT settles T+1 and RTGS within ~3h, but
    # NCBA reports no later failure for them, so acceptance is final here.
    doc.bank_reference = response.get("txnReferenceNo") or doc.bank_reference
    doc.status = "Successful" if response.get("resultCode") == "000" else "Failed"

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return response


def _ncba_amount(doc):
    return f"{flt(doc.amount):.2f}"


def _ncba_sender_cif(source):
    # NCBA: "first 6 digits of the sender account number" unless the bank assigned another.
    return source.bank_account_no[:6]


def _ncba_bank_code(doc):
    if not doc.bank_code:
        frappe.throw(
            _("Beneficiary bank code is missing. Set Bank Code on the supplier's Bank Account.")
        )
    return doc.bank_code


def _build_ncba_ift_payload(doc, source):
    return {
        "country": "Kenya",
        "transactionID": doc.transaction_reference,
        "beneficiaryAccountName": doc.beneficiary_name,
        "debitAccountNumber": source.bank_account_no,
        "creditAccountNumber": doc.beneficiary_account_number,
        "currency": doc.currency,
        "amount": _ncba_amount(doc),
        "narration": doc.description,
    }


def _build_ncba_eft_payload(doc, source):
    return {
        "amount": _ncba_amount(doc),
        "beneficiaryAccountNumber": doc.beneficiary_account_number,
        # bank + branch code, e.g. 07000
        "beneficiaryBankBIC": _ncba_bank_code(doc),
        "beneficiaryName": doc.beneficiary_name,
        "currency": doc.currency,
        "debitAccountNumber": source.bank_account_no,
        "narration": doc.description,
        "senderCountry": "Kenya",
        "transactionID": doc.transaction_reference,
        "senderCIF": _ncba_sender_cif(source),
    }


def _build_ncba_pesalink_payload(doc, source):
    return {
        "beneficiaryAccountNumber": doc.beneficiary_account_number,
        # NCBA wants the 2-digit bank code prefixed with "00", e.g. Equity 68 -> 0068.
        "beneficiaryBankBIC": "00" + _ncba_bank_code(doc)[:2],
        "beneficiaryName": doc.beneficiary_name,
        "amount": _ncba_amount(doc),
        "currency": doc.currency,
        "narration": doc.description,
        "senderAccountNumber": source.bank_account_no,
        "senderCIF": _ncba_sender_cif(source),
        "senderCountry": "Kenya",
        "transactionID": doc.transaction_reference,
    }


def _build_ncba_rtgs_payload(doc, source):
    if not doc.bank_bic:
        frappe.throw(
            _("RTGS needs the beneficiary bank's SWIFT code. Set SWIFT Number on Bank {0}.").format(
                frappe.db.get_value("Bank Account", doc.destination_account, "bank") or ""
            )
        )

    if not doc.purpose_code:
        frappe.throw(_("RTGS needs a CBK Purpose of Payment code."))

    return {
        "beneficiaryAccountNumber": doc.beneficiary_account_number,
        "beneficiaryBankBIC": doc.bank_bic,
        "beneficiaryCountry": "KE",
        "beneficiaryName": doc.beneficiary_name,
        "beneficiaryAddress": doc.beneficiary_address or "Nairobi",
        "creditAmount": _ncba_amount(doc),
        "debitCurrency": doc.currency,
        "creditCurrency": doc.destination_currency or doc.currency,
        "narration": doc.description,
        "senderAccountNumber": source.bank_account_no,
        "senderCIF": _ncba_sender_cif(source),
        "senderCountry": "KE",
        "purposeCode": doc.purpose_code,
        "transactionID": doc.transaction_reference,
    }


# =====================================================================
# HELPERS
# =====================================================================


def _get_country_code(bank_account):
    """
    Return the country code configured on the Bank Account.
    """

    country_code = getattr(
        bank_account,
        "custom_country_code",
        None,
    )

    if not country_code:
        frappe.throw(
            _(
                "Bank Account {0} has no Country Code configured."
            ).format(
                bank_account.name
            )
        )

    return country_code