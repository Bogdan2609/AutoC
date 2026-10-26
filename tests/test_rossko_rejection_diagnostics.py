"""Offline ROSSKO rejection classifications; no supplier API calls."""
import importlib.util
from pathlib import Path

_module = Path(__file__).resolve().parents[1] / 'scripts' / 'rossko_test_checkout_lab.py'
_spec = importlib.util.spec_from_file_location('rossko_test_checkout_lab_new', _module)
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def test_courier_minimum_russian_is_classified_without_raw_text():
    secret = 'клиент +79870000000'
    s, ids, reason = lab.normalize_checkout({'CheckoutResult': {'success': False,
        'message': 'Минимальная сумма заказа 3000 ₽; ' + secret}})
    assert s == 'supplier_rejected' and ids == []
    assert reason == 'supplier_rejected_courier_minimum'
    assert secret not in reason


def test_delivery_address_and_payment_classifications():
    assert lab.normalize_checkout({'success': False, 'message': 'Неверный адрес доставки'})[2] == 'supplier_rejected_delivery_or_address'
    assert lab.normalize_checkout({'success': False, 'message': 'Неверный способ оплаты'})[2] == 'supplier_rejected_payment'


def test_unsupported_or_missing_message_never_exposes_payload():
    assert lab.normalize_checkout({'success': False})[2] == 'supplier_rejected_message_missing'
    assert lab.normalize_checkout({'success': False, 'message': 'MY-SECRET-TOKEN'})[2] == 'supplier_rejected_unclassified'
    assert lab.normalize_checkout({'success': False, 'message': {'KEY1': 'SECRET'}})[2] == 'supplier_rejected_message_missing'


def test_ambiguous_response_is_manual_review_not_supplier_rejected():
    assert lab.normalize_checkout({})[0] == 'manual_review'
    assert lab.normalize_checkout({'success': 'maybe'})[0] == 'manual_review'
    assert lab.normalize_checkout({'success': True})[0] == 'manual_review'


def test_order_id_with_false_success_still_requires_manual_review():
    assert lab.normalize_checkout({'success': False, 'message': 'Минимальная сумма', 'OrderIDS': {'id': 88}})[0] == 'manual_review'
