"""Offline/manual file verifier (no database writes, emails or provider API calls).

Example: python -m scripts.inspect_supplier_feed partkom /srv/app/media/my.csv
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path
from app.modules.supplier_feeds.parsers import read_feed, FeedFormatError


def inspect(supplier, filename):
    begin=time.monotonic()
    count=valid=lower_bound=0
    errors=Counter();warehouses=Counter()
    for line,row,error in read_feed(supplier,Path(filename)):
        count+=1
        if error:
            errors[error]+=1
        else:
            valid+=1
            warehouses[row.warehouse]+=1
            lower_bound+=row.stock_is_lower_bound
    return {'supplier':supplier,'source_file':Path(filename).name,
        'total_records':count,'valid_records':valid,'rejected_records':count-valid,
        'records_with_stock_lower_bound':lower_bound,
        'warehouses':len(warehouses),'largest_warehouses':warehouses.most_common(12),
        'errors':errors.most_common(12),'elapsed_seconds':round(time.monotonic()-begin,2)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('supplier',choices=['rossko','partkom'])
    parser.add_argument('file')
    args=parser.parse_args()
    try:print(json.dumps(inspect(args.supplier,args.file),ensure_ascii=False,indent=2))
    except (FeedFormatError,OSError) as exc:parser.error(str(exc))

if __name__=='__main__': main()
