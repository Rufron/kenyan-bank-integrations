// Reference No/Date are filled from the Bank Transfer after the bank confirms,
// so they are optional while the payment will go through the bank integration.
// Mirrors is_bank_transfer_flow in services/payment_entry.py.
const is_bank_transfer_flow = (frm) =>
    frm.__integration_enabled
    && ["Pay", "Internal Transfer"].includes(frm.doc.payment_type)
    && frm.doc.paid_from_account_type === "Bank"
    && !["Cash", "Cheque"].includes(frm.doc.mode_of_payment);

// Mirrors get_source_bank_account: Internal Transfer has no Company Bank Account
// field, so use the company Bank Account linked to the Paid From ledger.
// Resolves to {custom_integration_enabled, bank} or null.
function get_source_bank_account(frm) {
    const fields = ["custom_integration_enabled", "bank"];
    if (frm.doc.payment_type === "Internal Transfer") {
        if (!frm.doc.paid_from) return Promise.resolve(null);
        return frappe.db.get_value("Bank Account",
            { account: frm.doc.paid_from, company: frm.doc.company, is_company_account: 1, disabled: 0 },
            fields).then((r) => r.message && r.message.bank ? r.message : null);
    }
    if (!frm.doc.bank_account) return Promise.resolve(null);
    return frappe.db.get_value("Bank Account", frm.doc.bank_account, fields).then((r) => r.message || null);
}

function set_reference_mandatory(frm) {
    // mandatory_depends_on wins over ERPNext's toggle_reqd in Frappe's save check.
    ["reference_no", "reference_date"].forEach((fieldname) => {
        frm.set_df_property(fieldname, "mandatory_depends_on", (doc) =>
            (doc.paid_from_account_type === "Bank" || doc.paid_to_account_type === "Bank")
            && !is_bank_transfer_flow(frm)
        );
    });

    frm.__integration_enabled = false;

    get_source_bank_account(frm).then((source) => {
        frm.__integration_enabled = !!(source && source.custom_integration_enabled);
        frm.layout.refresh_dependency();
    });
}

frappe.ui.form.on("Payment Entry", {
    refresh(frm) {
        set_reference_mandatory(frm);
    },

    bank_account(frm) {
        set_reference_mandatory(frm);
    },

    paid_from(frm) {
        set_reference_mandatory(frm);
    },

    payment_type(frm) {
        set_reference_mandatory(frm);
    },

    before_submit(frm) {

        // Banking integration only applies to money leaving a company
        // bank account. Skip Cash / Receive and any non-bank payment.
        if (
            !["Pay", "Internal Transfer"].includes(frm.doc.payment_type)
            || frm.doc.paid_from_account_type !== "Bank"
            || ["Cash", "Cheque"].includes(frm.doc.mode_of_payment)
        ) {
            return;
        }

        // ...and only when that bank account has integration enabled, so
        // ordinary bank payments are untouched.
        return get_source_bank_account(frm).then((source) => {

            if (!source || !source.custom_integration_enabled) {
                return;
            }
            const r = { message: source };

            // NCBA requires a CBK Purpose of Payment code on every RTGS payment.
            const needs_purpose_code = (values) =>
                values.transfer_type === "RTGS"
                && (r.message.bank || "").toUpperCase().startsWith("NCBA");

            return new Promise((resolve, reject) => {

                const dialog = new frappe.ui.Dialog({
                    title: __("Bank Transfer Type"),
                    fields: [
                        {
                            fieldname: "transfer_type",
                            label: __("Transaction Type"),
                            fieldtype: "Select",
                            options: [
                                "EFT",
                                "RTGS",
                                "Pesalink Bank",
                                "Pesalink Mobile",
                                "SWIFT",
                                "Mobile Wallet",
                                "Internal Bank Transfer"
                            ],
                            reqd: 1
                        },
                        {
                            fieldname: "purpose_code",
                            label: __("Purpose of Payment (CBK)"),
                            fieldtype: "Autocomplete",
                            options: CBK_PURPOSE_CODES,
                            depends_on: (values) => needs_purpose_code(values),
                        },
                        {
                            fieldname: "mobile",
                            label: __("Recipient Mobile (2547XXXXXXXX)"),
                            fieldtype: "Data",
                            depends_on: (values) => values.transfer_type === "Mobile Wallet",
                        },
                        {
                            fieldname: "network",
                            label: __("Mobile Network"),
                            fieldtype: "Select",
                            options: ["MPESA", "AIRTEL MONEY", "T-KASH"],
                            default: "MPESA",
                            depends_on: (values) => values.transfer_type === "Mobile Wallet",
                        }
                    ],

                    primary_action_label: __("Continue"),

                    primary_action(values) {

                        if (!values.transfer_type) {
                            frappe.msgprint(
                                __("Please select a transaction type.")
                            );
                            return;
                        }

                        const purpose_code = (values.purpose_code || "").split(" - ")[0].trim();

                        if (needs_purpose_code(values) && !purpose_code) {
                            frappe.msgprint(
                                __("Please select a Purpose of Payment code for RTGS.")
                            );
                            return;
                        }

                        const is_mobile = values.transfer_type === "Mobile Wallet";
                        const mobile = (values.mobile || "").replace(/\D/g, "");

                        if (is_mobile && !/^254\d{9}$/.test(mobile)) {
                            frappe.msgprint(__("Enter the mobile number as 2547XXXXXXXX (12 digits)."));
                            return;
                        }

                        frm.set_value("custom_beneficiary_mobile", is_mobile ? mobile : "");
                        frm.set_value("custom_mobile_network", is_mobile ? values.network : "");

                        // Store the selection on the Payment Entry
                        frm.set_value(
                            "custom_bank_transfer_type",
                            values.transfer_type
                        );
                        frm.set_value(
                            "custom_purpose_code",
                            needs_purpose_code(values) ? purpose_code : ""
                        );

                        dialog.hide();

                        resolve();
                    }
                });

                dialog.show();

                // If the user closes the dialog without selecting
                dialog.$wrapper.on("hidden.bs.modal", function () {

                    if (!frm.doc.custom_bank_transfer_type) {
                        reject();
                    }

                });
            });
        });
    }
});

// CBK Purpose of Payment codes, from NCBA's "CBK Purpose Codes _Final 11102024.xlsx".
const CBK_PURPOSE_CODES = [
    "HLFD - Purchase of food and househld goods",
    "SAVG - Savings",
    "SCHO - School Fees",
    "LOAN - Loan",
    "RENT - Rent",
    "EDUC - Education expenses",
    "PRPY - Purchase of property",
    "BECH - ChildBenefit",
    "CLOT - Clothing",
    "SALA - SalaryPayment",
    "UBIL - Utilities",
    "TITH - Tithes and Offerings",
    "AIRB - Air transport",
    "BUSB - Bus",
    "CERE - Ceremonies",
    "CONS - Construction Activities",
    "CFR - CostAndFreight",
    "DIVD - Dividend Payments",
    "FARM - Farming",
    "FOEX - ForeignExchange",
    "FUEL - Fuel expenses",
    "HLTI - HealthInsurance",
    "INPC - InsurancePremiumCar",
    "INSU - InsurancePremium",
    "INTE - Interest",
    "IBLD - Interbank loan/deposit Repayments",
    "INVS - Investment And Securities",
    "LIFI - LifeInsurance",
    "PPTI - PropertyInsurance",
    "RELG - Religious activities",
    "HOLI - Holiday",
    "MACH - Machinery related",
    "MAFC - MedicalAidFundContribution",
    "MDCS - MedicalServices",
    "MERC - Manufactured goods and merchandise",
    "LICF - LicenseFee",
    "SHIP - Shipping",
    "SWLF - Sweeps/Liquidity funding",
    "TBIL - TelecommunicationsBill",
    "REFU - Refund",
    "RLWY - Railway",
    "VIPN - Vehicle Identification Plate Number",
    "CHC - Charitable Contributions (Charity and Aid)",
    "COMU - Community Development",
    "CORT - Trade Settlement Payment",
    "CSDK - Central Security Depository Payments",
    "GOKX - Government related Payments and Transfers",
    "GOVT - GovernmentPayment",
    "BSD - Banking Supervision Department",
    "RPS - COMESA PAYMENTS",
    "VOST - Vostro Payments",
    "PENA - Penalties",
    "FRAD - Fraudulent Origin",
    "3101 - Monthly Rental Income Tax",
    "3103 - Witholding Rental Income",
    "3100 - Income Tax - Resident Individual",
    "3200 - Income Tax - Withholding",
    "3304 - Motor Vehicle Advance Tax",
    "3514 - Value Added Tax (VAT)",
    "TAXR - TaxRefund",
    "2901 - Income Tax - PAYE",
    "3509 - VAT - Withholding",
    "4103 - Stamp Duty",
    "3001 - Income Tax - Company",
    "6101 - Sale of single Number Plate",
    "6102 - Sale of pair of Number Plates",
    "1908 - Customs Warehouse Rent",
    "COTX - CorporateTax Identification",
    "INTX - IncomeTax",
    "PAYE - Pay As You Earn",
    "RITX - Rental Income Tax",
    "SDTX - Stamp Duty",
    "WHLD - WithHolding",
    "VATX - ValueAddedTaxPayment",
    "TRAC - RemovedFromTracking",
    "MSC - Miscellenious",
    "512 - National Industrial Training Authority",
    "1001 - Import Duty - Oil",
    "1002 - Import Duty",
    "1101 - Excise Duty - Oils",
    "1102 - Excise Duty",
    "1201 - VAT Oils",
    "1202 - VAT Imports",
    "1206 - VAT Oils - 8%",
    "1501 - Alteration Fee",
    "1518 - Concession Fees",
    "1519 - Registration fees",
    "1527 - Transhipment Fee",
    "1801 - IDF Fees (2.0%)",
    "1802 - IDF/ PIF OIL",
    "2101 - Road Maintenance Levy (RML)",
    "2301 - Petroleum Regulatory Levy (PRL)",
    "2501 - Gross Payment-Petroleum Development Fund (PDF)",
    "3801 - Standards Levy",
    "4301 - State Department for Fisheries, Aquaculture and the Blue Economy",
    "4601 - Import Health Certificate",
    "4702 - Nuts and Oils Import Declaration Form",
    "6001 - Kenya Railway Development Levy (RDL)",
    "6002 - Kenya Railway Development Levy (RDL)-Oils",
    "6301 - Transfer Fees for Motor Vehicle Registration",
    "6401 - Merchant Shipping Superitendent Levy",
    "6402 - Merchant Shipping Superintendent (MSS) Levy - Oils",
    "6501 - Road Safety fund",
    "6601 - SHMV purchase tax",
    "ADTX - Advance Tax",
    "ARTX - Agency Revenue",
    "BTTX - Betting Tax",
    "CCMC - Cash Collateral Margin Calls",
    "CGTX - Capital Gains Tax (CGT)",
    "EDTX - Excise Duty",
    "ISTX - Installment Tax",
    "TAXS - TaxPayment",
    "TOTX - Turnover Tax",
    "PL39 - Licence Fees For Comm.Banks",
    "PL40 - Licence Fees Deposit Micro.Institutions",
    "PL41 - Licence Fees Forex Bureaus",
    "PL42 - Licence Fees Credit Reference Bureaus",
    "PL43 - Licence Fees Mortgage Financial Institutions",
    "PL44 - Applications Fees For Commercial Banks",
    "PL45 - Application Fees For Mort.Fin.Institutions",
    "PL46 - Applications Fees Depo.Tak.Institutions",
    "PL47 - Application Fees Forex Bureaus",
    "PL48 - Application Fees Credit.Ref. Bureaus",
    "PL49 - Penalties Commercial Banks",
    "PL50 - Penalties Mortg.Fin .Institutions",
    "PL51 - Penalties Deposit Taking Micro Institutions",
    "PL52 - Penalties Forex Bureaus",
    "PL53 - Penalties Credit Reference Bureaus",
    "PSCO - Professional service/commission earned",
];
