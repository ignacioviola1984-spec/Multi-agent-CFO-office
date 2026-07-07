"""config package - process configuration and secrets management.

Imported as ``config.secrets`` (a package), never by putting ``config/`` on
sys.path. That matters: a bare ``config/`` on the path would let ``import
secrets`` resolve to this module instead of Python's standard-library ``secrets``
(used elsewhere for token ids and constant-time comparison). Keeping ``config`` a
package and importing ``from config import secrets as appsecrets`` keeps the
stdlib module reachable everywhere.
"""
