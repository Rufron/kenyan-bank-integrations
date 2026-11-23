# Copyright (c) 2026, Nathan Wagacha and contributors
# For license information, please see license.txt

import hmac
import secrets

import frappe
import pyotp
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit


_EQUITY_TYPES = {
    "Internal Bank Transfer",
    "EFT",
    "RTGS",
    "Pesalink Bank",
    "Pesalink Mobile",
    "SWIFT",
    "Mobile Wallet",
}
# Each of these has a payload builder in _send_via_stanbic.
_STANBIC_TYPES = {
    "EFT",
    "RTGS",
    "Internal Bank Transfer",
    "Pesalink Bank",
    "SWIFT",
    "Mobile Wallet",
}
_KCB_TYPES = set()  # TODO: fill once KCB transfer types are confirmed
# Pesalink Bank is built (_build_ncba_pesalink_payload) but disabled: NCBA's live API
# also requires `validationId` (from a Pesalink account-validation call whose endpoint
# path NCBA hasn't documented) and `BeneficiaryBankName`. Add it back once NCBA confirms.
_NCBA_TYPES = {
    "Internal Bank Transfer",
    "EFT",
    "RTGS",
}

# Keyed by every Bank name in use (the send_money dispatcher accepts the same aliases).
ALLOWED_TRANSFER_TYPES = {
    "Equity Bank Kenya": _EQUITY_TYPES,
    "Equity Bank": _EQUITY_TYPES,
    "Stanbic Bank Kenya": _STANBIC_TYPES,
    "Stanbic Bank": _STANBIC_TYPES,
    "KCB Bank Kenya": _KCB_TYPES,
    "Kenya Commercial Bank": _KCB_TYPES,
    "NCBA": _NCBA_TYPES,
    "NCBA Bank": _NCBA_TYPES,
    "NCBA Bank Kenya": _NCBA_TYPES,
}

ALLOWED_ORIGIN_DOCTYPES = {
    "Payment Entry",
    "Purchase Invoice",
}


class BankTransfer(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF

        amount: DF.Currency
        approved_by: DF.Link | None
        approved_on: DF.Datetime | None
        bank: DF.Link
        bank_bic: DF.Data | None
        bank_code: DF.Data | None
        bank_reference: DF.Data | None
        beneficiary_account_number: DF.Data | None
        beneficiary_address: DF.SmallText | None
        beneficiary_country: DF.Data | None
        beneficiary_document_number: DF.Data | None
        beneficiary_document_type: DF.Data | None
        beneficiary_email: DF.Data | None
        beneficiary_mobile_number: DF.Data | None
        beneficiary_name: DF.Data | None
        currency: DF.Data
        description: DF.SmallText
        destination_account: DF.Link | None
        destination_balance_after: DF.Currency
        destination_balance_before: DF.Currency
        destination_currency: DF.Literal["KES", "USD", "EUR", "GBP"]
        payment_entry: DF.Link | None
        purpose_code: DF.Data | None
        reference_doctype: DF.Link
        reference_name: DF.DynamicLink
        request_json: DF.Code | None
        response_json: DF.Code | None
        sender_address: DF.SmallText | None
        sender_country_code: DF.Data | None
        sender_document_number: DF.Data | None
        sender_document_type: DF.Data | None
        sender_email: DF.Data | None
        sender_mobile_number: DF.Data | None
        sender_name: DF.Data | None
        source_account: DF.Link
        source_balance_after: DF.Currency
        source_balance_before: DF.Currency
        status: DF.Literal["Draft", "Pending Approval", "Approved", "Processing", "Successful", "Failed"]
        transaction_reference: DF.Data | None
        transfer_type: DF.Literal["Internal Bank Transfer", "EFT", "RTGS", "Pesalink Bank", "Pesalink Mobile", "SWIFT", "Mobile Wallet"]
        wallet_name: DF.Data | None
    # end: auto-generated types
    def validate(self):
        self.validate_origin()
        self.validate_bank_transfer_type()
        self.validate_source_account()
        self.validate_amount()
        self.validate_swift_fields()

        if not self.currency and self.source_account:
            self.currency = (
                frappe.db.get_value(
                    "Bank Account",
                    self.source_account,
                    "account_currency",
                )
                or "KES"
            )
            
    def on_update(self):
        self.fill_payment_entry_reference()

    def fill_payment_entry_reference(self):
        """Stamp the bank's reference on the originating Payment Entry once the transfer succeeds."""
        payment_entry = self.payment_entry or (
            self.reference_name if self.reference_doctype == "Payment Entry" else None
        )
        reference_no = self.bank_reference or self.transaction_reference

        if self.status != "Successful" or not payment_entry or not reference_no:
            return

        # Never overwrite a reference someone already entered.
        if frappe.db.get_value("Payment Entry", payment_entry, "reference_no"):
            return

        frappe.db.set_value(
            "Payment Entry",
            payment_entry,
            {
                "reference_no": reference_no,
                "reference_date": frappe.utils.today(),
            },
        )
        # db.set_value skips realtime events; tell any open Payment Entry form to reload.
        frappe.get_doc("Payment Entry", payment_entry).notify_update()

    def validate_origin(self):
        if self.reference_doctype not in ALLOWED_ORIGIN_DOCTYPES:
            frappe.throw(
                _(
                    "Bank Transfer must originate from a Payment Entry "
                    "or Purchase Invoice."
                )
            )

        if self.is_new() and not frappe.flags.get("in_bank_transfer_factory"):
            frappe.throw(
                _(
                    "Bank Transfer records cannot be created directly. "
                    "Use the transfer action from the originating "
                    "Payment Entry or Purchase Invoice."
                )
            )

        if not self.reference_name:
            frappe.throw(
                _("A reference document is required.")
            )

        if not frappe.db.exists(
            self.reference_doctype,
            self.reference_name,
        ):
            frappe.throw(
                _(
                    "Reference document {0} {1} does not exist."
                ).format(
                    self.reference_doctype,
                    self.reference_name,
                )
            )

    def validate_bank_transfer_type(self):
        if not self.bank:
            frappe.throw(
                _("A Bank is required.")
            )

        allowed = ALLOWED_TRANSFER_TYPES.get(
            self.bank,
            set(),
        )

        if self.transfer_type not in allowed:
            frappe.throw(
                _(
                    "Transfer Type {0} is not supported by bank {1}."
                ).format(
                    frappe.bold(self.transfer_type),
                    frappe.bold(self.bank),
                )
            )

        # "Internal" means within the same bank; other banks need EFT/RTGS/Pesalink.
        if self.transfer_type == "Internal Bank Transfer" and self.destination_account:
            destination_bank = frappe.db.get_value("Bank Account", self.destination_account, "bank")
            if destination_bank != self.bank:
                frappe.throw(
                    _("Internal Bank Transfer only works to another {0} account, but {1} is at {2}. Use EFT or RTGS.").format(
                        self.bank, self.destination_account, destination_bank
                    )
                )

    def validate_source_account(self):
        if not self.source_account:
            frappe.throw(
                _("A source Bank Account is required.")
            )

        if not frappe.db.exists(
            "Bank Account",
            self.source_account,
        ):
            frappe.throw(
                _(
                    "Source Bank Account {0} does not exist."
                ).format(
                    self.source_account
                )
            )

    def validate_amount(self):
        if not self.amount or self.amount <= 0:
            frappe.throw(
                _("Amount must be greater than zero.")
            )

    def validate_swift_fields(self):

        if self.transfer_type != "SWIFT":
            return

        if not self.bank_bic:
            frappe.throw(_("Bank BIC / SWIFT Code is required for SWIFT transfers."))

        if not self.destination_currency:
            frappe.throw(_("Destination Currency is required for SWIFT transfers."))

def create_from_reference(
    reference_doctype: str,
    reference_name: str,
    **kwargs,
) -> "BankTransfer":
    """
    Create a Bank Transfer tracking record from an approved
    Payment Entry or Purchase Invoice.

    This is the only supported creation path for Bank Transfer.
    """

    if reference_doctype not in ALLOWED_ORIGIN_DOCTYPES:
        frappe.throw(
            _(
                "Unsupported origin document type: {0}"
            ).format(
                reference_doctype
            )
        )

    if not frappe.db.exists(
        reference_doctype,
        reference_name,
    ):
        frappe.throw(
            _(
                "Reference document {0} {1} does not exist."
            ).format(
                reference_doctype,
                reference_name,
            )
        )

    frappe.flags.in_bank_transfer_factory = True

    try:
        doc = frappe.new_doc("Bank Transfer")

        doc.reference_doctype = reference_doctype
        doc.reference_name = reference_name

        doc.update(kwargs)

        doc.insert(
            ignore_permissions=True
        )

    finally:
        frappe.flags.in_bank_transfer_factory = False

    return doc


APPROVER_ROLE = "Bank Transfer Approver"
MAX_OTP_FAILURES = 4
OTP_LOCKOUT_SECONDS = 600


def _only_approvers():
    if APPROVER_ROLE not in frappe.get_roles():
        frappe.throw(
            _("Only a {0} can approve and send bank transfers.").format(APPROVER_ROLE),
            frappe.PermissionError,
        )


# The approval secret is separate from Frappe's login 2FA secret and kept encrypted (in __Auth).
SECRET_FIELD = "bank_transfer_otp_secret"
SETUP_TTL = 600


def _linked_secret(user):
    from frappe.utils.password import get_decrypted_password

    return get_decrypted_password("User", user, SECRET_FIELD, raise_exception=False)


def _setup_not_linked():
    _only_approvers()
    user = frappe.session.user
    if _linked_secret(user):
        frappe.throw(_("Your authenticator is already set up. Ask a System Manager to reset it if you lost your phone."))
    return user


@frappe.whitelist()
def authenticator_is_set_up():
    _only_approvers()
    return bool(_linked_secret(frappe.session.user))


@frappe.whitelist()
@rate_limit(limit=5, seconds=SETUP_TTL)
def send_setup_email_code(password: str):
    """Setup step 1: password, then a one-time code goes to the approver's own inbox."""
    from frappe.utils.password import check_password

    user = _setup_not_linked()
    check_password(user, password)
    code = f"{secrets.randbelow(10**6):06d}"
    frappe.cache.set_value(f"bank_transfer_setup_email_code:{user}", code, expires_in_sec=SETUP_TTL)
    frappe.sendmail(
        recipients=[frappe.db.get_value("User", user, "email")],
        subject=_("Your bank transfer authenticator setup code"),
        message=_("Your code is <b>{0}</b>. It expires in 10 minutes. If you did not start this, tell a System Manager.").format(code),
        # Queued, not now=True: an SMTP failure after commit would 500 the request and hide the code prompt.
    )
    return True


@frappe.whitelist()
def start_authenticator_setup(email_code: str):
    """Setup step 2: the emailed code unlocks a QR with a brand-new secret, valid for 10 minutes."""
    from frappe.twofactor import get_qr_svg_code

    user = _setup_not_linked()
    key = f"bank_transfer_setup_email_code:{user}"
    expected = frappe.cache.get_value(key)
    # One guess per emailed code, so it can't be brute-forced.
    frappe.cache.delete_value(key)
    if not expected or not hmac.compare_digest(str(expected), (email_code or "").strip()):
        frappe.throw(_("Wrong or expired email code. Start again to get a new one."), frappe.PermissionError)

    # A fresh secret each time: a QR seen or scanned in an earlier attempt stops working.
    secret = pyotp.random_base32()
    frappe.cache.set_value(f"bank_transfer_setup_secret:{user}", secret, expires_in_sec=SETUP_TTL)
    issuer = frappe.db.get_single_value("System Settings", "otp_issuer_name") or "ERPNext"
    uri = pyotp.TOTP(secret).provisioning_uri(name=user, issuer_name=f"{issuer} Bank Transfers")
    return get_qr_svg_code(uri).decode()


@frappe.whitelist()
def finish_authenticator_setup(otp: str):
    """Setup step 3: the first correct code proves the phone scanned the QR; save the secret."""
    from frappe.utils.password import set_encrypted_password

    _only_approvers()
    user = frappe.session.user
    if _linked_secret(user):
        return True
    key = f"bank_transfer_setup_secret:{user}"
    secret = frappe.cache.get_value(key)
    if not secret:
        frappe.throw(_("The setup expired. Click Approve & Send to start again."))

    # Not single-use: the approver may type the same code straight into the approval.
    _check_code(user, otp, secret, single_use=False)
    set_encrypted_password("User", user, secret, SECRET_FIELD)
    frappe.cache.delete_value(key)
    _alert_authenticator_change(user, _("linked to a phone"))
    return True


def _alert_authenticator_change(user, what):
    """Tell the approver and System Managers, so an unexpected link or reset gets noticed."""
    from frappe.utils.user import get_system_managers

    message = _(
        "The bank transfer approval authenticator for {0} was {1} by {2} at {3}. "
        "If this was not expected, have a System Manager reset it immediately."
    ).format(user, what, frappe.session.user, frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M"))
    frappe.get_doc("User", user).add_comment("Info", message)
    recipients = {frappe.db.get_value("User", user, "email"), *get_system_managers(only_name=True)}
    frappe.sendmail(
        recipients=[r for r in recipients if r and r not in ("Administrator", "admin@example.com")],
        subject=_("Bank transfer authenticator {0}: {1}").format(what, user),
        message=message,
    )


def verify_approver_otp(user, otp):
    """Check a code from the approver's linked authenticator app."""
    secret = _linked_secret(user)
    if not secret:
        frappe.throw(_("Set up your authenticator app first: click Approve & Send and follow the steps."))

    _check_code(user, otp, secret)


def _check_code(user, otp, secret, single_use=True):
    fail_key = f"bank_transfer_otp_failures:{user}"
    failures = frappe.cache.get_value(fail_key) or 0
    if failures >= MAX_OTP_FAILURES:
        frappe.throw(_("Too many wrong codes. Try again in 10 minutes."), frappe.PermissionError)

    # A code stays valid for ~30-90s; refuse a second use so a seen code can't be replayed.
    otp = (otp or "").strip()
    used_key = f"bank_transfer_otp_used:{user}:{otp}"
    if not otp.isdigit() or frappe.cache.get_value(used_key) or not pyotp.TOTP(secret).verify(otp, valid_window=1):
        frappe.cache.set_value(fail_key, failures + 1, expires_in_sec=OTP_LOCKOUT_SECONDS)
        frappe.throw(_("Incorrect or already used verification code."), frappe.PermissionError)

    if single_use:
        frappe.cache.set_value(used_key, 1, expires_in_sec=120)
    frappe.cache.delete_value(fail_key)


@frappe.whitelist()
def approve_and_send(
    bank_transfer: str,
    otp: str,
    modified: str,
):
    """
    A Bank Transfer Approver (who may also have made the payment) confirms
    with an authenticator code, then it is sent.
    """

    user = frappe.session.user
    _only_approvers()

    doc = frappe.get_doc(
        "Bank Transfer",
        bank_transfer,
    )

    # Bind the approval to exactly what the approver saw (amount, beneficiary...).
    if str(doc.modified) != modified:
        frappe.throw(_("This transfer changed after you opened it. Reload and check it again."))

    verify_approver_otp(user, otp)

    # Failed is re-sendable: the retry reuses the same transaction_reference
    # (see send_money), so an idempotent bank won't double-pay. Processing /
    # Successful stay locked.
    if doc.status not in (
        "Draft",
        "Pending Approval",
        "Failed",
    ):
        frappe.throw(
            _(
                "Only Draft, Pending Approval or Failed "
                "transfers can be sent."
            )
        )

    doc.approved_by = frappe.session.user
    doc.approved_on = frappe.utils.now_datetime()
    doc.status = "Approved"

    doc.save(
        ignore_permissions=True
    )

    from banking_integration.services.transfers import send_money

    frappe.flags.bank_transfer_approved = doc.name
    try:
        return send_money(doc.name)
    finally:
        frappe.flags.bank_transfer_approved = None

@frappe.whitelist()
def reset_approver_authenticator(user: str):
    """System Manager clears an approver's linked phone; they re-link on their next Approve & Send."""
    from frappe.utils.password import remove_encrypted_password

    frappe.only_for("System Manager")
    remove_encrypted_password("User", user, SECRET_FIELD)
    frappe.cache.delete_value(f"bank_transfer_setup_secret:{user}")
    _alert_authenticator_change(user, _("reset"))
