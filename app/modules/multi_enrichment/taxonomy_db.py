"""v0.30 database-owned, audited classifier dictionary. Not a publication mechanism.

Seed patterns are intentionally precise. Broad ambiguous text must remain review-only.
Patterns and groups may be edited via authenticated admin API; existing human
proposal decisions never change as a side effect of editing the rulebook.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, select
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class TaxonomyGroup(Base):
    __tablename__ = 'classification_groups'
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(ForeignKey('classification_groups.code', ondelete='RESTRICT'))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxonomyType(Base):
    __tablename__ = 'classification_types'
    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    group_code: Mapped[str] = mapped_column(ForeignKey('classification_groups.code', ondelete='RESTRICT'), nullable=False)
    category_name: Mapped[str] = mapped_column(String(180), nullable=False)
    title_stem: Mapped[str] = mapped_column(String(180), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxonomyPattern(Base):
    __tablename__ = 'classification_patterns'
    __table_args__ = (Index('ix_classification_patterns_active_priority', 'active', 'priority'),
        CheckConstraint('priority BETWEEN 0 AND 1000', name='classification_pattern_priority'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    type_code: Mapped[str] = mapped_column(ForeignKey('classification_types.code', ondelete='RESTRICT'), nullable=False)
    positive_regex: Mapped[str] = mapped_column(String(300), nullable=False)
    negative_regex: Mapped[str | None] = mapped_column(String(300))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    supplier: Mapped[str] = mapped_column(String(32), nullable=False, default='any')
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaxonomyRevision(Base):
    __tablename__ = 'classification_revisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TaxonomyAudit(Base):
    __tablename__ = 'classification_rule_audit'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(130), nullable=False)
    before: Mapped[str] = mapped_column(Text, nullable=False, default='')
    after: Mapped[str] = mapped_column(Text, nullable=False, default='')
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default='admin_api')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


GROUPS = {
    'engine': 'Двигатель', 'transmission': 'Трансмиссия', 'suspension': 'Подвеска',
    'steering': 'Рулевое управление', 'braking': 'Тормозная система',
    'electrical': 'Электрооборудование', 'climate': 'Охлаждение и климат',
    'body': 'Кузов и салон', 'exhaust': 'Выхлопная система',
    'maintenance': 'Фильтры и жидкости', 'hardware': 'Крепёж и инструменты',
    'misc': 'Прочие автотовары',
}

# Additional types derived from *complete* user-observed vendor descriptions.
# Third entry is a collection of targeted regexes, not a global fuzzy matcher.
SEED_TYPES = {
    'timing_chain_guide': ('engine','Успокоители цепи ГРМ','Успокоитель цепи ГРМ',[r'\b(?:успокоител\w*|башмак\w*|направляющ\w*)\s+цеп\w*\s+грм\b']),
    'timing_chain_tensioner': ('engine','Натяжители цепи ГРМ','Натяжитель цепи ГРМ',[r'\bнатяжител\w*\s+цеп\w*\s+грм\b']),
    'belt_tensioner': ('engine','Ролики-натяжители','Ролик натяжной',[r'\bролик\s+натяжителя\s+(?:приводн\w*\s+)?ремн\w*\b']),
    'crankshaft_position_sensor': ('electrical', 'Датчики положения коленвала', 'Датчик положения коленвала', [r'\bдатчик\s+положени[яе]\s+(?:колен(?:чатого\s+вала|вала)|коленв\.?)\b']),
    'camshaft_position_sensor': ('electrical', 'Датчики положения распредвала', 'Датчик положения распредвала', [r'\bдатчик\s+положени[яе]\s+(?:распред(?:елительного\s+вала|вала)|распредв\.?)\b']),
    'valve_cover_gasket': ('engine', 'Прокладки клапанной крышки', 'Прокладка клапанной крышки', [r'\bпрокладк\w*\s+крышк\w*\s+(?:клапан\w*|гбц)\b',r'\bпрокладк\w*\s+клапанн\w*\s+крышк\w*']),
    'engine_oil_pan_gasket': ('engine','Прокладки масляного поддона','Прокладка масляного поддона',[r'\bпрокладк\w*\s+(?:масл\w*\s+картера|поддона\s+двигателя)']),
    'piston_rings': ('engine','Поршневые кольца','Кольца поршневые',[r'\bкольца?\s+поршнев\w*']),
    'main_bearings': ('engine','Коренные вкладыши','Вкладыши коренные',[r'\bвкладыш\w*\s+коренн\w*']),
    'timing_actuator': ('engine','Механизмы газораспределения','Механизм газораспределения',[r'\bмеханизм\s+газораспределения\b']),
    'drive_belt_tensioner': ('engine','Натяжители приводных ремней','Натяжитель ремня',[r'\bнатяжител\w*\s+(?:приводн\w*\s+)?(?:ремня|ремень|ремн\w*)']),
    'timing_belt_tensioner': ('engine','Натяжители ремня ГРМ','Натяжитель ремня ГРМ',[r'^натяжител\w*\s+ремня\s+грм\b']),
    'timing_belt_roller': ('engine','Ролики ГРМ','Ролик ГРМ',[r'\bролик\s+натяжител\w*\s+ремня\s+грм\b']),
    'suspension_spring': ('suspension','Пружины подвески','Пружина подвески',[r'\bпружин\w*\s+(?:ходовой\s+части|ходовой)\b']),
    'stabilizer_bushing': ('suspension','Втулки стабилизатора','Втулка стабилизатора',[r'\bвтулк\w*\s+стабил\w*',r'\bвтулк\w*\s+полиуретанов\w*\s+стабил\w*']),
    'brake_caliper_repair_kit': ('braking','Ремкомплекты тормозных суппортов','Ремкомплект суппорта',[r'\bремкомплект\s+(?:тормозного\s+)?суппорта\b']),
    'caliper_guide': ('braking','Направляющие суппорта','Направляющая суппорта',[r'\bнаправляющ\w*\s+(?:тормозного\s+)?суппорта\b']),
    'brake_drum': ('braking','Тормозные барабаны','Барабан тормозной',[r'\bбарабан\s+тормозн\w*']),
    'parking_brake_cable': ('braking','Тросы ручного тормоза','Трос ручного тормоза',[r'\bтрос\s+ручн\.?\s*торм\w*']),
    'clutch_master_cylinder': ('transmission','Главные цилиндры сцепления','Цилиндр сцепления главный',[r'\bцилиндр\s+сцеп\.?\s*.*\bглавн\w*',r'\bглавн\w*\s+цилиндр\s+сцепления']),
    'steering_coupling': ('steering','Муфты рулевых тяг','Муфта соединительная рулевой тяги',[r'\bмуфта\s+соединительн\w*\s+рулев\w*\s+тяг\w*']),
    'power_steering_pump': ('steering','Насосы ГУР','Насос гидроусилителя руля',[r'\bнасос\s+гидроусилителя\b']),
    'exhaust_flex': ('exhaust','Гофры выхлопной системы','Гофра выхлопной системы',[r'\bгофр\w*\s+глушителя\b']),
    'exhaust_resonator': ('exhaust','Резонаторы','Резонатор',[r'^резонатор\b']),
    'exhaust_flame_arrestor': ('exhaust','Пламегасители','Пламегаситель',[r'\bпламегаситель\w*']),
    'engine_oil': ('maintenance','Моторные масла','Масло моторное',[r'\bмасло\s+моторн\w*']),
    'transmission_oil': ('maintenance','Трансмиссионные масла','Масло трансмиссионное',[r'\bмасло\s+трансмиссионн\w*']),
    'oil_filter': ('maintenance','Масляные фильтры','Фильтр масляный',[r'\bфильтр\s+(?:для\s+)?(?:масла|масл\.)\b']),
    'air_filter': ('maintenance','Воздушные фильтры','Фильтр воздушный',[r'\bфильтр\s+для\s+воздуха\b']),
    'heater_core': ('climate','Радиаторы отопителя','Радиатор отопителя',[r'\bрадиатор\s+печки\b']),
    'ac_condenser': ('climate','Радиаторы кондиционера','Радиатор кондиционера',[r'\bрадиатор\s+кондиц\.\b']),
    'coolant_hose': ('climate','Патрубки охлаждения','Патрубок охлаждения',[r'\bизогнут\w*\s+патрубк\w*\b']),
    'crankshaft_gear': ('engine','Звёздочки коленвала','Звёздочка коленвала',[r'\bзв[её]здочк\w*\s+коленчат\w*\s+вала']),
    'engine_oil_pan_gasket': ('engine','Прокладки масляного поддона','Прокладка поддона',[r'\bпрокладк\w*\s+масляного\s+картера']),
    'window_regulator': ('body','Стеклоподъёмники','Стеклоподъёмник',[r'^стеклопод[ъь]емник\w*']),
    'plastic_body_clip': ('body','Клипсы кузова','Клипса кузовная',[r'^клипс\w*\b']),
    'trim_fastener': ('body','Крепёж обшивки','Клипса обшивки',[r'\bклипс\w*\s+(?:обшивки|молдинга)']),
    'body_panel': ('body','Кузовные панели','Панель кузова',[r'\bпанел\w*\s+(?:кузова|кузовн\w*)']),
    'body_moulding': ('body','Кузовные накладки','Накладка кузова',[r'\bнакладк\w*\s+.*\bкузова']),
    'engine_gasket_set': ('engine','Комплекты прокладок двигателя','Комплект прокладок двигателя',[r'\bкомплект\s+прокладок\s+(?:двигателя|гбц)']),
    'gearbox_bearing': ('transmission','Подшипники коробки передач','Подшипник КПП',[r'\bподшипник\s+коробки\s+передач']),
    'gas_strut': ('body','Газовые упоры','Упор газовый',[r'\bгазов\w*\s+упор\b']),
    'strut_spring_spacer': ('suspension','Проставки пружины','Проставка пружины',[r'\bпроставк\w*\s+пружин\w*']),
    'turbocharger_cartridge': ('engine','Картриджи турбокомпрессора','Картридж турбокомпрессора',[r'\bкартридж\s+турбокомпрессора']),
    'tool_impact_socket': ('hardware','Ударные головки','Головка ударная',[r'^головк\w*\s+ударн\w*']),
    'tool_socket': ('hardware','Торцевые головки','Головка торцевая',[r'^головк\w*\s+торцев\w*']),
    'tool_screwdriver': ('hardware','Отвёртки','Отвёртка',[r'^отв[её]ртк\w*']),
}

# Prevent regex denial-of-service in the admin control plane. Intentionally
# limited dialect: prohibit lookaround, backreferences, bounded large repeats,
# named groups and nested quantifier constructs; tested before persisting.
BAD_REGEX = re.compile(r'\(\?(?!:)|\\[1-9]|\{\d+[,}]|(?:\*|\+|\?)[+*?]|\(.*[+*].*\)[+*]')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{2,99}$')

def check_pattern(pattern: str):
    if not pattern or len(pattern)>300 or BAD_REGEX.search(pattern):
        raise ValueError('Unsupported or excessively complex regex')
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError('Invalid regex') from exc


@dataclass(frozen=True)
class RuleSnapshot:
    revision: int
    types: dict
    patterns: tuple


async def snapshot(db):
    """One snapshot per worker chunk, no ORM lazy loads during rule execution."""
    revision=await db.get(TaxonomyRevision,1)
    types={t.code:(t.category_name,t.title_stem) for t in (await db.scalars(
        select(TaxonomyType).where(TaxonomyType.active==True))).all()}
    rows=(await db.scalars(select(TaxonomyPattern).where(TaxonomyPattern.active==True)
         .order_by(TaxonomyPattern.priority.desc(),TaxonomyPattern.key))).all()
    patterns=[]
    for p in rows:
        if p.type_code not in types:continue
        patterns.append((p.key,p.type_code,re.compile(p.positive_regex,re.I),
                         re.compile(p.negative_regex,re.I) if p.negative_regex else None,
                         p.supplier,p.requires_review))
    return RuleSnapshot(revision.revision if revision else 0,types,tuple(patterns))


async def bump_revision(db,action:str,subject:str,before='',after='',origin='admin_api'):
    rev=await db.scalar(select(TaxonomyRevision).where(TaxonomyRevision.id==1).with_for_update())
    if rev is None:
        rev=TaxonomyRevision(id=1,revision=0)
        db.add(rev);await db.flush()
    rev.revision+=1;rev.updated_at=utcnow()
    db.add(TaxonomyAudit(revision=rev.revision,action=action,subject=subject,
                         before=before,after=after,origin=origin))
    return rev.revision


def type_group(code: str, name: str):
    value=(code+' '+name).lower()
    hints={
        'braking':('brake','caliper','pad','disc','abs','тормоз','суппорт','колод','brake_hose'),
        'suspension':('stabilizer','shock','spring','suspension','подвес','амортиз','сайлент','strut','ball_joint','bushing'),
        'steering':('steering','рулев','tie_rod','pitman','power_steering'),
        'transmission':('clutch','gearbox','cv_','transmission','кпп','сцеплен','шрус'),
        'electrical':('sensor','starter','alternator','battery','ignition','spark','электр','датчик','ламп','свечи'),
        'climate':('radiator','hose','coolant','heater','condenser','ac_','кондиц','отопител','охлажд'),
        'body':('wiper','body','mirror','window','door','headlight','tail_light','trim','кузов','зеркал'),
        'exhaust':('exhaust','выхлоп','глушител','гофр'),
        'maintenance':('filter','engine_oil','transmission_oil','масло','фильтр'),
        'hardware':('fastener','tool','гайк','болт','отвёрт','головк'),
    }
    for group,parts in hints.items():
        if any(part in value for part in parts):return group
    return 'engine' if any(x in value for x in ('engine','timing','piston','valve','crank','коленвал','двигател','прокладк')) else 'misc'


async def bootstrap_rulebook(db,base_types):
    """Additive, idempotent. Never alters existing operator-authored rule rows."""
    from app.modules.catalog_ext.models import Category
    groups={g.code:g for g in (await db.scalars(select(TaxonomyGroup))).all()}
    group_categories={}
    for code,name in GROUPS.items():
        if code not in groups:
            db.add(TaxonomyGroup(code=code,name=name));groups[code]=True
    await db.flush()
    categories=(await db.scalars(select(Category))).all()
    by_slug={c.slug:c for c in categories}
    by_name={c.name.casefold():c for c in categories}
    for code,name in GROUPS.items():
        slug='auto-v030-group-'+code
        if slug not in by_slug:
            c=Category(name=name,slug=slug,active=False,position=0)
            db.add(c);by_slug[slug]=c
        group_categories[code]=by_slug[slug]
    await db.flush()
    prev_types={t.code:t for t in (await db.scalars(select(TaxonomyType))).all()}
    all_types={**{v[0]:(type_group(v[0],v[1]),v[1],v[2],[]) for v in base_types.values()},**SEED_TYPES}
    for code,(group,name,stem,patterns) in all_types.items():
        if code not in prev_types:
            db.add(TaxonomyType(code=code,group_code=group,category_name=name,title_stem=stem))
        # Only new categories get a hierarchy. Existing SEO/category links stay unchanged.
        if name.casefold() not in by_name:
            c=Category(name=name,slug='auto-v030-'+code,active=False,position=0,parent_id=group_categories[group].id)
            db.add(c);by_name[name.casefold()]=c
    await db.flush()
    existing_keys=set((await db.scalars(select(TaxonomyPattern.key))).all())
    new=[]
    # Import the complete previous classifier into database-managed rules.
    # Once bootstrapped, disabling a legacy rule in the admin UI really disables it.
    for alias,(code,name,stem,expression) in base_types.items():
        key='legacy_'+alias
        if key in existing_keys:continue
        db.add(TaxonomyPattern(key=key,type_code=code,positive_regex=expression,priority=100))
        new.append(key)
    for code,(group,name,stem,patterns) in SEED_TYPES.items():
        for idx,expression in enumerate(patterns):
            key=f'seed_{code}_{idx+1}'
            if key in existing_keys:continue
            check_pattern(expression)
            db.add(TaxonomyPattern(key=key,type_code=code,positive_regex=expression,priority=150,
                                   requires_review=code in {'tool_impact_socket','tool_socket','tool_screwdriver','body_panel','plastic_body_clip','engine_oil','transmission_oil','drive_belt_tensioner','brake_caliper_repair_kit'}))
            new.append(key)
    if new or not await db.get(TaxonomyRevision,1):
        await bump_revision(db,'bootstrap','v0.30',after=f'New rules: {len(new)}',origin='system_seed')
    await db.commit()
    return {'rule_revision':(await db.get(TaxonomyRevision,1)).revision,
            'new_patterns':len(new),'types_supported':len(all_types),
            'publication_policy':'individual_manual_only','auto_publish':False}
