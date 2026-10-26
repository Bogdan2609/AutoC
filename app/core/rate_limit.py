"""Distributed, fail-closed resource budgets for expensive public endpoints.

Uses server-observed peer IP, not a spoofable X-Forwarded-For header. Deploy behind
an ingress that sets a trusted client address, with independent edge limits.
"""
import hashlib
import time
from fastapi import HTTPException, Request
from app.core.config import get_settings

_LUA="""
local n=redis.call('INCR',KEYS[1])
if n==1 then redis.call('EXPIRE',KEYS[1],tonumber(ARGV[2])) end
if n>tonumber(ARGV[1]) then return 0 end
return 1
"""

async def enforce_budget(scope:str,identity:str,*,limit:int,period:int):
    settings=get_settings()
    if settings.app_env=='test':return
    from app.modules.offer_engine.cache import redis_client
    keypart=hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
    bucket=int(time.time()//period)
    key=f'apv2:rl:{scope}:{keypart}:{bucket}'
    try:
        client=await redis_client()
        if client is None:
            raise ConnectionError('Redis not configured')
        ok=await client.eval(_LUA,1,key,limit,period*2)
    except Exception as exc:
        raise HTTPException(503,'Rate limiting unavailable') from exc
    if ok!=1:
        raise HTTPException(429,'Request rate exceeded',headers={'Retry-After':str(period)})


def peer_ip(request:Request)->str:
    return request.client.host if request.client is not None else 'unknown'
