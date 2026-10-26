"""Process-wide defense-in-depth for supplier account secrets in logs.

Prefer never logging request bodies or full URLs at all. API transport libraries
may emit diagnostic request URLs; this filter masks their logs even in debug.
"""
import logging
import re
from app.core.config import get_settings

_QUERY = re.compile(r'(?i)(\b(?:key1|key2|password|pass|token|login|secret_key|authorization)=)[^&\s]+')
_XML = re.compile(r'(?is)(<(?:key1|key2|password|pass|token|login|secret_key)>).*?(</(?:key1|key2|password|pass|token|login|secret_key)>)')


def redact(value: str) -> str:
    result = _QUERY.sub(r'\1***',str(value))
    result = _XML.sub(r'\1***\2',result)
    config=get_settings()
    for name in ('partkom_login','partkom_password','adeo_login','adeo_password',
                 'forum_login','forum_password','rossko_key1','rossko_key2',
                 'yookassa_shop_id','yookassa_secret_key'):
        secret=getattr(config,name,'')
        if secret and len(secret)>=4:
            result=result.replace(secret,'***')
    return result


class SecretRedactionFilter(logging.Filter):
    def filter(self,record):
        message=record.getMessage()
        cleaned=redact(message)
        if cleaned != message:
            record.msg=cleaned
            record.args=()
            # Exception tracebacks may include URLs/payloads with credentials.
            record.exc_info=None
            record.exc_text=None
        return True


def install_redaction():
    root=logging.getLogger()
    for logger in (root,logging.getLogger('uvicorn.error'),logging.getLogger('uvicorn.access'),
                   logging.getLogger('httpx'),logging.getLogger('httpcore')):
        for handler in logger.handlers:
            if not any(isinstance(x,SecretRedactionFilter) for x in handler.filters):
                handler.addFilter(SecretRedactionFilter())
    # No full supplier GET query string should appear in INFO-level HTTP logs.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
