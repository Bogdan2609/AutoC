"""Safe, non-publishing catalog enrichment in bounded committed batches.

No network, no supplier writes. All automatic writes target *draft* products;
operator edits, existing category/attribute values, and SEO are preserved.
"""
from __future__ import annotations
import uuid
from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_bulk.models import CatalogCandidate, CatalogCandidateBatch
from app.modules.catalog_ext.models import (AttributeDefinition, Category, ProductCategory,
                                             ProductAttribute)
from app.modules.supplier_feeds.models import FeedBatch
from .models import (EnrichmentProposal as Proposal, EnrichmentRun as Run,
                     EnrichmentDecisionAudit as Audit, utcnow)
from .rules import ENGINE_VERSION, TAXONOMY, ROOTS, suggest

ATTRS = {
    'installation_side': ('Сторона установки', ['левый','правый','обе стороны']),
    'installation_position': ('Расположение', ['передний','задний']),
}


def proposal_view(p):
    return {'id':str(p.id),'product_id':str(p.product_id),'candidate_id':str(p.candidate_id),
            'batch_id':str(p.batch_id),'title':p.title,'category_code':p.category_code,
            'category_id':str(p.category_id) if p.category_id else None,
            'attributes':p.attributes,'quality':p.quality,'flags':p.flags,'status':p.status,
            'evidence':p.evidence,'rule_version':p.rule_version,'review_note':p.review_note}


def run_view(r):
    return {'id':str(r.id),'kind':r.kind,'status':r.status,'batch_id':str(r.batch_id),
            'affected':r.affected,'reviewed':r.reviewed,'message':r.message,
            'created_at':r.created_at.isoformat() if r.created_at else None,
            'finished_at':r.finished_at.isoformat() if r.finished_at else None}


async def bootstrap_taxonomy(db):
    """Explicit admin-only operation. Never rewrites an existing taxonomy definition.

    Reuse categories with exactly matching names; do not create duplicates.
    All newly created categories remain inactive on the public storefront.
    """
    existing=(await db.scalars(select(Category))).all()
    by_name={x.name.casefold():x for x in existing}
    added=[]
    roots={}
    for code,(name,_) in ROOTS.items():
        cat=by_name.get(name.casefold())
        if cat is None:
            cat=Category(name=name,slug=f'auto-v028-root-{code}',active=False,position=0)
            db.add(cat);by_name[name.casefold()]=cat;added.append(name)
        roots[code]=cat
    await db.flush()
    for code,(name,_,_) in TAXONOMY.items():
        if name.casefold() in by_name:continue
        root_code=next((key for key,(_,members) in ROOTS.items() if code in members),None)
        cat=Category(name=name,slug=f'auto-v028-{code}',
                     parent_id=roots[root_code].id if root_code else None,position=0,active=False)
        db.add(cat);by_name[name.casefold()]=cat;added.append(name)
    defs=(await db.scalars(select(AttributeDefinition))).all()
    by_code={x.code:x for x in defs}
    for code,(name,allowed) in ATTRS.items():
        current=by_code.get(code)
        if current:
            present = list(current.enum_values or [])
            baseline = ['левый','правый'] if code=='installation_side' else allowed
            if current.data_type!='enum' or not set(baseline).issubset(set(present)):
                raise ValueError(f'Existing attribute {code} has incompatible definition')
            # Explicit admin bootstrap only: additive enum extension, never
            # changing/removing pre-existing values or ProductAttribute rows.
            missing = [value for value in allowed if value not in present]
            if missing:
                current.enum_values = present + missing
        else:
            db.add(AttributeDefinition(code=code,name=name,data_type='enum',
                enum_values=allowed,filterable=False,active=True))
    await db.commit()
    return {'created_categories':added,'total_categories_for_rules':len({v[0] for v in TAXONOMY.values()}),
            'attribute_codes':list(ATTRS),'auto_publish':False}


async def propose_chunk(db, batch_id:UUID, after:UUID|None, limit:int):
    """Bounded source query; no N+1 reads, idempotent after a crash."""
    q=(select(CatalogCandidate,Product).join(Product,Product.id==CatalogCandidate.product_id)
       .join(CatalogCandidateBatch,CatalogCandidateBatch.candidate_id==CatalogCandidate.id)
       .where(CatalogCandidateBatch.batch_id==batch_id,CatalogCandidate.status=='materialized')
       .order_by(CatalogCandidate.id).limit(limit).with_for_update(of=Product))
    if after:q=q.where(CatalogCandidate.id>after)
    rows=(await db.execute(q)).all()
    if not rows:return {'seen':0,'proposed':0,'review':0,'after':after}
    candidate_ids=[c.id for c,_ in rows]
    sources=(await db.scalars(select(CatalogCandidateBatch).where(
        CatalogCandidateBatch.candidate_id.in_(candidate_ids)))).all()
    grouped={key:[] for key in candidate_ids}
    for source in sources:grouped[source.candidate_id].append(source)
    products=[p.id for _,p in rows]
    existing=(await db.scalars(select(Proposal).where(Proposal.product_id.in_(products)))).all()
    by_product={p.product_id:p for p in existing}
    # A review explicitly set by an operator is not a disposable machine result.
    review_ids=[p.id for p in existing if p.status=='review']
    operator_reviews=set()
    if review_ids:
        operator_reviews=set((await db.scalars(select(Audit.proposal_id).where(
            Audit.proposal_id.in_(review_ids),Audit.origin=='admin_api',
            Audit.new_status=='review'))).all())
    categories=(await db.scalars(select(Category))).all()
    by_name={}
    duplicate_names=set()
    for c in categories:
        key=c.name.casefold()
        if key in by_name:duplicate_names.add(key)
        by_name[key]=c
    original_categories=(await db.scalars(select(ProductCategory).where(
        ProductCategory.product_id.in_(products),ProductCategory.is_primary==True))).all()
    existing_primary={c.product_id:c.category_id for c in original_categories}
    count=review=0
    for candidate,product in rows:
        if product.status!='draft':continue
        sources=grouped.get(candidate.id,[])
        all_descriptions=[s.sample_description for s in sources if s.sample_description]
        variations=max([s.description_variants for s in sources] or [1])
        res=suggest(candidate.sample_brand,candidate.sample_article,
                    candidate.sample_description,all_descriptions,variations)
        category=by_name.get((res.category_name or '').casefold())
        flags=set(res.flags)
        if res.category_code and category is None: flags.add('category_unconfigured')
        if res.category_name and res.category_name.casefold() in duplicate_names:
            flags.add('category_ambiguous')
        existing_cat=existing_primary.get(product.id)
        if existing_cat and (not category or existing_cat!=category.id):
            flags.add('manual_category_preserved')
        # A prior operator rename means this proposal cannot be safely applied.
        expected=f'{candidate.sample_brand} {candidate.sample_article}'
        if product.name.casefold().strip()!=expected.casefold().strip():
            flags.add('manual_title_preserved')
        if not product.seo_noindex:flags.add('draft_noindex_required')
        new_status='review' if flags else 'proposed'
        prop=by_product.get(product.id)
        if prop and (prop.status in ('approved','applied','rejected') or prop.id in operator_reviews):
            continue # Preserve applied changes and all operator decisions.
        if (prop and prop.fingerprint==res.provenance_hash and prop.rule_version==ENGINE_VERSION
                and prop.category_id==(category.id if category else None)
                and sorted(prop.flags or [])==sorted(flags)):
            continue # stable idempotency
        evidence={'brand':candidate.sample_brand,'article':candidate.sample_article,
                  'description':candidate.sample_description,
                  'explicit_dual_side':res.attributes.get('installation_side')=='обе стороны',
                  'source_batch_ids':[str(s.batch_id) for s in sources][:30],
                  'description_variants':variations}
        if prop is None:
            prop=Proposal(product_id=product.id,candidate_id=candidate.id,batch_id=batch_id,
                 title=res.title,fingerprint=res.provenance_hash,rule_version=ENGINE_VERSION)
            db.add(prop)
        prop.title=res.title;prop.category_code=res.category_code
        prop.category_id=category.id if category else None
        prop.attributes=res.attributes;prop.quality=max(0,res.score-8*(len(flags)-len(res.flags)))
        prop.evidence=evidence;prop.flags=sorted(flags);prop.status=new_status
        prop.fingerprint=res.provenance_hash;prop.rule_version=ENGINE_VERSION
        prop.updated_at=utcnow()
        count+=1;review+=int(new_status=='review')
    await db.commit()
    return {'seen':len(rows),'proposed':count,'review':review,'after':rows[-1][0].id}


async def approve_safe_chunk(db,batch_id:UUID,limit:int):
    q=(select(Proposal).where(Proposal.batch_id==batch_id,Proposal.status=='proposed',
        Proposal.rule_version==ENGINE_VERSION,Proposal.quality>=85,Proposal.category_id.is_not(None))
       .order_by(Proposal.id).limit(limit).with_for_update(skip_locked=True))
    rows=(await db.scalars(q)).all()
    if not rows:return 0
    for p in rows:
        if p.flags:continue
        p.status='approved';p.updated_at=utcnow()
        db.add(Audit(proposal_id=p.id,old_status='proposed',new_status='approved',
                     origin='auto_rule',note=f'Rule {ENGINE_VERSION}; reviewed requirements'))
    await db.commit()
    return len(rows)


async def apply_chunk(db,batch_id:UUID,limit:int):
    q=(select(Proposal).where(Proposal.batch_id==batch_id,Proposal.status=='approved')
       .order_by(Proposal.id).limit(limit).with_for_update(skip_locked=True))
    rows=(await db.scalars(q)).all()
    if not rows:return {'selected':0,'applied':0,'reviewed':0}
    attrs=(await db.scalars(select(AttributeDefinition).where(
        AttributeDefinition.code.in_(ATTRS.keys())))).all()
    attr_by_code={a.code:a for a in attrs}
    selected=0;applied=reviewed=0
    for p in rows:
        selected+=1
        product=await db.scalar(select(Product).where(Product.id==p.product_id).with_for_update())
        candidate=await db.get(CatalogCandidate,p.candidate_id)
        issue=None
        if not product or not candidate or candidate.product_id!=p.product_id:
            issue='Identity link changed'
        elif product.status!='draft':issue='Product is not a draft'
        elif p.rule_version!=ENGINE_VERSION:issue='Rule version changed; regenerate and reapprove' 
        elif not p.category_id:issue='Category unavailable'
        elif (p.flags or p.quality<85) and not (p.evidence or {}).get('manual_confirmed'):
            issue='Proposal no longer qualifies'
        elif product.name.strip().casefold()!=f'{candidate.sample_brand} {candidate.sample_article}'.casefold():
            issue='Product title manually edited; preserved'
        elif await db.scalar(select(ProductCategory.id).where(ProductCategory.product_id==product.id,
                  ProductCategory.is_primary==True,ProductCategory.category_id!=p.category_id)):
            issue='Existing primary category differs; preserved'
        else:
            for code,value in (p.attributes or {}).items():
                attr=attr_by_code.get(code)
                if not attr or attr.data_type!='enum' or value not in (attr.enum_values or []):
                    issue=f'Attribute definition incompatible: {code}';break
                already=await db.scalar(select(ProductAttribute).where(
                    ProductAttribute.product_id==product.id,ProductAttribute.attribute_id==attr.id))
                if already and already.value_enum!=value:
                    issue=f'Existing {code} conflicts; operator value preserved';break
        if issue:
            p.status='review';p.review_note=issue;p.updated_at=utcnow();reviewed+=1
            db.add(Audit(proposal_id=p.id,old_status='approved',new_status='review',
                        origin='system_guard',note=issue))
            await db.commit();continue
        changes={'name':{'old':product.name,'new':p.title}}
        product.name=p.title
        # Product.description is supplier evidence, not fabricated editorial text.
        if not product.description and (p.evidence or {}).get('description'):
            product.description=p.evidence['description']
            changes['description_source']='supplier_feed'
        if not product.seo_noindex:
            product.seo_noindex=True
            changes['seo_noindex']=True
        previous=await db.scalar(select(ProductCategory).where(
            ProductCategory.product_id==product.id,ProductCategory.category_id==p.category_id))
        if not previous:
            db.add(ProductCategory(product_id=product.id,category_id=p.category_id,is_primary=True))
            changes['primary_category_added']=str(p.category_id)
        elif not previous.is_primary:
            previous.is_primary=True
            changes['primary_category_promoted']=str(p.category_id)
        for code,value in (p.attributes or {}).items():
            attr=attr_by_code[code]
            existing=await db.scalar(select(ProductAttribute).where(
                ProductAttribute.product_id==product.id,ProductAttribute.attribute_id==attr.id))
            if existing is None:
                db.add(ProductAttribute(product_id=product.id,attribute_id=attr.id,value_enum=value))
                changes[f'attribute_added:{code}']=value
            # Existing values are NEVER replaced.
        db.add(ProductChangeLog(product_id=product.id,action='enrich_v028',changes=changes))
        p.status='applied';p.applied_at=utcnow();p.updated_at=utcnow()
        db.add(Audit(proposal_id=p.id,old_status='approved',new_status='applied',
                     origin='worker',note='Draft-only changes; no publication'))
        await db.commit()
        applied+=1
    return {'selected':selected,'applied':applied,'reviewed':reviewed}


async def execute_run(db,run_id:UUID):
    run=await db.scalar(select(Run).where(Run.id==run_id).with_for_update())
    if not run or run.status not in ('queued','processing'):return
    if run.status=='queued':run.status='processing';await db.commit()
    try:
        remaining=max(0,int(run.parameters.get('limit',500))-int(run.affected))
        if run.kind=='propose':
            cursor=UUID(run.parameters['cursor']) if run.parameters.get('cursor') else None
            while remaining:
                result=await propose_chunk(db,run.batch_id,cursor,min(remaining,200))
                if not result['seen']:break
                remaining-=result['seen']
                run=await db.get(Run,run_id)
                run.affected+=result['seen'];run.reviewed+=result['review']
                run.message=f"{result['proposed']} proposals refreshed in latest page"
                run.parameters={**run.parameters,'cursor':str(result['after'])}
                await db.commit();cursor=result['after']
        elif run.kind=='approve_safe':
            while remaining:
                count=await approve_safe_chunk(db,run.batch_id,min(remaining,200))
                if not count:break
                remaining-=count;run=await db.get(Run,run_id)
                run.affected+=count;await db.commit()
        elif run.kind=='apply':
            while remaining:
                result=await apply_chunk(db,run.batch_id,min(remaining,100))
                if not result['selected']:break
                remaining-=result['selected'];run=await db.get(Run,run_id)
                run.affected+=result['selected'];run.reviewed+=result['reviewed']
                run.message=f"{run.affected-run.reviewed} applied; {run.reviewed} sent to review"
                await db.commit()
        else:raise ValueError('Unknown enrichment job')
        run=await db.get(Run,run_id)
        run.status='completed';run.finished_at=utcnow()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        run=await db.get(Run,run_id)
        if run:
            run.status='failed';run.message=type(exc).__name__+': '+str(exc)[:180]
            run.finished_at=utcnow();await db.commit()
        raise
