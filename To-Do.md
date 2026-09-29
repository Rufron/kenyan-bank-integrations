# Banking Integration – Outstanding Tasks

## KCB and I&M Integrations

### KCB Bank

* [ ] Complete KCB callback integration and testing.
* [x] Provide KCB with the required callback endpoints.
* [x] Update the callback structure based on KCB's latest requirements.
* [ ] Confirm callback connectivity and end-to-end testing with KCB.

**Callback endpoints:**

* Funds Transfer Callback: `https://kaitetv16-staging.nbg.frappe.cloud/api/method/banking_integration.api.callbacks.kcb.funds_transfer_callback`
* IPN: `https://kaitetv16-staging.nbg.frappe.cloud/api/method/banking_integration.api.callbacks.kcb.ipn`

### I&M Bank

* [ ] Begin I&M Bank integration.
    * [ ] Confirm integration requirements and API specifications with Wycliffe.
    * [ ] Obtain required credentials and sandbox access.
    * [ ] Implement and test the required endpoints.

---

## Equity Integration

* [x] Equity account statements are functional in the sandbox environment.
* [x] Equity transfer functionality is functional in the sandbox environment.
* [ ] Complete end-to-end testing.
* [ ] Validate reconciliation of imported statements with ERPNext transactions.
* [ ] Confirm production readiness and production credentials.

---

## Stanbic Integration

* [x] Stanbic sandbox authentication and API connection implemented.
* [x] Stanbic account statement retrieval implemented.
* [x] Stanbic statements successfully pulled into ERPNext `Bank Transaction`.
* [x] Stanbic bank account mapped to the ERPNext Bank Account.
* [x] Bank transactions are available in the standard ERPNext Bank Reconciliation Tool.
* [ ] Complete reconciliation flow between imported Stanbic transactions and ERPNext accounting entries.
* [ ] Test matching of imported transactions against existing Payment Entries / Journal Entries.
* [ ] Test creation and reconciliation of ERPNext vouchers for transactions with no existing accounting entry.
* [ ] Complete end-to-end testing of Stanbic transfers.
* [ ] Validate production credentials and production connectivity.

---

## Remaining Integration Work

* [ ] Complete testing across all banking integrations.
* [ ] Validate error handling and callback failures.
* [ ] Confirm authentication and credential handling for production.
* [ ] Document setup, API endpoints, and operational procedures.
* [ ] Deploy and validate production integrations.
