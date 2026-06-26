"""
auth.py - ERPNext (Frappe) authentication for server-to-server access.

Frappe's API key + API secret model is much simpler than QuickBooks OAuth2:
there is no consent dance and no rotating refresh token. Every request just
carries a static header:

    Authorization: token <api_key>:<api_secret>

(Frappe also supports OAuth2, but for a headless server-to-server integration the
key:secret token is the correct, production-grade choice.)

Config comes entirely from the repo-root .env (never committed):
    ERPNEXT_BASE_URL    e.g. https://yoursite.frappe.cloud  or  http://localhost:8000
    ERPNEXT_API_KEY
    ERPNEXT_API_SECRET

The same base URL works against Frappe Cloud (free trial site,
https://<site>.frappe.cloud) or a self-hosted instance; the code is identical and
you switch by env only.

LEAST PRIVILEGE (do this on the ERPNext side, see sources/erpnext/README.md):
create a dedicated user, give it a role with READ permission only on the relevant
DocTypes (no create/write/delete), and generate THAT user's API key/secret. The
adapter is already read-only in code; the read-only role makes it read-only on
the server too. Defense in depth.
"""

import os


class ERPNextAuthError(Exception):
    """Auth / config problem (missing base URL or credentials)."""


class Config:
    """ERPNext connection config, entirely from environment (.env)."""

    def __init__(self):
        self.base_url = (os.environ.get("ERPNEXT_BASE_URL", "") or "").rstrip("/")
        self.api_key = os.environ.get("ERPNEXT_API_KEY", "")
        self.api_secret = os.environ.get("ERPNEXT_API_SECRET", "")
        # Optional: restrict to specific companies (comma-separated) and label the
        # snapshot. Both are optional; absent means "all companies the user sees".
        self.companies = [c.strip() for c in os.environ.get("ERPNEXT_COMPANIES", "").split(",") if c.strip()]
        self.site_label = os.environ.get("ERPNEXT_SITE_LABEL", "") or _host(self.base_url)

    def require(self):
        if not self.base_url:
            raise ERPNextAuthError("ERPNEXT_BASE_URL must be set in .env")
        if not self.api_key or not self.api_secret:
            raise ERPNextAuthError("ERPNEXT_API_KEY and ERPNEXT_API_SECRET must be set in .env")
        return self

    def auth_header(self):
        """The Frappe token header. Built fresh each call; never logged."""
        self.require()
        return {"Authorization": f"token {self.api_key}:{self.api_secret}"}


def _host(base_url):
    """A path/label-safe host token from a base URL (for the snapshot identity)."""
    h = (base_url or "").split("://")[-1].split("/")[0]
    return (h or "erpnext").replace(":", "_")
