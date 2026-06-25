"""
llm.py - The prose layer. The ONLY place the model writes text.

Two responsibilities:

  1. draft(): turn a deterministic facts payload into a short Argentine-Spanish
     (voseo) message. If NEXO_USE_LLM=1 and a key is present, Claude writes it;
     otherwise a deterministic template writes it. Either way the result passes
     through the grounding guard before it is returned.

  2. grounding guard (grounding_ok): rejects any text that references a NUMBER not
     present in the allowed payload. The agent declares exactly which figures the
     message may cite; if the model invents a figure (a different amount, an
     extra count, a year), the guard catches it and draft() falls back to the
     deterministic template - ungrounded prose is never emitted.

Numbers are deterministic; prose is the model's. The model never computes or
invents a number - this module is where that invariant is enforced.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths

try:
    from dotenv import load_dotenv
    load_dotenv(paths.ENV_PATH)
except Exception:
    pass

MODEL = "claude-sonnet-4-6"

# Shared style for every message (each agent adds its own specialization on top).
STYLE = (
    "Escribís en español rioplatense (Argentina), usando VOSEO (vos/tenés/podés), "
    "tono profesional y cálido, breve (2 a 4 frases). Sos el productor de seguros "
    "escribiéndole directamente al cliente. No inventes datos, números, fechas, "
    "montos ni nombres de pólizas: usá SOLO lo que se te da. No incluyas el correo, "
    "el teléfono ni el número de póliza en el texto. No uses guiones largos (rayas)."
)


def use_llm() -> bool:
    """True only when explicitly enabled AND a key is available. Default: offline
    (deterministic templates), so the pipeline runs free, fast and reproducible."""
    return os.environ.get("NEXO_USE_LLM") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


def _call(system, prompt, max_tokens):
    resp = _get_client().messages.create(
        model=MODEL, max_tokens=max_tokens,
        system=system + "\n\n" + STYLE,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# --------------------------------------------------------------------------
# Grounding guard (numeric).
# --------------------------------------------------------------------------

_NUM_TOKEN = re.compile(r"\d[\d.,]*")


def _norm_num(token: str) -> str:
    """Normalize a numeric token to a bare digit string (drop . and , separators
    and any trailing separator), so '50.000', '50,000' and '50000' all match."""
    return token.strip(".,").replace(".", "").replace(",", "")


def numbers_in(text: str):
    """Set of normalized digit-strings appearing in the text."""
    return {_norm_num(t) for t in _NUM_TOKEN.findall(text) if _norm_num(t)}


def allowed_forms(value):
    """Normalized digit-string forms a single allowed value may legitimately take."""
    forms = set()
    if isinstance(value, bool):
        return forms
    if isinstance(value, (int, float)):
        forms.add(_norm_num(str(int(round(value)))))     # integer form: 50000
        forms.add(_norm_num(("%g" % value)))             # compact form: 24.32 -> 2432
    else:
        for t in _NUM_TOKEN.findall(str(value)):          # any digits inside a string
            n = _norm_num(t)
            if n:
                forms.add(n)
    return {f for f in forms if f}


def build_allowed(values):
    """Union of the allowed digit-strings across every payload value."""
    allowed = set()
    for v in values:
        allowed |= allowed_forms(v)
    return allowed


def grounding_ok(text, allowed_values):
    """(ok, offending): ok is False if the text cites a number not in the allowed
    payload. `allowed_values` is the list of figures the message may reference."""
    allowed = build_allowed(allowed_values)
    offending = sorted(n for n in numbers_in(text) if n not in allowed)
    return (not offending), offending


# --------------------------------------------------------------------------
# draft(): the one call agents make.
# --------------------------------------------------------------------------

def draft(system, prompt, *, allowed_numbers, fallback, max_tokens=400):
    """Return a grounded Spanish message.

    allowed_numbers: the figures the message may cite (everything else is
        rejected by the guard).
    fallback: a deterministic template message (built from the same payload) used
        when offline OR when the model's text fails the grounding guard.
    """
    source = "template"
    text = fallback
    if use_llm():
        try:
            candidate = _call(system, prompt, max_tokens)
            ok, _offending = grounding_ok(candidate, allowed_numbers)
            if ok:
                text, source = candidate, "llm"
            else:
                source = "template_guarded"   # model invented a figure -> rejected
        except Exception:
            source = "template_error"          # API hiccup -> never breaks the run
    return {"text": text, "source": source}
