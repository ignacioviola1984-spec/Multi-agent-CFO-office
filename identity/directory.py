"""
identity/directory.py - The registered human identities for local dev / demo.

In production these come from the IdP (Auth0 / Entra ID / Cognito): the subject id
and display name are standard OIDC claims, and roles come from a groups/roles
claim. Offline, this small directory stands in for that -- it is the set of
"registered human owners" the maker-checker and payment gates authorize against,
and the source the demo/tests mint LocalDevIdentity tokens from.

Roles use the EXACT reviewer-role strings from cfo-office/review.py, so an
authenticated identity's role matches the role registered as owner for a function
with no translation layer. Payment-relevant roles (Controller, Treasurer, CFO) are
included so the write path has a proposer and a distinct approver.
"""

# subject id -> registered human (display name, email, roles held)
LOCAL_DIRECTORY = {
    "u-acct-mgr":   {"name": "Alba Nunez",   "email": "alba@example.com",   "roles": ["Accounting Manager"]},
    "u-treasurer":  {"name": "Tomas Rios",   "email": "tomas@example.com",  "roles": ["Treasurer"]},
    "u-ar-mgr":     {"name": "Carla Sosa",   "email": "carla@example.com",  "roles": ["Collections / AR Manager"]},
    "u-ap-mgr":     {"name": "Diego Paz",    "email": "diego@example.com",  "roles": ["AP Manager"]},
    "u-tax-mgr":    {"name": "Nadia Vega",   "email": "nadia@example.com",  "roles": ["Tax Manager"]},
    "u-reporting":  {"name": "Elena Cruz",   "email": "elena@example.com",  "roles": ["Technical Accounting / Reporting Manager"]},
    "u-fpa":        {"name": "Ivan Mora",    "email": "ivan@example.com",   "roles": ["FP&A Director"]},
    "u-vpfin":      {"name": "Sofia Leon",   "email": "sofia@example.com",  "roles": ["VP Finance / Head of Strategic Finance"]},
    "u-controls":   {"name": "Marcos Gil",   "email": "marcos@example.com", "roles": ["Internal Controls Manager"]},
    "u-audit":      {"name": "Lucia Fabbri", "email": "lucia@example.com",  "roles": ["Internal Audit Lead", "Auditor"]},
    "u-controller": {"name": "Bruno Diaz",   "email": "bruno@example.com",  "roles": ["Controller"]},
    "u-cfo":        {"name": "Ignacio Viola", "email": "ignacio@example.com", "roles": ["CFO"]},
}


def get(subject):
    return LOCAL_DIRECTORY.get(subject)


def subjects_with_role(role):
    return [s for s, p in LOCAL_DIRECTORY.items() if role in p["roles"]]
