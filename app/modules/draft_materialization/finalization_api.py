"""v0.31.2: read-only quality triage and individually authorized legacy category recovery.

No bulk edit, publication, creation, order or supplier calls. This endpoint may
add ONE primary category to ONE legacy DRAFT after a fresh, explicit operator
confirmation with an optimistic snapshot token. Unknown/contradictory parts fail
closed. The product title, fitment, offers and existing attributes never change.
"""
from __future__ import annotations
import hashlib
import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product, Brand, ProductChangeLog
from app.modules.catalog_ext.models import ProductCategory, Category
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal
from app.modules.multi_enrichment.taxonomy_db import snapshot, TaxonomyRevision
from app.modules.multi_enrichment.semantic_guard import detect_head, NON_AUTOMOTIVE, source_checks
from app.modules.supplier_feeds.models import FeedOffer, FeedBatch
from app.modules.draft_materialization.models import DraftPreflight

router = APIRouter(prefix='/api/v1/admin/draft-materialization/quality-guard',
                   tags=['catalog-quality-finalization'], dependencies=[Depends(require_admin)])

# Two codes name the same physical item. Compatibility is not permission to
# guess what isn't written in a supplier description.
HEAD_CANON = {'suspension_frame_bushing':'subframe_bushing'}


def evidence_token(product, categories, offers, revision):
    """Includes every source description/version used to justify the category."""
    payload = {'product_id':str(product.id), 'name':product.name,
               'status':product.status, 'noindex':product.seo_noindex,
               'brand_id':str(product.brand_id),'article':product.article,
               'article_norm':product.article_norm,
               'categories':sorted((str(c.category_id), c.is_primary) for c in categories),
               'revision':revision,
               'sources':sorted((str(fo.id),str(fo.batch_id), int(fo.source_line),
                                 fo.description, fo.supplier_sku,fo.brand_norm,fo.article_norm,
                                 b.supplier_code,b.status) for fo,b in offers)}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def evaluate_legacy(product, brand, categories, offers, book):
    reasons=[]; heads=[]; rows=[]
    if product.status!='draft' or not product.seo_noindex:
        reasons.append('not_unpublished_noindex_draft')
    if categories:
        reasons.append('existing_category_relation_requires_operator_review')
    if not offers: reasons.append('missing_feed_evidence')
    if any(b.status not in ('staged','published') for _,b in offers):
        reasons.append('feed_batch_not_staged_or_published')
    if any(fo.article_norm != product.article_norm for fo,_ in offers):
        reasons.append('feed_article_identity_disagreement')
    # Do not infer from brand/article. Every *distinct* description must independently
    # support the same physical part, even when sourced from a single supplier.
    descriptions=sorted({fo.description.strip() for fo,_ in offers})
    if len(descriptions)>20: reasons.append('too_many_description_variants')
    for description in descriptions:
        if NON_AUTOMOTIVE.search(description): reasons.append('non_automotive_item')
        head=detect_head(description)
        heads.append(HEAD_CANON.get(head.code,head.code) if head else None)
        if not head: reasons.append('unrecognized_physical_part')
        elif 'part_subsystem_unconfirmed' in source_checks(head.code,[description])['issues']:
            reasons.append('subsystem_unconfirmed')
    candidates={h for h in heads if h}
    if len(candidates)!=1:reasons.append('source_type_disagreement')
    suggested=next(iter(candidates)) if len(candidates)==1 else None
    if suggested and suggested not in book.types:
        reasons.append('inactive_or_unknown_taxonomy_type')
    # Never automatically adopt an existing Product's offer for another Product.
    if any(fo.product_id!=product.id for fo,_ in offers):
        reasons.append('feed_offer_identity_changed')
    if not book.revision:reasons.append('taxonomy_not_bootstrapped')
    for fo,b in offers[:30]:
        rows.append({'supplier':b.supplier_code,'description':fo.description,
                     'batch_id':str(fo.batch_id),'source_line':fo.source_line})
    return {'product_id':str(product.id),'name':product.name,'brand':brand.name,
            'article':product.article,'status':product.status,'seo_noindex':product.seo_noindex,
            'primary_categories':sum(bool(c.is_primary) for c in categories),
            'linked_offers':len(offers),'sources':rows,'heads':heads,
            'suggested_category_code':suggested if not reasons else None,
            'suggested_category_name':book.types[suggested][0] if not reasons and suggested in book.types else None,
            'reasons':sorted(set(reasons)), 'eligible_for_individual_review':not reasons,
            'rule_revision':book.revision,
            'evidence_token':evidence_token(product,categories,offers,book.revision)}


async def legacy_case(db,product_id,*,locked=False):
    q=select(Product).where(Product.id==product_id)
    if locked:q=q.with_for_update()
    product=await db.scalar(q)
    if not product:raise HTTPException(404,'Product not found')
    brand=await db.get(Brand,product.brand_id)
    cq=select(ProductCategory).where(ProductCategory.product_id==product_id)
    if locked:cq=cq.with_for_update()
    cats=(await db.scalars(cq)).all()
    # Join uses the indexed feed_offers.product_id field.
    oq=(select(FeedOffer,FeedBatch).join(FeedBatch,FeedBatch.id==FeedOffer.batch_id)
        .where(FeedOffer.product_id==product_id)
        .order_by(FeedBatch.supplier_code,FeedOffer.source_line))
    if locked:
        # Protect the evidence between optimistic-token validation and commit.
        # SQLite test façade ignores FOR SHARE; PostgreSQL locks source rows.
        oq=oq.with_for_update(read=True,of=(FeedOffer,FeedBatch))
    offers=(await db.execute(oq)).all()
    return product,brand,cats,offers


@router.get('/legacy/missing-primary')
async def legacy_missing_primary(limit:int=Query(default=50,ge=1,le=200),
                                 db:AsyncSession=Depends(get_session)):
    book=await snapshot(db)
    ids=(await db.scalars(select(Product.id).where(Product.status=='draft',
        ~select(ProductCategory.id).where(ProductCategory.product_id==Product.id,
            ProductCategory.is_primary==True).exists()).order_by(Product.id).limit(limit))).all()
    items=[]
    for pid in ids:
        product,brand,cats,offers=await legacy_case(db,pid)
        items.append(evaluate_legacy(product,brand,cats,offers,book))
    return {'scanned':len(items),'items':items,'rule_revision':book.revision,
            'note':'Only a human may assign one primary category to one legacy draft; no name or publication changes.'}


@router.get('/legacy/{product_id}/preview')
async def legacy_preview(product_id:UUID,db:AsyncSession=Depends(get_session)):
    book=await snapshot(db)
    p,b,c,o=await legacy_case(db,product_id)
    return evaluate_legacy(p,b,c,o,book)


class LegacyCategoryRequest(BaseModel):
    expected_name:str=Field(min_length=2,max_length=300)
    expected_revision:int=Field(ge=1)
    evidence_token:str=Field(pattern=r'^[0-9a-f]{64}$')
    category_code:str=Field(min_length=3,max_length=80)
    confirm_individual_manual:bool
    confirm_unpublished_draft:bool
    reason:str=Field(min_length=12,max_length=500)


@router.post('/legacy/{product_id}/assign-primary')
async def manual_legacy_category(product_id:UUID,body:LegacyCategoryRequest,
                                 db:AsyncSession=Depends(get_session)):
    if not body.confirm_individual_manual or not body.confirm_unpublished_draft:
        raise HTTPException(422,'Explicit manual per-product and draft-only confirmation required')
    try:
        rev=await db.scalar(select(TaxonomyRevision).where(TaxonomyRevision.id==1).with_for_update())
        if not rev or rev.revision!=body.expected_revision:
            raise HTTPException(409,'Rule revision changed; refresh preview')
        p,b,c,o=await legacy_case(db,product_id,locked=True)
        if p.name!=body.expected_name:
            raise HTTPException(409,'Product name changed; operator edits preserved')
        # Legacy only: never modify a materialized or existing published card.
        if await db.scalar(select(DraftPreflight.id).where(DraftPreflight.product_id==p.id)):
            raise HTTPException(409,'Preflight-managed product is not legacy category recovery')
        book=await snapshot(db)
        preview=evaluate_legacy(p,b,c,o,book)
        if preview['evidence_token']!=body.evidence_token:
            raise HTTPException(409,'Evidence or category relations changed; refresh preview')
        if not preview['eligible_for_individual_review'] or preview['suggested_category_code']!=body.category_code:
            raise HTTPException(409,'Source evidence does not independently support requested category')
        name=book.types[body.category_code][0]
        cat=await db.scalar(select(Category).where(Category.name==name))
        if not cat:raise HTTPException(409,'Verified catalog category not found; do not auto-create')
        # Can be inactive; do NOT activate or make publicly discoverable.
        db.add(ProductCategory(product_id=p.id,category_id=cat.id,is_primary=True))
        db.add(ProductChangeLog(product_id=p.id,action='legacy_category_manual_v0312',
            changes={'category_code':body.category_code,'category_id':str(cat.id),
                     'category_active':cat.active,'revision':book.revision,
                     'evidence_hash':body.evidence_token,'reason':body.reason,
                     'unchanged_fields':['name','slug','seo','attributes','offers','status','seo_noindex'],
                     'publication':'manual_only'}))
        await db.commit()
        return {'product_id':str(p.id),'category_code':body.category_code,
                'category_name':cat.name,'category_active':cat.active,
                'status':'draft','seo_noindex':True,'published':False}
    except Exception:
        await db.rollback()
        raise


@router.get('/proposals/{candidate_id}/diagnose')
async def proposal_diagnose(candidate_id:UUID,db:AsyncSession=Depends(get_session)):
    p=await db.scalar(select(Proposal).where(Proposal.candidate_id==candidate_id))
    if not p:raise HTTPException(404,'Proposal not found')
    descriptions=[s.get('description','') for s in (p.evidence.get('sources') or [])]
    return {'candidate_id':str(candidate_id),'proposal_id':str(p.id),'status':p.status,
            'category_code':p.category_code,'quality':p.quality,'conflicts':p.conflicts,
            'semantic':source_checks(p.category_code,descriptions),'source_descriptions':descriptions,
            'rule_revision':p.evidence.get('taxonomy_revision'),'manual_review_required':bool(p.conflicts),
            'note':'An unrecognized noun head is not evidence of a correct classification; no automatic changes.'}


@router.get('/review-queue')
async def review_queue(status:str='review',limit:int=Query(default=50,ge=1,le=100),
                       start_after:UUID|None=None,db:AsyncSession=Depends(get_session)):
    if status not in ('review','proposed','applied'):
        raise HTTPException(422,'Unknown status')
    q=select(Proposal).where(Proposal.status==status)
    if start_after:q=q.where(Proposal.candidate_id>start_after)
    rows=(await db.scalars(q.order_by(Proposal.candidate_id).limit(limit))).all()
    return {'status':status,'items':[{'candidate_id':str(x.candidate_id),
              'category_code':x.category_code,'conflicts':x.conflicts,
              'semantic':source_checks(x.category_code,[s.get('description','') for s in (x.evidence.get('sources') or [])]),
              'revision':x.evidence.get('taxonomy_revision')} for x in rows],
            'cursor':str(rows[-1].candidate_id) if rows else str(start_after) if start_after else None,
            'read_only':True}
