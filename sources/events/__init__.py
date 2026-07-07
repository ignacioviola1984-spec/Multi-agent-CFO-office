"""sources/events/ - event-driven (webhook) ingestion behind the same canonical
layer as the batch connectors. finance_core never learns whether a period's data
arrived by pull (QuickBooks/ERPNext snapshot) or by push (webhook events).
"""
