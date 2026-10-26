"""Canonical deterministic request fingerprints. No PII is logged or returned."""
import hashlib
import json


def payload_hash(body):
    """Compatible with the initial v0.17 fingerprints stored in customer_orders."""
    data = json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()
