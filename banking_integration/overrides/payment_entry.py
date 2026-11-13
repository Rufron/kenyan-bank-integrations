from banking_integration.services.payment_entry import is_bank_transfer_flow


class PaymentEntryMixin:
    def validate_transaction_reference(self):
        # Reference No/Date come back from the bank once the Bank Transfer succeeds.
        if is_bank_transfer_flow(self):
            return

        super().validate_transaction_reference()
