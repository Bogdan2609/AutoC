"""v0.31.3: resumable, conservative automation for missing categories and draft cards.

Runs are opt-in administrative jobs.  Neither this module nor its API contains
publication, supplier checkout or payment transitions.  All writes are audited.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.modules.catalog.models import Brand, Product, ProductChangeLog
from app.modules.catalog_ext.models import Category, ProductCategory
from app.modules.commerce.models import Job
from app.modules.draft_materialization.finalization_api import evaluate_legacy, legacy_case
from app.modules.draft_materialization.models import DraftPreflight, DraftRun, utcnow
from app.modules.draft_materialization.service import CHUNK, materialize_chunk, preflight_chunk
from app.modules.multi_enrichment.classifier import classify_source
from app.modules.multi_enrichment.taxonomy_db import TaxonomyRevision, snapshot

# Bound individual worker invocations, with durable hand-off to the next job.
PAGES_PER_JOB = 5
LEGACY_PAGE = 20


def _legacy_ids_query(after=None):
    q = select(Product.id).where(
        Product.status == 'draft', Product.seo_noindex.is_(True),
        ~select(ProductCategory.id).where(
            ProductCategory.product_id == Product.id,
            ProductCategory.is_primary.is_(True)).exists(),
        ~select(DraftPreflight.id).where(DraftPreflight.product_id == Product.id).exists(),
    )
    if after is not None:
        q = q.where(Product.id > after)
    return q.order_by(Product.id)


async def _automatic_legacy_candidate(db, product_id:UUID, expected_revision:int,
                                      *, apply:bool, run_id:UUID):
    """Re-evaluate and conditionally apply ONE category under locks, no title changes.

    The same evidence checks apply to preview and apply.  An explicit taxonomy
    pattern MUST agree with the physical head: head detection alone is insufficient.
    """
    rev = await db.scalar(select(TaxonomyRevision).where(
        TaxonomyRevision.id == 1).with_for_update())
    if rev is None or rev.revision != expected_revision:
        raise RuntimeError('Classification revision changed during automation')
    p,b,c,o = await legacy_case(db, product_id, locked=apply)
    book = await snapshot(db)
    if book.revision != expected_revision:
        raise RuntimeError('Classification revision changed during automation')
    view = evaluate_legacy(p,b,c,o,book)
    reasons = list(view['reasons'])
    code = view['suggested_category_code']
    if await db.scalar(select(ProductChangeLog.id).where(
        ProductChangeLog.product_id == p.id,
        ProductChangeLog.action.in_(('workspace_save','publish_manual','unpublish',
                                    'semantic_draft_repair_v0311'))).limit(1)):
        reasons.append('operator_or_repair_history_preserved')
    # Don't automatically rewrite or classify a titled, potentially manually
    # curated legacy card.  555 / 4U examples have just brand + article.
    if p.name.strip().casefold() != f'{b.name} {p.article}'.casefold():
        reasons.append('non_identity_title_requires_review')
    if code:
        for fo, batch in o:
            extracted = classify_source(fo.description, book, batch.supplier_code)
            if extracted['category_code'] != code or extracted['flags']:
                reasons.append('active_rule_does_not_confirm_source')
                break
    if not code:
        reasons.append('no_verified_category')
    cat = await db.scalar(select(Category).where(Category.name == book.types[code][0])) if code in book.types else None
    if not cat:
        reasons.append('category_record_missing')
    if reasons:
        return {'eligible':False,'changed':False,'reasons':sorted(set(reasons))}
    if not apply:
        return {'eligible':True,'changed':False,'reasons':[]}
    # Re-check both category absence and Product state while row locks are held.
    if (p.status != 'draft' or not p.seo_noindex or c or
        await db.scalar(select(ProductCategory.id).where(ProductCategory.product_id == p.id).limit(1))):
        return {'eligible':False,'changed':False,'reasons':['category_or_status_changed']}
    db.add(ProductCategory(product_id=p.id,category_id=cat.id,is_primary=True))
    db.add(ProductChangeLog(product_id=p.id,action='legacy_category_auto_v0313',
        changes={'category_code':code,'category_id':str(cat.id),
                 'evidence_hash':view['evidence_token'],'taxonomy_revision':expected_revision,
                 'run_id':str(run_id), 'category_active_unchanged':cat.active,
                 'unchanged_fields':['name','slug','seo','attributes','offers','status','seo_noindex'],
                 'publication':'individual_manual_only'}))
    await db.commit()
    return {'eligible':True,'changed':True,'reasons':[]}


async def legacy_page(db, run:DraftRun, *, apply:bool):
    """Keyset scan of legacy drafts; failed matches remain unmodified."""
    params = dict(run.parameters)
    after = UUID(params['legacy_cursor']) if params.get('legacy_cursor') else None
    total = int(params.get('legacy_limit',0))
    remaining = max(0,total-int(params.get('legacy_scanned',0)))
    ids = (await db.scalars(_legacy_ids_query(after).limit(min(LEGACY_PAGE,remaining)))).all() if remaining else []
    accepted = rejected = recovered = 0
    for pid in ids:
        result = await _automatic_legacy_candidate(db,pid,int(params['rule_revision']),apply=apply,run_id=run.id)
        accepted += int(result['eligible']);rejected += int(not result['eligible'])
        recovered += int(result['changed'])
    if ids:
        params['legacy_cursor'] = str(ids[-1])
    params['legacy_scanned'] = int(params.get('legacy_scanned',0)) + len(ids)
    params['legacy_recovered'] = int(params.get('legacy_recovered',0)) + recovered
    params['legacy_reviewed'] = int(params.get('legacy_reviewed',0)) + rejected
    params['legacy_eligible'] = int(params.get('legacy_eligible',0)) + accepted
    if not ids or len(ids) < min(LEGACY_PAGE,remaining) or params['legacy_scanned'] >= total:
        params['stage'] = 'candidates'
    run.parameters = params
    await db.commit()
    return len(ids)


async def candidate_page(db,run:DraftRun,*,apply:bool):
    """Checkpoint after one page. Restrict materialization to that exact UUID range."""
    params = dict(run.parameters)
    remaining = int(params['limit']) - run.scanned
    if remaining <= 0:
        return False
    after = UUID(params['cursor']) if params.get('cursor') else None
    out = await preflight_chunk(db, after=after, limit=min(CHUNK,remaining), run_id=run.id)
    if not out['seen']:
        return False
    if apply:
        # Old preflight records beyond this keyset page MUST NOT be consumed.
        made = await materialize_chunk(db,limit=out['seen'],run_id=run.id,
                                       candidate_after=after,candidate_to=out['cursor'])
    else:
        made = {'created':0,'reviewed':0}
    # Crash-safe: on retry, materialized proposals are skipped and the current
    # page can be revisited without creating any duplicate Product.
    params['cursor'] = str(out['cursor'])
    run.parameters = params
    run.scanned += out['seen']
    run.eligible += out['eligible']
    run.reviewed += out['reviewed'] + made['reviewed']
    run.created += made['created']
    await db.commit()
    return True


async def execute_auto_pipeline(db, run:DraftRun):
    """Finite worker slice; enqueue its successor without admin intervention."""
    try:
        for _ in range(PAGES_PER_JOB):
            await db.refresh(run)
            params = dict(run.parameters)
            if (await snapshot(db)).revision != int(params['rule_revision']):
                raise RuntimeError('Taxonomy changed: run is paused with failed status')
            apply = params['mode'] == 'drafts'
            if params.get('stage') == 'legacy':
                await legacy_page(db,run,apply=apply)
                continue
            if params.get('stage') == 'candidates':
                progressed = await candidate_page(db,run,apply=apply)
                if progressed:
                    continue
                params = dict(run.parameters)
                params['stage'] = 'complete'
                run.parameters = params
                break
            break
        params = dict(run.parameters)
        done = params['stage']=='complete' or (params['stage']=='candidates' and run.scanned >= int(params['limit']))
        if done:
            params['stage'] = 'complete'
            run.parameters = params
            run.status = 'completed';run.finished_at = utcnow()
            run.message = 'Completed; only unpublished drafts and safe legacy categories'
        else:
            # One continuation job per slice, backed by the commerce job queue.
            # The next invocation cannot exceed the bounded 5-page slice.
            step = int(params.get('next_job',0))+1
            params['next_job'] = step
            run.parameters = params
            db.add(Job(dedup_key=f'catalog-auto-v0313:{run.id}:step:{step}',
                kind='draft_materialization',payload={'run_id':str(run.id),
                    'auto_step':step,'auto_generation':int(params.get('generation',0))},
                status='pending',due_at=utcnow(),attempts=0))
            run.status = 'processing'
        await db.commit()
    except Exception as exc:
        await db.rollback()
        db.add(run)
        run.status = 'failed'
        run.finished_at = utcnow()
        run.message = f'{type(exc).__name__}: {str(exc)[:180]}'
        await db.commit()
        raise
