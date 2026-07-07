"""
sources/events/connector.py - EventSourceConnector: the push side of the same
SourceConnector contract the batch (pull) connectors implement.

It reads the append-only event store, replays the events through the source's
mapper, and returns the canonical tables. Because it emits the identical canonical
shape (sources/canonical/schema.py), finance_core and the MCP surface never learn
that this period arrived by webhook instead of by a QuickBooks/ERPNext pull -- the
whole point of the canonical layer.

Only the tables a wallet source can speak to are populated (cash_bank, payments);
the rest of the canonical contract is emitted empty, exactly as QuickBooks leaves
the Order-to-Cash tables empty. The mapping is deterministic and order-independent
(mappers.py), so replay after out-of-order or late delivery is exact.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CANONICAL = os.path.join(ROOT, "sources", "canonical")
for _p in (ROOT, CANONICAL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from schema import CONTRACT_TABLES, EXTRA_TABLES  # noqa: E402
from connector import SourceConnector  # noqa: E402
from sources.events.mappers import get_mapper  # noqa: E402
from sources.events.store import EventStore  # noqa: E402


class EventSourceConnector(SourceConnector):
    """A webhook/event source rebuilt from the event store into canonical tables."""

    def __init__(self, store=None, source="wallet"):
        self.source = source
        self.name = f"events:{source}"
        self.store = store if store is not None else EventStore()
        self.mapper = get_mapper(source)

    def canonical_tables(self, period=None):
        tables = {name: [] for name in CONTRACT_TABLES}
        tables.update({name: [] for name in EXTRA_TABLES})
        mapped = self.mapper.build_canonical(self.store.payloads(self.source))
        tables.update(mapped)  # cash_bank, payments
        return tables
