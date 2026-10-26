"""Audited admin taxonomy/rule CRUD. No publication endpoints or Product writes."""
from __future__ import annotations
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog_ext.models import Category
from .taxonomy_db import (TaxonomyGroup,TaxonomyType,TaxonomyPattern,TaxonomyAudit,
    TaxonomyRevision,check_pattern,bump_revision,KEY_RE,snapshot,utcnow)
from .classifier import classify_source, RULE_VERSION

router=APIRouter(prefix='/api/v1/admin/multi-enrichment/taxonomy',
                 tags=['classification-rulebook'],dependencies=[Depends(require_admin)])


def verify_code(value):
    if not KEY_RE.fullmatch(value):raise HTTPException(422,'Code must be lowercase ASCII identifier')
    return value


async def revision_guard(db,expected):
    row=await db.scalar(select(TaxonomyRevision).where(TaxonomyRevision.id==1).with_for_update())
    if not row:raise HTTPException(409,'Bootstrap classification taxonomy first')
    if row.revision!=expected:raise HTTPException(409,f'Stale taxonomy revision; actual={row.revision}')
    return row


class GroupCreate(BaseModel):
    code:str=Field(min_length=3,max_length=50)
    name:str=Field(min_length=2,max_length=160)
    parent_code:str|None=None
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=12,max_length=300)


class TypeCreate(BaseModel):
    code:str=Field(min_length=3,max_length=80)
    group_code:str
    category_name:str=Field(min_length=3,max_length=180)
    title_stem:str=Field(min_length=3,max_length=180)
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=12,max_length=300)


class TypeUpdate(BaseModel):
    active:bool
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=12,max_length=300)


class RuleCreate(BaseModel):
    key:str=Field(min_length=3,max_length=100)
    type_code:str
    positive_regex:str=Field(min_length=1,max_length=300)
    negative_regex:str|None=None
    priority:int=Field(default=150,ge=0,le=1000)
    supplier:Literal['any','rossko','partkom']='any'
    active:bool=True
    requires_review:bool=True
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=12,max_length=300)


class RuleUpdate(BaseModel):
    positive_regex:str|None=None
    negative_regex:str|None=None
    priority:int|None=Field(default=None,ge=0,le=1000)
    supplier:Literal['any','rossko','partkom']|None=None
    active:bool|None=None
    requires_review:bool|None=None
    expected_revision:int=Field(ge=1)
    reason:str=Field(min_length=12,max_length=300)


def serialize(row):
    return {c.key:getattr(row,c.key) for c in row.__table__.columns if c.key!='id'}


@router.get('/groups')
async def groups(db:AsyncSession=Depends(get_session)):
    return {'items':[serialize(x) for x in (await db.scalars(select(TaxonomyGroup).order_by(TaxonomyGroup.code))).all()]}


@router.get('/types')
async def types(db:AsyncSession=Depends(get_session)):
    return {'items':[serialize(x) for x in (await db.scalars(select(TaxonomyType).order_by(TaxonomyType.code))).all()]}


@router.get('/patterns')
async def patterns(type_code:str|None=None,limit:int=Query(default=300,ge=1,le=500),
                   offset:int=Query(default=0,ge=0),db:AsyncSession=Depends(get_session)):
    stmt=select(TaxonomyPattern)
    if type_code:stmt=stmt.where(TaxonomyPattern.type_code==type_code)
    records=(await db.scalars(stmt.order_by(TaxonomyPattern.priority.desc(),TaxonomyPattern.key)
                              .offset(offset).limit(limit))).all()
    return {'items':[serialize(r) for r in records]}


@router.get('/audit')
async def audit(limit:int=Query(default=50,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    records=(await db.scalars(select(TaxonomyAudit).order_by(TaxonomyAudit.revision.desc()).limit(limit))).all()
    return {'items':[serialize(r) for r in records]}


@router.post('/groups',status_code=201)
async def create_group(body:GroupCreate,db:AsyncSession=Depends(get_session)):
    verify_code(body.code)
    if body.parent_code:verify_code(body.parent_code)
    await revision_guard(db,body.expected_revision)
    if await db.get(TaxonomyGroup,body.code):raise HTTPException(409,'Group already exists')
    if body.parent_code and not await db.get(TaxonomyGroup,body.parent_code):raise HTTPException(422,'Unknown parent group')
    db.add(TaxonomyGroup(code=body.code,name=body.name,parent_code=body.parent_code))
    parent=(await db.scalar(select(Category).where(Category.slug=='auto-v030-group-'+body.parent_code))) if body.parent_code else None
    db.add(Category(name=body.name,slug='auto-v030-group-'+body.code,active=False,position=0,
                    parent_id=parent.id if parent else None))
    revision=await bump_revision(db,'group_create',body.code,after=body.reason)
    await db.commit()
    return {'code':body.code,'rule_revision':revision,'auto_publish':False}


@router.post('/types',status_code=201)
async def create_type(body:TypeCreate,db:AsyncSession=Depends(get_session)):
    verify_code(body.code)
    await revision_guard(db,body.expected_revision)
    if await db.get(TaxonomyType,body.code):raise HTTPException(409,'Type already exists')
    if not await db.get(TaxonomyGroup,body.group_code):raise HTTPException(422,'Unknown group')
    existing=(await db.scalars(select(Category).where(Category.name==body.category_name))).all()
    if existing:raise HTTPException(409,'Category name exists; use the already mapped type')
    group=(await db.scalar(select(Category).where(Category.slug=='auto-v030-group-'+body.group_code)))
    cat=Category(name=body.category_name,slug='auto-v030-'+body.code,active=False,
                 parent_id=group.id if group else None,position=0)
    db.add(cat)
    db.add(TaxonomyType(code=body.code,group_code=body.group_code,
                        category_name=body.category_name,title_stem=body.title_stem))
    revision=await bump_revision(db,'type_create',body.code,after=body.reason)
    await db.commit()
    return {'code':body.code,'rule_revision':revision,'category_active':False}


@router.patch('/types/{code}')
async def update_type(code:str,body:TypeUpdate,db:AsyncSession=Depends(get_session)):
    await revision_guard(db,body.expected_revision)
    obj=await db.scalar(select(TaxonomyType).where(TaxonomyType.code==code).with_for_update())
    if not obj:raise HTTPException(404,'Unknown type')
    was=obj.active
    if was==body.active:return {'code':code,'unchanged':True,'rule_revision':body.expected_revision}
    obj.active=body.active
    revision=await bump_revision(db,'type_activate' if body.active else 'type_deactivate',code,
                                 before=str(was),after=f'{body.active}: {body.reason}')
    await db.commit()
    return {'code':code,'rule_revision':revision,'active':body.active}


@router.post('/patterns',status_code=201)
async def create_pattern(body:RuleCreate,db:AsyncSession=Depends(get_session)):
    verify_code(body.key)
    for pattern in (body.positive_regex,body.negative_regex):
        if pattern is not None:
            try:check_pattern(pattern)
            except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    await revision_guard(db,body.expected_revision)
    if not await db.get(TaxonomyType,body.type_code):raise HTTPException(422,'Unknown classification type')
    if await db.scalar(select(TaxonomyPattern).where(TaxonomyPattern.key==body.key)):
        raise HTTPException(409,'Rule key exists')
    obj=TaxonomyPattern(**body.model_dump(exclude={'expected_revision','reason'}))
    db.add(obj)
    revision=await bump_revision(db,'pattern_create',body.key,after=json.dumps(
        {'rule':body.model_dump(exclude={'expected_revision'}),'reason':body.reason},ensure_ascii=False))
    await db.commit()
    return {'key':obj.key,'rule_revision':revision,'active':obj.active,'auto_publish':False}


@router.patch('/patterns/{key}')
async def update_pattern(key:str,body:RuleUpdate,db:AsyncSession=Depends(get_session)):
    await revision_guard(db,body.expected_revision)
    obj=await db.scalar(select(TaxonomyPattern).where(TaxonomyPattern.key==key).with_for_update())
    if not obj:raise HTTPException(404,'Unknown rule')
    before=serialize(obj)
    values=body.model_dump(exclude_unset=True,exclude={'expected_revision','reason'})
    if not values:raise HTTPException(422,'At least one setting required')
    if values.get('positive_regex') is None and 'positive_regex' in values:
        raise HTTPException(422,'Positive regex cannot be null')
    for name in ('positive_regex','negative_regex'):
        if name in values and values[name] is not None:
            try:check_pattern(values[name])
            except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    for field,value in values.items():setattr(obj,field,value)
    if serialize(obj)==before:return {'key':key,'unchanged':True,'rule_revision':body.expected_revision}
    obj.updated_at=utcnow()
    revision=await bump_revision(db,'pattern_update',key,
                before=json.dumps(before,ensure_ascii=False,default=str),
                after=json.dumps({'changes':values,'reason':body.reason},ensure_ascii=False))
    await db.commit()
    return {'key':key,'rule_revision':revision,'active':obj.active,'auto_publish':False}


class TestRule(BaseModel):
    description:str=Field(min_length=2,max_length=2000)
    supplier:Literal['rossko','partkom']='rossko'


@router.post('/test')
async def test_rule(body:TestRule,db:AsyncSession=Depends(get_session)):
    book=await snapshot(db)
    return {'result':classify_source(body.description,book,body.supplier),
            'rule_revision':book.revision,'rule_version':RULE_VERSION,'auto_publish':False}
