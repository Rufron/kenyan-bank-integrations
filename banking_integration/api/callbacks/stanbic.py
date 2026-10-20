import frappe

from banking_integration.api.callbacks.kcb import (
    get_payload,
    json_response,
    log_callback,
)

# Status tokens Stanbic uses across its payment rails. Anything not listed
# (e.g. still pending at the bank) leaves the transfer untouched.
SUCCESS_STATUSES = {"SUCCESS", "SUCCESSFUL", "COMPLETED", "ACSC", "ACCC"}
FAILED_STATUSES = {"FAILED", "FAILURE", "REJECTED", "RJCT", "DECLINED"}


def update_bank_transfer(payload):
    """Update a Bank Transfer from a Stanbic payment callback."""

    # We send dbsReferenceId / endToEndIdentification on the outbound request;
    # Stanbic echoes one of them back.
    reference = (
        payload.get("dbsReferenceId")
        or payload.get("endToEndIdentification")
        or payload.get("reference")
    )

    if not reference:
        return

    name = frappe.db.exists(
        "Bank Transfer", {"transaction_reference": reference}
    ) or frappe.db.exists(
        "Bank Transfer", {"bank_reference": reference}
    )

    if not name:
        return

    doc = frappe.get_doc("Bank Transfer", name)

    status = (
        payload.get("status")
        or payload.get("transactionStatus")
        or payload.get("paymentStatus")
        or ""
    ).upper()

    if status in SUCCESS_STATUSES:
        doc.status = "Successful"
    elif status in FAILED_STATUSES:
        doc.status = "Failed"

    doc.bank_reference = (
        payload.get("bankReferenceId")
        or payload.get("dbsReferenceId")
        or doc.bank_reference
    )

    doc.response_json = frappe.as_json(payload, indent=2)

    doc.save(ignore_permissions=True)
    frappe.db.commit()


@frappe.whitelist(allow_guest=True)
def handle_payment_notification():
    """Stanbic payment status callback endpoint."""

    try:
        payload = get_payload()

        log_callback("Stanbic Payment", payload)
        update_bank_transfer(payload)

        return json_response({"status": "success"})

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Stanbic Payment Callback",
        )

        return json_response({"status": "error"}, status=500)
