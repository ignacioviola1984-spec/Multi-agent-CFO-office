"""
config/streamlit_secrets.py - Resolve the Anthropic API key for the Streamlit apps
WITHOUT crashing when there is no secrets.toml.

Any access to `st.secrets` -- even `"KEY" in st.secrets` -- raises
`StreamlitSecretNotFoundError` when no `.streamlit/secrets.toml` file exists (a
clean Docker image, Cloud Run). The replay/demo apps are designed to run without
secrets, so a total absence of secrets.toml is a VALID state, not an error.

Shared resolution order (one convention for every entry point):
  1. os.environ["ANTHROPIC_API_KEY"]  -- what config/secrets.py populates from .env.
  2. st.secrets["ANTHROPIC_API_KEY"]  -- guarded; a missing secrets.toml means
     "no key", never a traceback.

Returns the key (also mirrored into os.environ so downstream Anthropic clients
pick it up) or None. None means: run in replay mode, live-agent features disabled.
"""

import os

ANTHROPIC_ENV = "ANTHROPIC_API_KEY"


def ensure_anthropic_key():
    """Return the Anthropic API key, or None if unavailable. Never raises on a
    missing secrets.toml. On a hit via st.secrets, mirrors it into os.environ."""
    key = os.environ.get(ANTHROPIC_ENV)
    if key:
        return key
    try:
        import streamlit as st
        # Any read of st.secrets triggers the load; with no secrets.toml this
        # raises StreamlitSecretNotFoundError, which we treat as "no key".
        value = st.secrets[ANTHROPIC_ENV]
    except Exception:
        return None
    if value:
        os.environ[ANTHROPIC_ENV] = value
        return value
    return None


def has_anthropic_key():
    """True iff a usable Anthropic key is available (env or st.secrets)."""
    return ensure_anthropic_key() is not None
