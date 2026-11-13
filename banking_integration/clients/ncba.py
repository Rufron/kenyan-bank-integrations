"""NCBA Open Banking API client.

Credential mapping on Bank Integration Credentials:
    api_key       -> APIM subscription key (Ocp-Apim-Subscription-Key header)
    client_id     -> userID
    client_secret -> password
    sandbox_url / production_url -> base URL up to and including /apigateway
"""

from datetime import datetime, timezone

import frappe
import requests
from frappe import _


class NCBAClient:
    TOKEN_PATH = "/api/v1/Auth/generate-token"
    ACCOUNT_DETAILS_PATH = "/api/v1/AccountDetails/accountdetails"
    STATEMENT_PATH = "/api/v1/AccountStatement/accountstatement"
    STATUS_PATH = "/api/v1/TransactionStatusQuery/transactionstatusquery"
    IFT_PATH = "/api/v1/IFTTransaction/ifttransaction"
    EFT_PATH = "/api/v1/EFTTransaction/efttransaction"
    PESALINK_PATH = "/api/v1/PesaLinkTransaction/pesaLinktransaction"
    RTGS_PATH = "/api/v1/RTGSPayment/RTGSPayment"

    def __init__(self, credentials):
        self.credentials = credentials

        self.base_url = (
            credentials.sandbox_url
            if credentials.environment == "Sandbox"
            else credentials.production_url
        )

        if not self.base_url:
            frappe.throw(_("NCBA {0} URL is not set on {1}.").format(
                credentials.environment, credentials.name
            ))

        self.base_url = self.base_url.rstrip("/")
        self.subscription_key = credentials.get_password("api_key")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def get_access_token(self, force_refresh=False):
        """Reuse the stored token until shortly before NCBA's tokenExpiry (~24h)."""
        now = frappe.utils.now_datetime()

        if (
            not force_refresh
            and self.credentials.access_token
            and self.credentials.token_expiry
            and frappe.utils.get_datetime(self.credentials.token_expiry) > now
        ):
            return self.credentials.access_token

        data = self._send(
            self.TOKEN_PATH,
            {
                "userID": self.credentials.client_id,
                "password": self.credentials.get_password("client_secret"),
            },
            token=None,
            timeout=60,
        )

        token = data.get("accessToken")

        if not token:
            frappe.throw(_("NCBA did not return an access token."))

        token_expiry = frappe.utils.add_to_date(
            now, seconds=self._seconds_left(data.get("tokenExpiry")) - 300, as_datetime=True
        )

        frappe.db.set_value(
            "Bank Integration Credentials",
            self.credentials.name,
            {"access_token": token, "token_expiry": token_expiry},
        )
        self.credentials.access_token = token
        self.credentials.token_expiry = token_expiry

        return token

    @staticmethod
    def _seconds_left(expiry):
        """Seconds until NCBA's ISO tokenExpiry (UTC); 1 hour if it can't be read."""
        try:
            # NCBA sends 7 fractional digits; Python accepts at most 6.
            stamp, offset = expiry[:26], expiry[-6:]
            expires = datetime.fromisoformat(stamp + offset)
            return max(int((expires - datetime.now(timezone.utc)).total_seconds()), 360)
        except (TypeError, ValueError):
            return 3600

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    def post(self, path, payload, timeout=60):
        """Authenticated POST; retries once with a fresh token on 401."""
        try:
            return self._send(path, payload, token=self.get_access_token(), timeout=timeout)
        except NCBAUnauthorized:
            return self._send(
                path, payload, token=self.get_access_token(force_refresh=True), timeout=timeout
            )

    def _send(self, path, payload, token, timeout):
        headers = {
            "Ocp-Apim-Subscription-Key": self.subscription_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.base_url}{path}"

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as e:
            frappe.log_error(title="NCBA API Request Error", message=f"URL: {url}\nError: {e}")
            frappe.throw(_("Could not reach NCBA: {0}").format(e))

        if response.status_code == 401 and token:
            raise NCBAUnauthorized

        try:
            data = response.json()
        except ValueError:
            data = {"raw_text": response.text}

        if response.status_code >= 400:
            frappe.log_error(
                title="NCBA API Error",
                message=f"URL: {url}\nStatus: {response.status_code}\nResponse: {response.text}",
            )
            frappe.throw(
                _("NCBA API error {0}: {1}").format(response.status_code, response.text[:300])
            )

        return data

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def account_details(self, account_number):
        return self.post(
            self.ACCOUNT_DETAILS_PATH, {"country": "Kenya", "accountNo": account_number}
        )

    def account_statement(self, account_number, from_date, to_date):
        """Dates as ddmmyyyy. NCBA is slow here (~45s for 10 days), so allow 110s."""
        return self.post(
            self.STATEMENT_PATH,
            {
                "country": "Kenya",
                "accountNo": account_number,
                "fromDate": from_date,
                "toDate": to_date,
            },
            timeout=110,
        )

    def transaction_status(self, transaction_id):
        return self.post(self.STATUS_PATH, {"country": "KE", "transactionId": transaction_id})


class NCBAUnauthorized(Exception):
    pass
