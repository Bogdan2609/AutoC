"""Explicit manual retention utility, never scheduled by an AutoC worker.

Safe defaults: dry-run only; apply requires an independent environment opt-in,
confirmation text, and a transaction. Use only after a PostgreSQL backup.
"""
import argparse
import asyncio
import json
import os

from app.core.db import get_session_factory
from app.modules.marketplace.snapshot_retention_v0421 import retention_batch

CONFIRM = 'DELETE_ONLY_UNREFERENCED_HISTORY'


async def run(args):
    if args.apply and (args.confirm != CONFIRM or
                       os.getenv('AUTOCODEX_ALLOW_RETENTION_DELETE', '') != CONFIRM):
        raise SystemExit('Refusing deletion: two explicit confirmations are required')
    if args.apply and os.getenv('APP_ENV', '').lower() in ('production', 'prod'):
        raise SystemExit('Production deletion must be reviewed in a separate maintenance release')
    async with get_session_factory()() as db:
        try:
            result = await retention_batch(db, days=args.days, limit=args.limit,
                                           apply=args.apply, approved=args.apply)
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
        except BaseException:
            await db.rollback()
            raise
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm', default='')
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
