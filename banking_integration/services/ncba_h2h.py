"""NCBA ConnectPlus host-to-host payment file (SDSC layout: one self-contained line per payment).

Spec: "Standard NCBA ConnectPlus H2H File Format Specs.xlsx". Encryption (NCBA's Java AES tool) and
SFTP upload are not done here yet: NCBA has not shared the tool or the SFTP details.
"""

import re

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from banking_integration.banking_integration.doctype.bank_transfer.bank_transfer import APPROVER_ROLE

# Characters NCBA rejects, plus hyphens (NCBA flagged them), the comma delimiter and line breaks.
_BAD_CHARS = re.compile(r"[%&;<>()\\,\r\n-]")
_NOT_SENT = ("Draft", "Pending Approval", "Approved", "Failed")

# Bank Transfer type -> (NCBA network type, how to get the bank/clearing code)
_DOMESTIC = {
    "EFT": ("EFT", lambda d: d.bank_code),
    "RTGS": ("RTGS", lambda d: d.bank_bic),
    "Pesalink Bank": ("PESALINK", lambda d: d.bank_code),
    "Internal Bank Transfer": ("INTERNAL", lambda d: "07000"),
    "Mobile Wallet": ("MMTS", lambda d: "99002"),  # M-PESA
}


def _clean(value, length):
    return " ".join(_BAD_CHARS.sub(" ", str(value or "")).split())[:length]


def _email(value):
    # Emails keep their hyphens (e.g. kaitet-group.com); only the delimiter and spaces go.
    return re.sub(r"[,\s]", "", value or "")[:40]


def _amount(value):
    return f"{flt(value, 2):.2f}"


def build_line(doc, debit_account_no, value_date):
    """One SDSC line for a Bank Transfer."""
    head = [debit_account_no[:6], debit_account_no, _amount(doc.amount), doc.currency, value_date, "A",
            _clean(doc.beneficiary_name, 35)]
    narrative = _clean(doc.description or doc.name, 35)
    address = [_clean(x, 35) for x in (doc.beneficiary_address or "").splitlines()[:4]]
    purpose = _clean(doc.purpose_code, 4) or "MSC"  # MSC = miscellaneous, as in NCBA's sample files

    if doc.transfer_type == "SWIFT":
        if not doc.bank_bic or not address:
            frappe.throw(_("{0}: international payments need the beneficiary bank SWIFT code and address.").format(doc.name))
        address += [""] * (4 - len(address))
        return head + [
            _clean(doc.beneficiary_account_number, 35), "S", doc.bank_bic, _email(doc.beneficiary_email),
            *address,
            "SHA",  # shortcut: charges always shared, add a field if Finance wants BEN/OUR.
            narrative, narrative, "", purpose,
        ]

    if doc.transfer_type not in _DOMESTIC:
        frappe.throw(_("{0}: {1} can't go in an NCBA H2H file.").format(doc.name, doc.transfer_type))
    network, code = _DOMESTIC[doc.transfer_type]
    account = doc.beneficiary_account_number
    if network == "MMTS":
        if (doc.wallet_name or "MPESA").upper() != "MPESA":
            frappe.throw(_("{0}: NCBA H2H mobile payments only go to M-PESA.").format(doc.name))
        account = re.sub(r"\D", "", doc.beneficiary_mobile_number or "")
        account = "254" + account[1:] if account.startswith("0") else account
    if not account or not code(doc):
        frappe.throw(_("{0}: beneficiary account and bank code are required for {1}.").format(doc.name, network))
    # NCBA limits (spec "File Formats" sheet): EFT and MMTS are KES only, 50 to 999,999 / 250,000.
    limit = {"EFT": 999_999, "MMTS": 250_000}.get(network)
    if limit and (doc.currency != "KES" or not 50 <= flt(doc.amount) <= limit):
        frappe.throw(_("{0}: NCBA {1} payments must be in KES, between 50 and {2:,}. Use RTGS for more.").format(
            doc.name, network, limit))
    if network == "RTGS" and not doc.purpose_code:
        frappe.throw(_("{0}: RTGS needs a CBK Purpose of Payment code.").format(doc.name))

    # Address 1 is mandatory; domestic beneficiaries without one default to their country.
    address = (address or ["KENYA"]) + [""] * 2
    desc = _clean(doc.reference_name or doc.name, 35)
    return head + [
        _clean(account, 35), network, code(doc), _email(doc.beneficiary_email),
        desc, desc, desc, desc,
        narrative, narrative, purpose, "", *address[:3],
    ]


@frappe.whitelist()
def generate_h2h_file(bank_transfers):
    """Build an (unencrypted) SDSC file for the selected Bank Transfers; returns name and content."""
    frappe.only_for(APPROVER_ROLE)
    names = frappe.parse_json(bank_transfers)
    if not names:
        frappe.throw(_("Select at least one Bank Transfer."))

    docs = [frappe.get_doc("Bank Transfer", n) for n in names]
    sources = {d.source_account for d in docs}
    if len(sources) != 1:
        frappe.throw(_("All transfers in one file must be paid from the same NCBA account."))
    source = sources.pop()
    debit_account_no, bank = frappe.db.get_value("Bank Account", source, ["bank_account_no", "bank"])
    if not (bank or "").upper().startswith("NCBA"):
        frappe.throw(_("{0} is not an NCBA account.").format(source))

    for d in docs:
        if d.status not in _NOT_SENT:
            frappe.throw(_("{0} is already {1}; it can't be paid again.").format(d.name, d.status))

    value_date = getdate(nowdate()).strftime("%d%m%Y")
    lines = [",".join(build_line(d, debit_account_no, value_date)) for d in docs]

    seq_key = frappe.cache.make_key(f"ncba_h2h_seq:{nowdate()}")
    seq = frappe.cache.incr(seq_key)
    frappe.cache.expire(seq_key, 2 * 86400)
    # NCBA: S2S6249 = SDSC, then YYMMDD + 3-digit daily sequence + "_E" (confirmed by NCBA, 24 Nov 2026).
    filename = f"S2S6249{getdate(nowdate()).strftime('%y%m%d')}{seq:03d}_E.txt"
    return {"filename": filename, "content": "\r\n".join(lines) + "\r\n", "count": len(lines)}
