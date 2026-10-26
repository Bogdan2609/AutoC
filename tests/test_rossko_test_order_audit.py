"""No network calls: checks redaction and normalization for GetOrders read-only review."""
from pathlib import Path
import ast

SRC = Path(__file__).resolve().parents[1] / 'scripts' / 'rossko_test_order_audit.py'

def test_source_does_not_dispatch():
    tree = ast.parse(SRC.read_text())
    calls = [x for x in ast.walk(tree) if isinstance(x, ast.Call)]
    strings = [x.value for c in calls for x in c.args if isinstance(x, ast.Constant) and isinstance(x.value, str)]
    assert 'GetOrders' in strings
    assert 'GetCheckout' not in strings


def test_source_compiles_without_writing_bytecode():
    compile(SRC.read_text(), str(SRC), 'exec')

import importlib.util

def load_with_project_deps():
    spec = importlib.util.spec_from_file_location('rossko_readonly_orders_audit', SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_scrubs_personal_fields():
    mod = load_with_project_deps()
    response = {'OrdersResult': {'success': True, 'OrdersList': {'Order': [
        {'id': 5000, 'created_date': '22.10.2026 14:20:00',
         'contact': {'name': 'PRIVATE_NAME', 'phone': 'PRIVATE_PHONE'},
         'detail': {'delivery_address': 'PRIVATE_ADDRESS'},
         'parts': {'part': [{'brand':'TRIALLI', 'partnumber':'CF705',
                             'count':1, 'price':'492.27', 'supplier_secret':'SECRET'}]}},
        {'id': 5001, 'parts': {'part': {'brand':'OTHER','partnumber':'123',
                                       'count':1,'price':'2.00'}}}
    ]}}}
    status, candidates = mod.parse_recent_orders(response)
    assert status == 'ok' and len(candidates) == 1
    assert candidates[0]['order_id'] == 5000
    assert candidates[0]['matching_lines'][0]['within_price_cap'] is True
    assert candidates[0]['created_date'] == '22.10.2026'
    displayed = str(candidates)
    for token in ('PRIVATE_NAME','PRIVATE_PHONE','PRIVATE_ADDRESS','SECRET'):
        assert token not in displayed


def test_unknown_supplier_result_does_not_imply_absence_of_order():
    mod = load_with_project_deps()
    assert mod.parse_recent_orders({'OrdersResult': {'success': False}}) == ('supplier_lookup_failed', [])
    assert mod.parse_recent_orders({'OrdersResult': {'success': True, 'OrdersList': {}}}) == ('ok', [])
