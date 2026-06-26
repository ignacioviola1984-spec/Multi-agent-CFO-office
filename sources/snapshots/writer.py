"""
writer.py - Immutable source snapshots. This is the finance-grade part.

Before any data feeds the engine, every extraction is frozen on disk under
  snapshots/{realm_id}/{period}/{extract_timestamp}/
with three things:
  raw/        the exact QuickBooks responses, one JSON per call
  canonical/  the transformed canonical tables, one CSV per table
  manifest.json  record_counts, period, realm_id, extract_timestamp (UTC
                 ISO-8601), a sha256 of every raw and every canonical file, and
                 the validation_result (pass/fail + per-check detail)

Append-only: the timestamp in the path means a new extraction never overwrites a
prior one. The hashes give integrity and reproducibility (re-run -> same hash).
"""

import hashlib
import json
import os

from schema import CONTRACT_TABLES, EXTRA_TABLES
import csvio

ALL_COLUMNS = {**CONTRACT_TABLES, **EXTRA_TABLES}


def _ts_token(extract_timestamp):
    """Path-safe token from a UTC ISO-8601 timestamp (drop ':' and '-')."""
    return extract_timestamp.replace("-", "").replace(":", "").replace(".", "")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_snapshot(base_dir, realm_id, period, raw, canonical, validation_result, extract_timestamp,
                   source=None, extra=None):
    """Freeze one extraction. Returns the snapshot dir and the manifest dict.

    `realm_id` is the source identity in the snapshot path (a QuickBooks realm, an
    ERPNext site host, ...). `source` and `extra` (e.g. site_url, companies) are
    optional and only added to the manifest when provided, so existing callers
    are unaffected."""
    realm = str(realm_id) or "unknown-realm"
    snap_dir = os.path.join(base_dir, realm, period, _ts_token(extract_timestamp))
    raw_dir = os.path.join(snap_dir, "raw")
    can_dir = os.path.join(snap_dir, "canonical")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(can_dir, exist_ok=True)

    hashes = {"raw": {}, "canonical": {}}

    # raw responses, one JSON per call
    for name, payload in raw.items():
        p = os.path.join(raw_dir, name + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        hashes["raw"][name + ".json"] = _sha256_file(p)

    # canonical tables, one CSV per table
    record_counts = {}
    for name, rows in canonical.items():
        cols = ALL_COLUMNS.get(name)
        if cols is None:
            continue
        p = csvio.write_table(os.path.join(can_dir, name + ".csv"), cols, rows)
        hashes["canonical"][name + ".csv"] = _sha256_file(p)
        record_counts[name] = len(rows)

    manifest = {
        "realm_id": realm,
        "period": period,
        "extract_timestamp": extract_timestamp,
        "record_counts": record_counts,
        "hashes": hashes,
        "validation_result": validation_result,
    }
    if source:
        manifest["source"] = source
    if extra:
        manifest.update({k: v for k, v in extra.items() if k not in manifest})
    mpath = os.path.join(snap_dir, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return snap_dir, manifest
