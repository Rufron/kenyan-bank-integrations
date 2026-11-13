from frappe import _


def get_data(data):
    """Show Bank Transfers created from this Payment Entry under Connections."""
    data.setdefault("fieldname", "payment_entry")
    # reference_name is set on every transfer, including ones created before `payment_entry` was filled.
    data.setdefault("non_standard_fieldnames", {})["Bank Transfer"] = "reference_name"
    data.setdefault("transactions", []).append({"label": _("Bank Integration"), "items": ["Bank Transfer"]})
    return data
