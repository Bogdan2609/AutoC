"""Credential references: account rows NEVER store secret material.

For named profiles, supply secrets via process environment (Docker env_file or
secrets manager). A named profile NEVER falls back to default credentials.
"""
import os
from types import SimpleNamespace
from app.core.config import get_settings

_FIELDS = {
    'rossko': ('rossko_key1','rossko_key2','rossko_delivery_id','rossko_address_id'),
    'adeo': ('adeo_login','adeo_password'),
    'partkom': ('partkom_login','partkom_password'),
    'forum-auto': ('forum_login','forum_password'),
}
_REQUIRED = {
    'rossko': ('rossko_key1','rossko_key2','rossko_delivery_id'),
    'adeo': ('adeo_login','adeo_password'),
    'partkom': ('partkom_login','partkom_password'),
    'forum-auto': ('forum_login','forum_password'),
}


def for_account(code, account, destination=None):
    if code not in _FIELDS:
        raise ValueError('Unsupported supplier')
    settings = get_settings()
    if account.credential_profile == 'default':
        values = {field:getattr(settings, field) for field in _FIELDS[code]}
    else:
        prefix = f'SUPPLIER_PROFILE_{account.credential_profile}_'
        values = {field:os.getenv(prefix+field.upper(), '') for field in _FIELDS[code]}
    # Address/delivery IDs have to be approved and configured for this mapping.
    if code=='rossko' and destination:
        if destination.external_delivery_id:
            values['rossko_delivery_id'] = destination.external_delivery_id
        if destination.external_address_id:
            values['rossko_address_id'] = destination.external_address_id
        values['rossko_requisite_id'] = getattr(destination, 'external_requisite_id', None)
    return SimpleNamespace(**values, supplier_api_enabled=settings.supplier_api_enabled)


def ready(code, account, destination=None):
    config = for_account(code,account,destination)
    return bool(all(getattr(config,key,'') for key in _REQUIRED[code]))
