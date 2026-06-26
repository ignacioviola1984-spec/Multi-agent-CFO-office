"""
adapter.py - Read-only client over the ERPNext (Frappe) REST API.

Hard rules baked in here (same posture as the QuickBooks adapter):
  - READ-ONLY in code. The only HTTP verb this client issues is GET:
      * /api/resource/<DocType>            list (paged) / read
      * /api/method/frappe.desk.query_report.run   financial-statement reports
    There is deliberately NO create/update/delete method. A Frappe API key can
    have write permission; the restriction is enforced HERE, by construction, on
    top of the read-only role you give the key (see README / auth.py).
  - PAGINATION ALWAYS. Frappe's list default returns ~20 rows and only the
    "name" field. We page with limit_start / limit_page_length and request
    explicit fields, so we get every record for the period.
  - filters (Frappe JSON) scope by period and company.
  - 429 / 5xx -> deterministic exponential backoff (honors Retry-After).

The HTTP transport is injectable (`transport=`) so tests run fully offline
against a recorded fixture and never touch the network or need a secret.
"""

import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from auth import Config, ERPNextAuthError


def _period_bounds(period):
    """(start_date, end_date) ISO strings for a YYYY-MM period."""
    y, m = (int(x) for x in period.split("-"))
    start = datetime.date(y, m, 1)
    end = datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0
PAGE_SIZE = 500            # Frappe limit_page_length per page


class ERPNextApiError(Exception):
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


class ERPNextAdapter:
    """Typed-ish read-only access to ERPNext DocTypes and financial reports."""

    def __init__(self, config=None, transport=None, sleep=time.sleep):
        self.config = (config or Config())
        self._transport = transport or _default_transport
        self._sleep = sleep

    # ----- the single GET path (read-only) --------------------------------
    def _get(self, path, params=None):
        cfg = self.config
        if not cfg.base_url:
            raise ERPNextAuthError("ERPNEXT_BASE_URL is not set")
        qs = f"?{urllib.parse.urlencode(params or {})}" if params else ""
        url = f"{cfg.base_url}{path}{qs}"
        for attempt in range(MAX_RETRIES):
            headers = dict(cfg.auth_header())
            headers["Accept"] = "application/json"
            status, resp_headers, body = self._transport("GET", url, headers)
            if status == 200:
                return body
            if status == 429 or 500 <= status < 600:
                self._sleep(self._backoff(attempt, resp_headers))
                continue
            raise ERPNextApiError(status, _error_message(body), body)
        raise ERPNextApiError(status, f"giving up after {MAX_RETRIES} attempts", body)

    def _backoff(self, attempt, headers):
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (TypeError, ValueError):
                pass
        return BACKOFF_BASE_SECONDS * (2 ** attempt)

    # ----- DocType list (paged) -------------------------------------------
    def list_doctype(self, doctype, fields=("*",), filters=None, order_by="modified asc"):
        """GET /api/resource/<DocType>, paging through every row.

        `fields` defaults to ["*"] (Frappe returns only "name" otherwise).
        `filters` is a Frappe filter list, e.g. [["company","=","X"],
        ["posting_date","between",["2026-05-01","2026-05-31"]]].
        """
        rows, start = [], 0
        while True:
            params = {
                "fields": json.dumps(list(fields)),
                "limit_start": start,
                "limit_page_length": PAGE_SIZE,
                "order_by": order_by,
            }
            if filters:
                params["filters"] = json.dumps(filters)
            page = (self._get(f"/api/resource/{urllib.parse.quote(doctype)}", params) or {}).get("data", [])
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            start += PAGE_SIZE

    # ----- Financial-statement reports ------------------------------------
    def run_report(self, report_name, filters):
        """GET frappe.desk.query_report.run for a financial statement.

        Returns the report's `message` dict (with `result` rows + `columns`).
        Used for Profit and Loss Statement, Balance Sheet, Trial Balance,
        General Ledger, Accounts Receivable.
        """
        params = {"report_name": report_name, "filters": json.dumps(filters)}
        body = self._get("/api/method/frappe.desk.query_report.run", params)
        return (body or {}).get("message", body) or {}

    # ----- period extraction (ALL Frappe DocType / report / field names live
    # here, on the ERPNext side of the canonical boundary) -----------------
    # period-scoped DocTypes -> the date field each is filtered on
    _TXN = {
        "Sales Invoice": "posting_date", "Purchase Invoice": "posting_date",
        "Payment Entry": "posting_date", "GL Entry": "posting_date",
        "Sales Order": "transaction_date", "Quotation": "transaction_date",
        "Opportunity": "transaction_date", "Dunning": "posting_date",
        "Payment Request": "transaction_date",
    }

    def _company_filter(self):
        companies = getattr(self.config, "companies", None)
        return [["company", "in", companies]] if companies else []

    def _period_list(self, doctype, period):
        filters = list(self._company_filter())
        date_field = self._TXN.get(doctype)
        if date_field and period:
            start, end = _period_bounds(period)
            filters.append([date_field, "between", [start, end]])
        return self.list_doctype(doctype, fields=["*"], filters=filters or None)

    def _safe_period_list(self, doctype, period):
        """Optional DocTypes (apps may be uninstalled): never fail the extraction."""
        try:
            return self._period_list(doctype, period)
        except Exception:
            return []

    def companies(self):
        rows = self.list_doctype(
            "Company", fields=["name", "company_name", "abbr", "default_currency", "country"])
        for c in rows:
            c.setdefault("company", c.get("company_name") or c.get("name"))
        return rows

    def financial_report(self, report_name, company, period):
        start, end = _period_bounds(period)
        filters = {"company": company, "from_date": start, "to_date": end,
                   "periodicity": "Monthly", "filter_based_on": "Date Range"}
        return (self.run_report(report_name, filters) or {}).get("result", [])

    def extract_raw(self, period):
        """Pull every read-only response needed for `period` into the raw shape the
        ERPNext mapper consumes. All Frappe object names are confined to this
        method so the canonical layer never sees them."""
        companies = self.companies()
        reports = {c["company"]: {
            "profit_and_loss": self.financial_report("Profit and Loss Statement", c["company"], period),
            "balance_sheet": self.financial_report("Balance Sheet", c["company"], period),
        } for c in companies}
        return {
            "companies": companies,
            "fx_rates": self.list_doctype("Currency Exchange", fields=["*"]),
            "accounts": self.list_doctype(
                "Account", fields=["name", "account_name", "root_type", "account_type", "company", "is_group"]),
            "reports": reports,
            "sales_invoices": self._period_list("Sales Invoice", period),
            "purchase_invoices": self._period_list("Purchase Invoice", period),
            "payment_entries": self._period_list("Payment Entry", period),
            "sales_orders": self._period_list("Sales Order", period),
            "quotations": self._period_list("Quotation", period),
            "opportunities": self._safe_period_list("Opportunity", period),
            "dunnings": self._safe_period_list("Dunning", period),
            "payment_requests": self._safe_period_list("Payment Request", period),
            "customers": self.list_doctype("Customer", fields=["*"]),
            "suppliers": self.list_doctype("Supplier", fields=["*"]),
            "gl_entries": self._period_list("GL Entry", period),
            "bank_accounts": self._safe_period_list("Bank Account", period),
        }


def _error_message(body):
    if isinstance(body, dict):
        for k in ("exception", "message", "_server_messages", "_raw"):
            if body.get(k):
                return str(body[k])[:300]
    return str(body)[:300]
