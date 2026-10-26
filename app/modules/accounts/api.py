import hashlib,secrets
import anyio
from fastapi import Request
from app.core.rate_limit import enforce_budget,peer_ip
from datetime import timedelta,timezone
from argon2 import PasswordHasher,exceptions
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,EmailStr,Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.accounts.models import User,Session,Address,utc
router=APIRouter(prefix='/api/v1/accounts',tags=['accounts'])
hasher=PasswordHasher(memory_cost=32768,time_cost=2,parallelism=1)
_ARGON_LIMITER=anyio.CapacityLimiter(4)
_DUMMY_HASH=hasher.hash('dummy-invalid-account-password')
def _check_hash(encoded,password):
 try:return hasher.verify(encoded,password)
 except (exceptions.VerificationError,exceptions.InvalidHashError):return False
class Register(BaseModel):
 email:EmailStr
 password:str=Field(min_length=12,max_length=128)
 display_name:str=Field(default='',max_length=160)
class Login(BaseModel):
 email:EmailStr
 password:str=Field(max_length=128)
class NewAddress(BaseModel):
 contact:str=Field(min_length=2,max_length=160)
 phone:str=Field(min_length=6,max_length=30)
 address:str=Field(min_length=8,max_length=400)
async def current_user(authorization:str|None=Header(default=None),db:AsyncSession=Depends(get_session))->User:
 if not authorization or not authorization.startswith('Bearer ') or len(authorization[7:])!=64:raise HTTPException(401,'Bearer authentication required')
 digest=hashlib.sha256(authorization[7:].encode()).hexdigest()
 token=await db.scalar(select(Session).where(Session.digest==digest,Session.revoked.is_(False)))
 if token is None or token.expires_at.replace(tzinfo=timezone.utc)<utc():raise HTTPException(401,'Session expired')
 user=await db.get(User,token.user_id)
 if user is None or not user.is_active:raise HTTPException(401,'User disabled')
 return user
@router.post('/register',status_code=201)
async def register(body:Register,request:Request,db:AsyncSession=Depends(get_session)):
 await enforce_budget('register_ip',peer_ip(request),limit=5,period=3600)
 encoded=await anyio.to_thread.run_sync(hasher.hash,body.password,limiter=_ARGON_LIMITER)
 user=User(email=str(body.email).casefold(),password_hash=encoded,display_name=body.display_name.strip());db.add(user)
 try:await db.commit()
 except IntegrityError as exc:
  await db.rollback();raise HTTPException(409,'Email already registered') from exc
 return {'id':str(user.id),'email':user.email}
@router.post('/login')
async def login(body:Login,request:Request,db:AsyncSession=Depends(get_session)):
 email=str(body.email).casefold()
 await enforce_budget('login_ip',peer_ip(request),limit=20,period=300)
 await enforce_budget('login_account',email,limit=8,period=300)
 user=await db.scalar(select(User).where(User.email==email))
 encoded=user.password_hash if user and user.is_active else _DUMMY_HASH
 valid=await anyio.to_thread.run_sync(_check_hash,encoded,body.password,limiter=_ARGON_LIMITER)
 valid=bool(valid and user and user.is_active)
 if not valid:raise HTTPException(401,'Invalid credentials')
 raw=secrets.token_hex(32);db.add(Session(user_id=user.id,digest=hashlib.sha256(raw.encode()).hexdigest(),expires_at=utc()+timedelta(days=14)))
 await db.commit();return {'access_token':raw,'token_type':'bearer'}
@router.get('/me')
async def me(user:User=Depends(current_user)):return {'id':str(user.id),'email':user.email,'display_name':user.display_name}
@router.post('/addresses',status_code=201)
async def add_address(body:NewAddress,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 a=Address(user_id=user.id,**body.model_dump());db.add(a);await db.commit();return {'id':str(a.id)}
@router.get('/addresses')
async def addresses(user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 items=(await db.scalars(select(Address).where(Address.user_id==user.id))).all()
 return [{'id':str(a.id),'contact':a.contact,'phone':a.phone,'address':a.address} for a in items]
@router.post('/logout',status_code=204)
async def logout(authorization:str=Header(),user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 digest=hashlib.sha256(authorization[7:].encode()).hexdigest()
 token=await db.scalar(select(Session).where(Session.digest==digest))
 if token:token.revoked=True;await db.commit()
