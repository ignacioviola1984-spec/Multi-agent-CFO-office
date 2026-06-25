"""csvio.py - tiny CSV read/write for canonical tables (stdlib only)."""

import csv
import os


def read_table(path):
    """Read a CSV into a list of dicts (empty list if the file is missing)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_table(path, columns, rows):
    """Write rows (list of dicts) to a CSV with exactly `columns` as the header."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    return path
