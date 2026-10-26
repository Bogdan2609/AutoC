#!/usr/bin/env python3
"""Read-only, independent full-catalog semantic audit for Autocodex v0.31.

Does not change rulebooks, proposals, preflights, products, offers, or publication.
Flags are *suspicions*, not proof of incorrect part or vehicle fitment.
Requires psycopg3 (already bundled with Autocodex web container).
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
from pathlib import Path
from typing import Any


# Independently authored *head item* patterns.  They are not imported from the
# classifier being audited.  A leading item determines a physical product;
# a named related assembly (chain, hub, belt etc.) must not hijack the category.
# Only use clear full descriptions.  Do not infer vehicle fitment or kit contents.
HEAD_RULES = [
    ('timing_chain_guide', r'^\s*(?:успокоител\w*|башмак|направляющ\w*)\s+цеп[ьи]\s*(?:грм|газораспределения)?', {'timing_chain_guide'}, 'Успокоитель/направляющая цепи — не цепь ГРМ'),
    ('timing_chain_tensioner', r'^\s*натяжител\w*\s+цеп[ьи]\b', {'timing_chain_tensioner'}, 'Натяжитель цепи — не цепь'),
    ('timing_belt_tensioner', r'^\s*натяжител\w*\s+ремня\s+грм\b', {'timing_belt_tensioner'}, 'Натяжитель ГРМ — не ремень'),
    ('drive_belt_tensioner', r'^\s*натяжител\w*\s+(?:(?:приводн\w*|поликлинов\w*)\s+)?ремн[ья]\b', {'drive_belt_tensioner','timing_belt_tensioner'}, 'Натяжитель ремня — не ремень'),
    ('timing_belt_roller', r'^\s*ролик\s+(?:натяжн\w*|натяжител\w*|грм)\b', {'timing_belt_roller','belt_tensioner','belt_pulley'}, 'Ролик — не ремень'),
    ('cv_boot', r'^\s*пыльник\s+(?:(?:внутр\w*|наруж\w*)\s+)?шрус\w*\b', {'cv_boot'}, 'Пыльник ШРУСа — не ШРУС'),
    ('steering_boot', r'^\s*пыльник\s+рулев\w*\s+рейк\w*', {'steering_boot'}, 'Пыльник рулевой рейки — не сама рейка'),
    ('strut_mount', r'^\s*опор\w*\s+(?:(?:передн\w*|задн\w*)\s+)?стойк\w*(?:\s+амортизатор\w*)?', {'strut_mount'}, 'Опора стойки — не стойка'),
    ('shock_absorber', r'^\s*амортизатор(?:ы)?\b', {'shock_absorber'}, 'Амортизатор — самостоятельная деталь'),
    ('stabilizer_link', r'^\s*(?:стойк\w*|тяг\w*)\s+стабил\w*', {'stabilizer_link'}, 'Стойка стабилизатора — не амортизатор'),
    ('stabilizer_bushing', r'^\s*втулк\w*\s+(?:(?:передн\w*|задн\w*)\s+)?стабил\w*', {'stabilizer_bushing'}, 'Втулка стабилизатора — не стойка'),
    ('wheel_bearing', r'^\s*подшипник\s+(?:(?:передн\w*|задн\w*)\s+)?ступиц\w*', {'wheel_bearing','wheel_bearing_qualified'}, 'Подшипник ступицы — не ступица в сборе'),
    ('wheel_seal', r'^\s*сальник\s+ступиц\w*', {'wheel_seal'}, 'Сальник ступицы — не ступица'),
    ('wheel_hub', r'^\s*ступиц\w*\s+(?:колес\w*|передн\w*|задн\w*)', {'wheel_hub'}, 'Ступица — не подшипник'),
    ('brake_caliper_repair_kit', r'^\s*ремкомплект\s+(?:тормозн\w*\s+)?суппорт\w*', {'brake_caliper_repair_kit'}, 'Ремкомплект суппорта — не суппорт'),
    ('caliper_guide', r'^\s*(?:(?:втулк\w*\s+)?направляющ\w*\s+(?:тормозн\w*\s+)?суппорт\w*|направляющ\w*\s+суппорт\w*)', {'caliper_guide'}, 'Направляющая — не суппорт'),
    ('brake_caliper_piston', r'^\s*поршень\s+(?:тормозн\w*\s+)?суппорт\w*', {'brake_caliper_piston'}, 'Поршень суппорта — не суппорт в сборе'),
    ('brake_caliper', r'^\s*суппорт\s+торм\w*', {'brake_caliper'}, 'Суппорт в сборе'),
    ('engine_oil_pan_gasket', r'^\s*прокладк\w*\s+(?:маслян\w*|маслян\w*|масляного)\s+картер\w*', {'engine_oil_pan_gasket'}, 'Прокладка поддона — не поддон'),
    ('valve_cover_gasket', r'^\s*прокладк\w*\s+(?:клапанн\w*\s+крышк\w*|крышк\w*\s+клапан\w*)', {'valve_cover_gasket'}, 'Прокладка крышки — не крышка'),
    ('intake_manifold_gasket', r'^\s*прокладк\w*\s+(?:впускн\w*\s+коллект\w*|коллект\w*\s+впускн\w*)', {'intake_manifold_gasket'}, 'Прокладка впускного коллектора — не коллектор'),
    ('exhaust_manifold_gasket', r'^\s*прокладк\w*\s+(?:выпускн\w*\s+коллект\w*|коллект\w*\s+выпускн\w*)', {'exhaust_manifold_gasket'}, 'Прокладка выпускного коллектора — не коллектор'),
    ('brake_pads', r'^\s*(?:колодк\w*\s+тормозн\w*|тормозн\w*\s+колодк\w*)', {'brake_pads'}, 'Тормозные колодки'),
    ('brake_disc', r'^\s*(?:диск\s+тормозн\w*|тормозн\w*\s+диск)', {'brake_disc'}, 'Тормозной диск — не диск сцепления'),
    ('clutch_disc', r'^\s*диск\s+сцеплен\w*', {'clutch_disc'}, 'Диск сцепления — не тормозной диск'),
    ('cabin_filter', r'^\s*фильтр\s+(?:салон\w*|салона)', {'cabin_filter'}, 'Салонный фильтр'),
    ('oil_filter', r'^\s*фильтр\s+(?:масл\w*|для\s+масл\w*)', {'oil_filter'}, 'Масляный фильтр'),
    ('air_filter', r'^\s*фильтр\s+(?:воздушн\w*|для\s+воздуха)', {'air_filter'}, 'Воздушный фильтр'),
    ('fuel_filter', r'^\s*фильтр\s+топливн\w*', {'fuel_filter'}, 'Топливный фильтр'),
    ('ac_condenser', r'^\s*радиатор\s+(?:кондиц\w*|конденсор\w*)', {'ac_condenser'}, 'Радиатор кондиционера — не радиатор двигателя'),
    ('radiator', r'^\s*радиатор\s+(?:охлажден\w*|двигател\w*)', {'radiator'}, 'Радиатор охлаждения двигателя'),
    ('turbocharger_cartridge', r'^\s*картридж\s+турбо\w*', {'turbocharger_cartridge'}, 'Картридж турбины — не вся турбина'),
    ('clutch_master_cylinder', r'^\s*(?:главн\w*\s+цилиндр\s+сцеплен\w*|цилиндр\s+сцеплен\w*\s+главн\w*)', {'clutch_master_cylinder'}, 'Главный цилиндр сцепления'),
    ('power_steering_pump', r'^\s*насос\s+(?:гидроусилител\w*|гур\b)', {'power_steering_pump'}, 'Насос ГУР — не топливный насос'),
    ('tie_rod_end', r'^\s*наконечник\s+рулев\w*\s+тяг\w*', {'tie_rod_end'}, 'Наконечник тяги — не тяга'),
    ('tie_rod', r'^\s*тяг\w*\s+рулев\w*', {'tie_rod'}, 'Рулевая тяга — не наконечник'),
    ('timing_chain', r'^\s*цеп[ьи]\s+грм\b', {'timing_chain'}, 'Цепь ГРМ без головного слова «успокоитель»'),
    ('timing_belt', r'^\s*ремень\s+грм\b', {'timing_belt'}, 'Ремень ГРМ'),
    ('drive_belt', r'^\s*ремень\s+(?:приводн\w*|поликлинов\w*)', {'drive_belt'}, 'Приводной ремень'),
    ('engine_mount', r'^\s*(?:подушк\w*|опор\w*)\s+двигател\w*', {'engine_mount'}, 'Опора двигателя'),
    ('suspension_spring', r'^\s*пружин\w*\s+(?:подвеск\w*|ходов\w*)', {'suspension_spring'}, 'Пружина подвески'),
    ('strut', r'^\s*стойк\w*\s+амортизационн\w*', {'strut','shock_absorber'}, 'Амортизационная стойка'),
    ('ball_joint', r'^\s*(?:опор\w*\s+шаров\w*|шаров\w*\s+опор\w*)', {'ball_joint'}, 'Шаровая опора — не рычаг в сборе'),
    ('arm_bushing', r'^\s*сайлентблок\w*\s+(?:(?:передн\w*|задн\w*)\s+)?рычаг\w*', {'arm_bushing'}, 'Сайлентблок рычага — не рычаг'),
    ('suspension_frame_bushing', r'^\s*сайлентблок\w*\s+рам\w*', {'suspension_frame_bushing'}, 'Сайлентблок рамы — не сайлентблок рычага'),
    ('control_arm', r'^\s*рычаг\w*\s+(?:подвеск\w*|передн\w*|задн\w*)', {'control_arm'}, 'Рычаг подвески — не шаровая опора'),
    ('crankshaft_position_sensor', r'^\s*датчик\s+положен\w*\s+(?:колен\w*|коленчат\w*)', {'crankshaft_position_sensor'}, 'Датчик коленвала'),
    ('camshaft_position_sensor', r'^\s*датчик\s+положен\w*\s+(?:распред\w*|распределительн\w*)', {'camshaft_position_sensor'}, 'Датчик распредвала'),
    ('parking_brake_cable', r'^\s*трос\s+(?:(?:стояночн\w*|ручн\w*)\s+тормоз\w*|ручн\.\s*торм\.)', {'parking_brake_cable'}, 'Трос ручного тормоза — не тормозной шланг'),
    ('fuel_pump', r'^\s*(?:насос\s+топливн\w*|бензонасос)', {'fuel_pump'}, 'Топливный насос — не насос ГУР'),
    ('water_pump', r'^\s*(?:помпа\s+водян\w*|насос\s+водян\w*)', {'water_pump'}, 'Водяной насос'),
    ('engine_oil', r'^\s*масло\s+моторн\w*', {'engine_oil'}, 'Моторное масло — не масляный фильтр'),
    ('transmission_oil', r'^\s*масло\s+трансмиссионн\w*', {'transmission_oil'}, 'Трансмиссионное масло'),
    ('engine_oil_pan_gasket', r'^\s*прокладк\w*\s+(?:масл\w*\s+)?поддон\w*', {'engine_oil_pan_gasket'}, 'Прокладка поддона — не поддон'),
    ('oil_pan', r'^\s*поддон\s+(?:двигател\w*|масл\w*)', {'oil_pan'}, 'Поддон — не прокладка'),
    ('fuel_injector', r'^\s*форсунк\w*\b', {'fuel_injector'}, 'Форсунка — не её комплект уплотнений'),
    ('sealing_o_ring', r'^\s*кольц\w*\s+уплотнительн\w*', {'sealing_o_ring'}, 'Уплотнительное кольцо — не форсунка'),
    ('piston_rings', r'^\s*кольца?\s+поршнев\w*', {'piston_rings'}, 'Поршневые кольца'),
    ('main_bearings', r'^\s*вкладыш\w*\s+коренн\w*', {'main_bearings'}, 'Коренные вкладыши'),
    ('wiper_blade', r'^\s*щ[её]тк\w*\s+стеклооч\w*', {'wiper_blade'}, 'Щётка — не трапеция стеклоочистителя'),
    ('wiper_linkage', r'^\s*трапец\w*\s+стеклооч\w*', {'wiper_linkage'}, 'Трапеция дворников'),
    ('spark_plug', r'^\s*свеч\w*\s+зажиган\w*', {'spark_plug'}, 'Свеча зажигания'),
    ('glow_plug', r'^\s*свеч\w*\s+накаливан\w*', {'glow_plug'}, 'Свеча накаливания'),
]
COMPILED_HEADS = tuple((code,re.compile(pattern,re.IGNORECASE),codes,explanation) for code,pattern,codes,explanation in HEAD_RULES)
LEFT_ONLY = re.compile(r'(?<!\w)(?:лев(?:ый|ая|ое|ую|ом|ой|ые)?|лев\.)(?!\w)', re.I)
RIGHT_ONLY = re.compile(r'(?<!\w)(?:прав(?:ый|ая|ое|ую|ом|ой|ые)?|прав\.)(?!\w)', re.I)
BOTH_SIDES = re.compile(r'прав\s*[/\\-]\s*лев|лев\s*[/\\-]\s*прав|обе\s+сторон', re.I)
BAD_ENCODING = re.compile(r'(?:Ð[\x80-\xbf]|Ñ[\x80-\xbf]){2,}')


def as_dict(value:Any)->dict:
    if isinstance(value,dict):return value
    if isinstance(value,str):
        try: return json.loads(value)
        except (ValueError,TypeError):return {}
    return {}


def as_list(value:Any)->list:
    if isinstance(value,list):return value
    if isinstance(value,str):
        try:
            obj=json.loads(value)
            return obj if isinstance(obj,list) else []
        except (ValueError,TypeError):return []
    return []


def source_type(description:str)->str|None:
    """Reference type from a deliberately conservative, independently written prefix dictionary."""
    if not description:return None
    for code,pattern,_allow,_why in COMPILED_HEADS:
        if pattern.search(description):return code
    return None


def inspect_record(rec:dict)->list[dict]:
    """Pure function used by smoke tests and DB streaming auditor."""
    findings=[]
    evidence=as_dict(rec.get('evidence'))
    sources=as_list(evidence.get('sources'))
    actual=str(rec.get('category_code') or '')
    attrs=as_dict(rec.get('attributes'))
    product_id=str(rec.get('product_id') or '')
    base=dict(candidate_id=str(rec.get('candidate_id') or ''),product_id=product_id,
              brand=str(rec.get('sample_brand') or ''),article=str(rec.get('sample_article') or ''),
              proposal_status=str(rec.get('proposal_status') or ''),
              category_code=actual,title=str(rec.get('title') or ''),
              product_status=str(rec.get('product_status') or ''),quality=rec.get('quality'))

    def add(level,code,reason,expected='',supplier='',description='',line='',src_cat=''):
        findings.append({**base,'severity':level,'check_code':code,'expected_type':expected,
                         'supplier':supplier,'source_line':line,'source_type':src_cat,
                         'description':description[:450], 'reason':reason})

    if rec.get('proposal_product_id') and rec.get('candidate_product_id') and rec['proposal_product_id']!=rec['candidate_product_id']:
        add('critical','product_identity_link_conflict','Proposal and candidate reference different products')
    if product_id and rec.get('product_status')=='draft' and rec.get('seo_noindex') is not True:
        add('critical','draft_indexation_leak','Linked draft is indexable although draft products must have seo_noindex=true')
    if product_id and rec.get('product_status') not in ('draft','published'):
        add('high','unexpected_product_state','Linked product has an unexpected state')
    if product_id and rec.get('product_status')=='published':
        add('info','published_product_preserved','Already-published product: this report must not modify it')
    if product_id and rec.get('proposal_status')=='review':
        add('high','materialized_review_proposal','Existing product is linked to a review proposal; manual investigation required')
    if product_id and rec.get('primary_category_count') is not None and rec['primary_category_count']!=1:
        add('high','invalid_primary_category_count','Linked Product must have exactly one primary category')
    if product_id and rec.get('product_name') and rec.get('product_name')!=rec.get('title'):
        add('info','product_proposal_title_difference','Product title differs from latest proposal (can be intentional manual edit)')
    if rec.get('proposal_status')=='proposed' and not actual:
        add('high','proposed_without_type','Proposed status without a classified type')
    if rec.get('proposal_status')=='proposed' and as_list(rec.get('conflicts')):
        add('high','proposed_with_flags','Proposed status unexpectedly carries conflict flags')
    if not sources:
        add('high','missing_evidence','Proposal has no source descriptions')
    if len({s.get('supplier') for s in sources}) != len(sources):
        add('medium','duplicate_source_evidence','Multiple records from a single supplier in one evidence snapshot')
    seen_codes=[];matching_sources=0
    for s in sources:
        supplier=str(s.get('supplier') or '')
        desc=str(s.get('description') or '')
        line=str(s.get('source_line') or '')
        parsed=as_dict(s.get('extracted'))
        code=source_type(desc)
        if code:
            seen_codes.append(code);matching_sources+=1
            _name,_pat,allow,why=next(x for x in COMPILED_HEADS if x[0]==code)
            if actual and actual not in allow:
                level='critical' if product_id else 'high'
                add(level,'independent_type_mismatch',why,code,supplier,desc,line,parsed.get('category_code') or '')
        if desc and BAD_ENCODING.search(desc):
            add('medium','source_encoding_suspect','Source description contains typical mojibake',supplier=supplier,description=desc,line=line)
        ex_code=parsed.get('category_code')
        if ex_code and actual and ex_code!=actual and rec.get('proposal_status') in ('proposed','applied'):
            add('high','classifier_source_type_mismatch','Stored supplier classification and final category disagree',ex_code,supplier,desc,line,ex_code)
        # Do not infer a side from missing information. Flag only direct explicit opposition.
        side=attrs.get('installation_side')
        if side and desc and not BOTH_SIDES.search(desc):
            explicit_l=bool(LEFT_ONLY.search(desc));explicit_r=bool(RIGHT_ONLY.search(desc))
            if side=='левый' and explicit_r and not explicit_l:
                add('high','side_opposition','Source explicitly says right; consolidated claim says left','правый',supplier,desc,line)
            if side=='правый' and explicit_l and not explicit_r:
                add('high','side_opposition','Source explicitly says left; consolidated claim says right','левый',supplier,desc,line)
    if len(set(seen_codes))>1:
        add('high' if rec.get('proposal_status')!='review' else 'medium','source_head_disagreement',
            'Different suppliers name distinct physical item types; compare their source lines',','.join(sorted(set(seen_codes))))
    return findings


def get_db_url()->str:
    url=os.environ.get('DATABASE_URL','')
    if not url:raise RuntimeError('DATABASE_URL not set; run inside the Autocodex web container')
    return url.replace('postgresql+psycopg://','postgresql://',1)


def run(output_dir:Path,limit:int=0)->dict:
    import psycopg
    from psycopg.rows import dict_row
    output_dir.mkdir(parents=True,exist_ok=True)
    reports={name:(output_dir/name).open('w',encoding='utf-8-sig',newline='') for name in ('findings.csv','unclassified.csv','by_type.csv')}
    columns=['candidate_id','product_id','brand','article','proposal_status','category_code','title','product_status','quality','severity','check_code','expected_type','supplier','source_line','source_type','description','reason']
    findings=csv.DictWriter(reports['findings.csv'],fieldnames=columns);findings.writeheader()
    unknown=csv.writer(reports['unclassified.csv']);unknown.writerow(['candidate_id','brand','article','proposal_status','description','supplier'])
    group_counts=collections.Counter();status_counts=collections.Counter();severity_counts=collections.Counter()
    issue_counts=collections.Counter();type_summaries=collections.Counter();unclassified_count=0
    scanned=0;affected=set();affected_critical=set();revision=None
    try:
        with psycopg.connect(get_db_url(),row_factory=dict_row,connect_timeout=15) as conn:
            with conn.cursor() as c:
                c.execute('SET TRANSACTION READ ONLY')
                c.execute('SELECT revision FROM classification_revisions WHERE id=1')
                val=c.fetchone();revision=val['revision'] if val else None
            sql='''SELECT m.candidate_id, COALESCE(m.product_id,c.product_id) AS product_id,
                          m.product_id AS proposal_product_id, c.product_id AS candidate_product_id,
                          m.status AS proposal_status,
                          m.title, m.category_code, m.quality, m.conflicts,
                          m.attributes, m.evidence, c.sample_brand, c.sample_article,
                          p.status AS product_status, p.seo_noindex, p.name AS product_name,
                          CASE WHEN p.id IS NOT NULL THEN (SELECT COUNT(*) FROM catalog_product_categories pc WHERE pc.product_id=p.id AND pc.is_primary=true) END AS primary_category_count
                   FROM multi_source_proposals m
                   JOIN catalog_bulk_candidates c ON c.id=m.candidate_id
                   LEFT JOIN products p ON p.id=COALESCE(m.product_id,c.product_id)
                   ORDER BY m.candidate_id'''
            if limit>0:sql+=' LIMIT '+str(int(limit))
            with conn.cursor(name='semantic_audit_v031') as cur:
                cur.itersize=500
                cur.execute(sql)
                for rec in cur:
                    scanned+=1
                    status_counts[rec['proposal_status']]+=1
                    if not rec['category_code']:
                        unclassified_count+=1
                        s=as_list(as_dict(rec['evidence']).get('sources'))
                        for item in (s[:1] or [{}]):
                            unknown.writerow([str(rec['candidate_id']),rec['sample_brand'],rec['sample_article'],rec['proposal_status'],item.get('description',''),item.get('supplier','')])
                    type_summaries[rec['category_code'] or 'UNCLASSIFIED']+=1
                    issues=inspect_record(rec)
                    if issues:affected.add(str(rec['candidate_id']))
                    for issue in issues:
                        findings.writerow(issue)
                        issue_counts[issue['check_code']]+=1
                        severity_counts[issue['severity']]+=1
                        group_counts[(issue['severity'],issue['check_code'],rec['category_code'] or 'UNCLASSIFIED')]+=1
                        if issue['severity']=='critical':affected_critical.add(str(rec['candidate_id']))
        wt=csv.writer(reports['by_type.csv']);wt.writerow(['severity','check_code','category_code','findings'])
        for (sev,code,typ),count in group_counts.most_common():wt.writerow([sev,code,typ,count])
        summary={'audit_version':'v0.31-readonly-1','read_only':True,'candidates_scanned':scanned,
                 'rule_revision_in_database':revision,'statuses':dict(status_counts),
                 'unclassified_with_empty_category_code':unclassified_count,
                 'distinct_candidates_with_findings':len(affected),
                 'distinct_candidates_with_critical_findings':len(affected_critical),
                 'finding_counts_by_severity':dict(severity_counts),
                 'finding_counts_by_code':dict(issue_counts.most_common()),
                 'top_categories':dict(type_summaries.most_common(30)),
                 'caveat':'Independent head rules cover only explicit known phrases; flags are suspects, not exhaustive proof; no inferred vehicle fitment.'}
        (output_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return summary
    finally:
        for f in reports.values():f.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='/tmp/catalog_full_audit_v031',help='Directory for CSV + JSON results')
    parser.add_argument('--limit',type=int,default=0,help='Use e.g. 1000 for a smoke test (default: all)')
    args=parser.parse_args()
    run(Path(args.output),args.limit)
