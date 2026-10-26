"""Backwards-compatible alias for the clearly labelled DEMO-only catalog seed.

The former BOSCH mock is deliberately retired: it could be mistaken for a
real supplier quote and conflicted with genuine discovery of that SKU.
"""
from scripts.seed_demo_catalog import main
import asyncio

if __name__ == '__main__':
    asyncio.run(main())
