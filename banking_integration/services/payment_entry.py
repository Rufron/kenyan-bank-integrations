import frappe
from frappe import _

from banking_integration.banking_integration.doctype.bank_transfer.bank_transfer import (
    create_from_reference,
)


ALLOWED_TRANSFER_TYPES = {
    "EFT",
    "RTGS",
    "Pesalink Bank",
    "Pesalink Mobile",
    "SWIFT",
    "Mobile Wallet",
    "Internal Bank Transfer",
}


def is_bank_transfer_flow(doc):
    """True when submitting this Payment Entry will create a Bank Transfer."""

    # Physical instruments never trigger an electronic transfer, even when
    # drawn on an integration-enabled bank account.
    if doc.mode_of_payment in ("Cash", "Cheque"):
        return False

    # Only outbound bank payments become a Bank Transfer, and only when the
    # source company bank account has integration explicitly enabled. This
    # keeps all other payment entries (cash, receive, or bank accounts not
    # opted in) behaving exactly as before.
    if doc.payment_type not in ("Pay", "Internal Transfer") or doc.paid_from_account_type != "Bank":
        return False

    source = get_source_bank_account(doc)
    return bool(
        source
        and frappe.db.get_value("Bank Account", source, "custom_integration_enabled")
    )


def get_source_bank_account(doc):
    """Pay uses the Company Bank Account field; Internal Transfer has none, so match the Paid From ledger."""
    if doc.payment_type == "Internal Transfer":
        return _company_bank_account_for(doc.paid_from, doc.company)
    return doc.bank_account


def _company_bank_account_for(account, company):
    return frappe.db.get_value(
        "Bank Account",
        {"account": account, "company": company, "is_company_account": 1, "disabled": 0},
        "name",
    )


@frappe.whitelist()
def create_bank_transfer_from_payment_entry(doc, method=None):

    if isinstance(doc, str):
        doc = frappe.get_doc("Payment Entry", doc)

    if doc.docstatus != 1 or not is_bank_transfer_flow(doc):
        return

    # ---------------------------------------------------------
    # Prevent duplicate Bank Transfer
    # ---------------------------------------------------------

    existing = frappe.db.exists(
        "Bank Transfer",
        {
            "reference_doctype": "Payment Entry",
            "reference_name": doc.name,
        },
    )

    if existing:
        return existing

    # ---------------------------------------------------------
    # Validate source Bank Account
    # ---------------------------------------------------------

    source_bank_account = get_source_bank_account(doc)

    if not source_bank_account:
        frappe.throw(
            _("Company Bank Account is required.")
        )

    source_account = frappe.db.get_value(
        "Bank Account",
        {
            "name": source_bank_account,
            "company": doc.company,
        },
        "name",
    )

    if not source_account:
        frappe.throw(
            _("Bank Account {0} was not found.").format(
                source_bank_account
            )
        )

    if doc.payment_type == "Internal Transfer":
        destination_account = _company_bank_account_for(doc.paid_to, doc.company)
        if not destination_account:
            frappe.throw(
                _("No company Bank Account is linked to {0} (Account Paid To).").format(doc.paid_to)
            )
        beneficiary_name = doc.company
    elif get_transfer_type(doc) == "Mobile Wallet":
        # Mobile money goes to a phone number, so the party needs no bank account.
        if not doc.get("custom_beneficiary_mobile"):
            frappe.throw(_("Enter the recipient's mobile number for a Mobile Wallet payment."))
        destination_account = doc.party_bank_account
        beneficiary_name = doc.party_name or doc.party
    else:
        destination_account, beneficiary_name = _party_destination(doc)

    return _create_transfer(doc, source_account, destination_account, beneficiary_name)


def _party_destination(doc):
    # ---------------------------------------------------------
    # Validate party
    # ---------------------------------------------------------

    if not doc.party:
        frappe.throw(
            _("Party is required.")
        )

    # ---------------------------------------------------------
    # Find destination Bank Account
    # ---------------------------------------------------------

    # The account chosen on the Payment Entry wins; parties can hold several.
    destination_account = doc.party_bank_account or get_party_bank_account(
        party_type=doc.party_type,
        party=doc.party,
    )

    if not destination_account:
        frappe.throw(
            _(
                "No Bank Account found for {0} {1}."
            ).format(
                doc.party_type,
                doc.party,
            )
        )

    return destination_account, doc.party_name or doc.party


def _create_transfer(doc, source_account, destination_account, beneficiary_name):
    # ---------------------------------------------------------
    # Load Bank Accounts
    # ---------------------------------------------------------

    source = frappe.get_doc(
        "Bank Account",
        source_account,
    )

    destination = (
        frappe.get_doc("Bank Account", destination_account)
        if destination_account
        else frappe._dict()
    )

    # ---------------------------------------------------------
    # Provider
    # ---------------------------------------------------------

    bank = source.bank

    if not bank:
        frappe.throw(
            _("No Bank is configured for {0}.").format(
                source.name
            )
        )

    # ---------------------------------------------------------
    # Transfer type
    # ---------------------------------------------------------

    transfer_type = get_transfer_type(doc)

    # ---------------------------------------------------------
    # Description
    # ---------------------------------------------------------

    description = (
        doc.remarks
        or _("Payment to {0}").format(
            beneficiary_name
        )
    )

    # ---------------------------------------------------------
    # Create using the Bank Transfer's official mechanism
    # ---------------------------------------------------------

    transfer = create_from_reference(
        reference_doctype="Payment Entry",
        reference_name=doc.name,
        payment_entry=doc.name,

        bank=bank,

        source_account=source.name,
        destination_account=destination.get("name"),

        transfer_type=transfer_type,
        purpose_code=doc.get("custom_purpose_code"),

        amount=doc.paid_amount,
        currency=doc.paid_from_account_currency,

        description=description,

        sender_name=doc.company,
        sender_country_code="KE",

        beneficiary_name=beneficiary_name,
        beneficiary_country="KE",

        beneficiary_account_number=(
            destination.get("bank_account_no")
        ),

        bank_code=(
            destination.get("custom_bank_code")
        ),
        bank_bic=destination.get("bank") and frappe.db.get_value("Bank", destination.bank, "swift_number"),
        destination_currency=doc.paid_from_account_currency
        if doc.paid_from_account_currency in ("KES", "USD", "EUR", "GBP") else None,

        beneficiary_mobile_number=doc.get("custom_beneficiary_mobile"),
        wallet_name=doc.get("custom_mobile_network"),
    )

    return transfer.name


def get_party_bank_account(
    party_type,
    party,
):
    """
    Find the Bank Account belonging to the party.
    """

    return frappe.db.get_value(
        "Bank Account",
        {
            "party_type": party_type,
            "party": party,
        },
        "name",
    )


def get_bank(bank_account):
    return frappe.db.get_value(
        "Bank Account",
        bank_account.name,
        "bank",
    )

def get_transfer_type(payment_entry):

    transfer_type = payment_entry.get(
        "custom_bank_transfer_type"
    )

    if not transfer_type:
        frappe.throw(
            _(
                "Bank Transfer Type has not been selected."
            )
        )

    if transfer_type not in ALLOWED_TRANSFER_TYPES:
        frappe.throw(
            _(
                "Invalid Bank Transfer Type: {0}"
            ).format(
                transfer_type
            )
        )

    return transfer_type


def on_payment_entry_cancel(
    doc,
    method=None,
):
    """
    Prevent cancellation if money has already been sent.
    """

    linked_transfers = frappe.get_all(
        "Bank Transfer",
        filters={
            "reference_doctype": "Payment Entry",
            "reference_name": doc.name,
        },
        fields=[
            "name",
            "status",
        ],
    )

    for transfer in linked_transfers:

        if transfer.status in (
            "Processing",
            "Successful",
        ):

            frappe.throw(
                _(
                    "Cannot cancel Payment Entry {0}. "
                    "Bank Transfer {1} is currently {2}. "
                    "Funds may have already moved."
                ).format(
                    doc.name,
                    frappe.utils.get_link_to_form(
                        "Bank Transfer",
                        transfer.name,
                    ),
                    transfer.status,
                )
            )