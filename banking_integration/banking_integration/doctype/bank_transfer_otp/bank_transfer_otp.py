# Copyright (c) 2026, Nathan Wagacha and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class BankTransferOTP(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		attempts: DF.Int
		bank_transfer: DF.Link | None
		email: DF.Data | None
		expires_at: DF.Datetime | None
		otp_hash: DF.Password | None
		status: DF.Literal["Pending", "Verified", "Expired", "Failed"]
		user: DF.Link | None
		verified_at: DF.Datetime | None
	# end: auto-generated types

	pass
