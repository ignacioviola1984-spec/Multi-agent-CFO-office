"""
adapter.py - Read-only client over the QuickBooks Online Accounting API.

Hard rules baked in here:
  - READ-ONLY in code. The only HTTP verb this client issues is GET (reports and
    the /query endpoint). There is deliberately NO create/update/delete method.
    The com.intuit.quickbooks.accounting scope DOES allow writes, so the
    restriction is enforced here, by construction, not by the scope.
  - minorversion=75 on every call (minor versions 1-74 are deprecated).
  - Rate limits: 500 requests/min per realm, ~10 concurrent. On HTTP 429
    (ThrottleExceeded) we back off exponentially and honor Retry-After.
  - On 401 we refresh the access token once and retry.

The HTTP transport is injectable (`transport=`) so tests run fully offline
against recorded fixtures and never touch the network or need a secret.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from oauth import Config, valid_access_token, QBOAuthError

MINOR_VERSION = "75"
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0
PAGE_SIZE = 1000          # QBO max MAXRESULTS per query page


class QBOApiError(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


def _default_transport(method, url, headers, timeout=30):
    """Minimal GET transport. Returns (status, headers, json_or_text)."""
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, dict(r.headers), _maybe_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, dict(e.headers or {}), _maybe_json(raw)


def _maybe_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw}


class QuickBooksAdapter:
    """Typed-ish read-only access to QBO reports and entities for one realm."""

    def __init__(self, config=None, token_provider=None, transport=None, sleep=time.sleep):
        self.config = config or Config()
        # token_provider() -> a valid bearer token; injectable for tests.
        self._token_provider = token_provider or (lambda: valid_access_token(self.config))
        self._transport = transport or _default_transport
        self._sleep = sleep

    # ----- the single GET path (read-only) --------------------------------
    def _get(self, path, params=None):
        if not self.config.realm_id:
            raise QBOAuthError("QBO_REALM_ID is not set")
        params = dict(params or {})
        params["minorversion"] = MINOR_VERSION
        url = (f"{self.config.api_base}/v3/company/{self.config.realm_id}/{path}"
               f"?{urllib.parse.urlencode(params)}")
        refreshed = False
        for attempt in range(MAX_RETRIES):
            headers = {"Authorization": f"Bearer {self._token_provider()}",
                       "Accept": "application/json"}
            status, resp_headers, body = self._transport("GET", url, headers)
            if status == 200:
                return body
            if status == 401 and not refreshed:
                # token may have expired mid-flight: force one refresh and retry.
                valid_access_token(self.config, now=int(time.time()) + 10**9)  # force refresh
                refreshed = True
                continue
            if status == 429:
                self._sleep(self._backoff(attempt, resp_headers))
                continue
            if 500 <= status < 600:
                self._sleep(self._backoff(attempt, resp_headers))
                continue
            raise QBOApiError(status, _error_message(body), body)
        raise QBOApiError(status, f"giving up after {MAX_RETRIES} attempts", body)

    def _backoff(self, attempt, headers):
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (TypeError, ValueError):
                pass
        # deterministic exponential backoff (no randomness, for reproducibility)
        return BACKOFF_BASE_SECONDS * (2 ** attempt)

    # ----- Reports --------------------------------------------------------
    def report(self, name, **params):
        """GET /reports/{name}. e.g. report('ProfitAndLoss', start_date=..., end_date=...)."""
        return self._get(f"reports/{name}", params)

    def profit_and_loss(self, start_date, end_date, accounting_method="Accrual"):
        return self.report("ProfitAndLoss", start_date=start_date, end_date=end_date,
                           accounting_method=accounting_method)

    def balance_sheet(self, as_of_date, accounting_method="Accrual"):
        return self.report("BalanceSheet", start_date=as_of_date, end_date=as_of_date,
                           accounting_method=accounting_method)

    def trial_balance(self, start_date, end_date, accounting_method="Accrual"):
        return self.report("TrialBalance", start_date=start_date, end_date=end_date,
                           accounting_method=accounting_method)

    def aged_receivables(self, as_of_date):
        return self.report("AgedReceivables", report_date=as_of_date)

    def aged_payables(self, as_of_date):
        return self.report("AgedPayables", report_date=as_of_date)

    def general_ledger(self, start_date, end_date, accounting_method="Accrual"):
        return self.report("GeneralLedger", start_date=start_date, end_date=end_date,
                           accounting_method=accounting_method)

    # ----- Entity queries (read-only SQL-like /query) ---------------------
    def query(self, statement):
        """GET /query?query=<statement>. Returns the QueryResponse dict."""
        body = self._get("query", {"query": statement})
        return body.get("QueryResponse", {})

    def query_all(self, entity, where=None, order_by="Id"):
        """Page through every row of an entity, returning a flat list."""
        rows, start = [], 1
        while True:
            clause = f" WHERE {where}" if where else ""
            stmt = (f"SELECT * FROM {entity}{clause} ORDER BY {order_by} "
                    f"STARTPOSITION {start} MAXRESULTS {PAGE_SIZE}")
            page = self.query(stmt).get(entity, [])
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            start += PAGE_SIZE

    def accounts(self):       return self.query_all("Account")
    def invoices(self):       return self.query_all("Invoice")
    def bills(self):          return self.query_all("Bill")
    def payments(self):       return self.query_all("Payment")
    def bill_payments(self):  return self.query_all("BillPayment")
    def customers(self):      return self.query_all("Customer")
    def vendors(self):        return self.query_all("Vendor")
    def journal_entries(self): return self.query_all("JournalEntry")


def _error_message(body):
    if isinstance(body, dict):
        fault = body.get("Fault") or body.get("fault")
        if isinstance(fault, dict):
            errs = fault.get("Error") or fault.get("error") or []
            if errs:
                e0 = errs[0]
                return e0.get("Message") or e0.get("message") or json.dumps(fault)
        if "_raw" in body:
            return str(body["_raw"])[:300]
    return str(body)[:300]
