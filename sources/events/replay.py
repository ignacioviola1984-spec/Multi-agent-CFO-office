"""
sources/events/replay.py - Rebuild canonical state from the event store.

Out-of-order and late events are a fact of webhook life. The canonical mapping is
deterministic regardless of arrival order (mappers.py), so canonical state is
always a pure function of the stored events. This command makes that explicit: it
reads the append-only event store, replays it through the source's mapper, writes
the canonical CSVs, and emits a manifest with a sha256 of every canonical file --
the same integrity/reproducibility pattern as sources/snapshots/writer.py.

Because the output depends only on the events (never on arrival order or
received-at time), replaying the same events in any order reproduces byte-identical
canonical files and hashes. That is what the tests assert.

    python sources/events/replay.py --source wallet --store <store.jsonl> --out <dir>
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CANONICAL = os.path.join(ROOT, "sources", "canonical")
for _p in (ROOT, CANONICAL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import csvio  # noqa: E402
from schema import ALL_TABLES  # noqa: E402
from sources.events.mappers import get_mapper  # noqa: E402
from sources.events.store import EventStore  # noqa: E402


def rebuild_canonical(store, source):
    """Return the canonical tables for `source` rebuilt from the event store."""
    return get_mapper(source).build_canonical(store.payloads(source))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_canonical(tables, out_dir, source="wallet"):
    """Write the mapped tables as canonical CSVs + a manifest with per-file sha256.
    Only tables present in `tables` are written; each uses its canonical columns.
    Returns (out_dir, manifest)."""
    can_dir = os.path.join(out_dir, "canonical")
    os.makedirs(can_dir, exist_ok=True)
    hashes = {}
    counts = {}
    for name in sorted(tables):
        cols = ALL_TABLES.get(name)
        if cols is None:
            continue
        p = csvio.write_table(os.path.join(can_dir, name + ".csv"), cols, tables[name])
        hashes[name + ".csv"] = _sha256_file(p)
        counts[name] = len(tables[name])
    manifest = {"source": source, "record_counts": counts, "hashes": hashes,
                "canonical_dir": can_dir}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return out_dir, manifest


def replay(store_path, source, out_dir):
    store = EventStore(store_path)
    tables = rebuild_canonical(store, source)
    return write_canonical(tables, out_dir, source=source)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebuild canonical state from the event store.")
    ap.add_argument("--source", default="wallet")
    ap.add_argument("--store", default=None, help="event store path (default: the module default)")
    ap.add_argument("--out", required=True, help="output directory for canonical CSVs + manifest")
    args = ap.parse_args(argv)
    store = EventStore(args.store) if args.store else EventStore()
    tables = rebuild_canonical(store, args.source)
    out_dir, manifest = write_canonical(tables, args.out, source=args.source)
    print(f"replayed {store.count(args.source)} '{args.source}' events -> {out_dir}")
    for fname, h in sorted(manifest["hashes"].items()):
        print(f"  {fname:20} sha256 {h[:16]}...  ({manifest['record_counts'].get(fname[:-4], 0)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
