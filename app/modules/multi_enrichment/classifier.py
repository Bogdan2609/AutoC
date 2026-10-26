"""Conservative, deterministic evidence reconciliation, not vehicle fitment inference.

Each supplier counts once. Text differences by themselves are NOT conflicts.
Unrecognized technical qualifiers remain in immutable evidence, never invented as facts.
"""
from __future__ import annotations
from .semantic_guard import detect_head, refine_match, source_checks
import hashlib
import json
import re
from dataclasses import dataclass
from app.modules.catalog_enrichment.rules import (
    TAXONOMY as LEGACY, SIDE_BOTH, SIDE_LEFT, SIDE_RIGHT, FRONT, REAR,
    NON_AUTO, MOJIBAKE, normalize_description, title_placement, strip_repeated_identifier,
)

RULE_VERSION = '0.30.0'
# New types ranked before generic old expressions; no broad "стойка" or "насос" guessing.
EXTENDED = {
    'wiper_linkage': ('Трапеции стеклоочистителя', 'Трапеция стеклоочистителя', r'\bтрапец(?:ия|ии)\s+стеклооч(?:истителя|\.)'),
    'caliper_guide': ('Направляющие суппорта', 'Направляющая суппорта', r'\b(?:втулк(?:а|и)\s+)?направляющ(?:ая|ие|ей|ую)\s+суппорта|\bвтулк(?:а|и)\s+направляющ(?:ая|ие)\s+суппорта'),
    'exhaust_flex': ('Гофры выхлопной системы', 'Гофра выхлопной системы', r'\bвиброкомпенсатор\s+вых|\bгофр(?:а|ы)\s+(?:выхлоп|вых\.)'),
    'steering_pitman_arm': ('Рулевые сошки', 'Сошка рулевая', r'\bсошк(?:а|и)\s+рулев'),
    'valve_guide': ('Направляющие клапанов', 'Направляющая клапана', r'\bвтулк(?:а|и)\s+направляющ.*клапана|\bнаправляющ(?:ая|ие)\s+клапан'),
    'clutch_release_bearing': ('Выжимные подшипники', 'Муфта выключения сцепления', r'\bмуфта\s+выкл\.?\s*сцеп|\bподшипник\s+выжимн'),
    'drive_belt': ('Приводные ремни', 'Ремень приводной', r'\bремен(?:ь|я)\s+приводн|\bремн(?:и|я)\s+приводн'),
    'spark_wire': ('Высоковольтные провода', 'Провод зажигания', r'\b(?:провод(?:а)?\s+(?:зажигания|высоковольтн))'),
    'ignition_coil': ('Катушки зажигания', 'Катушка зажигания', r'\bкатушк(?:а|и)\s+зажигания'),
    'starter': ('Стартеры', 'Стартер', r'\bстартер(?:ы|а)?\b'),
    'alternator': ('Генераторы', 'Генератор', r'\bгенератор(?:ы|а)?\b'),
    'battery': ('Аккумуляторы', 'Аккумулятор', r'\bаккумулятор(?:ы|а)?\b|\bакб\b'),
    'timing_chain': ('Цепи ГРМ', 'Цепь ГРМ', r'\bцеп(?:ь|и)\s+грм'),
    'clutch_disc': ('Диски сцепления', 'Диск сцепления', r'\bдиск\s+сцепления'),
    'clutch_kit': ('Комплекты сцепления', 'Комплект сцепления', r'\bкомплект\s+сцепления'),
    'gearbox_mount': ('Опоры КПП', 'Опора КПП', r'\bопор(?:а|ы)\s+(?:кпп|коробки\s+передач)'),
    'oil_pan': ('Поддоны двигателя', 'Поддон двигателя', r'\bподдон\s+(?:двигателя|маслян)'),
    'radiator': ('Радиаторы охлаждения', 'Радиатор охлаждения', r'\bрадиатор\s+(?:охлаждения|двигателя)'),
    'coolant_hose': ('Патрубки охлаждения', 'Патрубок охлаждения', r'\bпатрубок\s+(?:радиатора|охлаждения)'),
    'brake_hose': ('Тормозные шланги', 'Шланг тормозной', r'\bшланг\s+тормозн'),
    'brake_cylinder': ('Тормозные цилиндры', 'Цилиндр тормозной', r'\bцилиндр\s+тормозн'),
    'abs_sensor': ('Датчики ABS', 'Датчик ABS', r'\bдатчик\s+abs\b|\bдатчик\s+абс\b'),
    'oxygen_sensor': ('Датчики кислорода', 'Датчик кислорода', r'\bдатчик\s+кислорода|\bлямбда.?зонд'),
    'temperature_sensor': ('Датчики температуры', 'Датчик температуры', r'\bдатчик\s+температуры'),
    'fuel_pump': ('Топливные насосы', 'Насос топливный', r'\bнасос\s+топливн|\bбензонасос'),
    'fuel_injector': ('Форсунки', 'Форсунка', r'\bфорсунк(?:а|и)\b'),
    'body_mirror': ('Зеркала заднего вида', 'Зеркало заднего вида', r'\bзеркал(?:о|а)\s+(?:заднего\s+вида|боков)'),
    'headlight': ('Фары', 'Фара', r'\bфар(?:а|ы)\s+(?:передн|головн)|\bфара\b'),
    'tail_light': ('Задние фонари', 'Фонарь задний', r'\bфонар(?:ь|и)\s+задн'),
    'wiper_blade': ('Щётки стеклоочистителя', 'Щётка стеклоочистителя', r'\bщ[её]тк(?:а|и)\s+стеклооч'),
    'strut': ('Стойки амортизатора', 'Стойка амортизатора', r'\bстойк(?:а|и)\s+амортизатора'),
    'wheel_bearing_qualified': ('Ступичные подшипники', 'Подшипник ступицы', r'\bподшипник\s+(?:передней|задней)\s+ступиц[ыие]'),
    'wheel_seal': ('Сальники ступицы', 'Сальник ступицы', r'\bсальник\s+ступиц'),
    'cv_boot': ('Пыльники ШРУС', 'Пыльник ШРУС', r'\bпыльник\s+шрус'),
    'engine_gasket': ('Прокладки двигателя', 'Прокладка двигателя', r'\bпрокладк(?:а|и)\s+(?:гбц|головки\s+блока|двигателя)'),
    'parking_brake_cable': ('Тросы ручного тормоза', 'Трос стояночного тормоза', r'\bтрос\s+(?:стояночного|ручного)\s+тормоза'),
    'stabilizer_link_alias': ('Стойки стабилизатора', 'Стойка стабилизатора', r'\bтяг(?:а|и)\s+стаб(?:илизатора|ил\.?)(?=\s|$)'),
}
# v0.29.1: targeted, category-specific rules from the actual supplier review sample.
# Ambiguous nouns are anchored at the beginning: a kit containing nuts is not
# itself a nut, and a control arm with a bushing is not always a bushing.
REFINED = {
    'engine_mount_cushion': ('Опоры двигателя', 'Опора двигателя', r'\bподушк(?:а|и)\s+(?:двигателя|двс|двиг\.)\b'),
    'oxygen_sensor_short': ('Датчики кислорода', 'Датчик кислорода', r'\bдатчик\s+кисл\.?(?=\s|$)|\bдатчик\s+(?:лямбда|o2)\b'),
    'brake_pads_short': ('Тормозные колодки', 'Колодки тормозные', r'\bколодк(?:и|а)\s+торм\.?(?=\s|$)|\bколодк(?:и|а)\s+тормозные\b|\bколодк(?:и|а)\s+(?:дисков|барабан)\w*'),
    'brake_pads_bare': ('Тормозные колодки', 'Колодки тормозные', r'^колодк(?:и|а)(?=\s|$)'),
    'brake_caliper_short': ('Тормозные суппорты', 'Суппорт тормозной', r'\bсуппорт\s+торм\.?(?=\s|$)|\bсуппорт\s+тормозного\b'),
    'stabilizer_bushing_forward': ('Втулки стабилизатора', 'Втулка стабилизатора', r'\bвтулк(?:а|и)\s+(?:передн\w*\s+|задн\w*\s+)?стаб(?:илизатора|ил\.|\.)(?=\s|$)'),
    'stabilizer_bushing_reverse': ('Втулки стабилизатора', 'Втулка стабилизатора', r'\bстабилизатора\s+втулк(?:а|и)\b'),
    'strut_adjectival': ('Стойки амортизатора', 'Стойка амортизатора', r'\bстойк(?:а|и)\s+амортизационн\w*\b'),
    'turbocharger_cartridge': ('Картриджи турбокомпрессора', 'Картридж турбокомпрессора', r'\bкартридж(?:и)?\s+турбо(?:компрессора|нагнетателя)\b'),
    'suspension_spring': ('Пружины подвески', 'Пружина подвески', r'\bпружин(?:а|ы)\s+подвеск(?:и|и)\b|\bпружин(?:а|ы)\s+(?:передн|задн)\w*\s+подвеск'),
    'automotive_lamp': ('Автомобильные лампы', 'Лампа автомобильная', r'^ламп(?:а|ы)\s+(?:светодиодн|галогенн|ксенонов|накаливания|автомобильн)\w*\b'),
    'fastener_nut': ('Гайки', 'Гайка', r'^гайк(?:а|и)(?=\s|$)'),
    'fastener_bolt': ('Болты', 'Болт', r'^болт(?:ы|а)?(?=\s|$)'),
    'suspension_frame_bushing': ('Сайлентблоки', 'Сайлентблок рамы', r'\bсайлентблок(?:и)?\s+(?:рам(?:ы|а)|кузова)(?=\s|$)'),
    'intake_manifold_gasket': ('Прокладки впускного коллектора', 'Прокладка впускного коллектора', r'\bпрокладк(?:а|и)\s+(?:коллект\.?|коллектора)\s*(?:впускн\w*|вп\.)\b|\bпрокладк(?:а|и)\s+впускного\s+коллектора\b'),
    'exhaust_manifold_gasket': ('Прокладки выпускного коллектора', 'Прокладка выпускного коллектора', r'\bпрокладк(?:а|и)\s+(?:коллект\.?|коллектора)\s*(?:выпускн\w*|вып\.)\b|\bпрокладк(?:а|и)\s+выпускного\s+коллектора\b'),
    'alternator_belt': ('Приводные ремни', 'Ремень приводной', r'\bремен(?:ь|я)\s+(?:генератора|навесного\s+оборудования)\b'),
    'cabin_filter_reverse': ('Салонные фильтры', 'Фильтр салона', r'\bсалонн\w*\s+фильтр\b'),
    'engine_oil_filter_short': ('Масляные фильтры', 'Фильтр масляный', r'\bфильтр\s+масл\.?(?=\s|$)'),
    'engine_air_filter_short': ('Воздушные фильтры', 'Фильтр воздушный', r'\bфильтр\s+возд\.?(?=\s|$)'),
    'spark_plug_short': ('Свечи зажигания', 'Свеча зажигания', r'\bсвеч(?:а|и)\s+заж\.?(?=\s|$)'),
    'stabilizer_link_short': ('Стойки стабилизатора', 'Стойка стабилизатора', r'\bтяг(?:а|и)\s+стабил\.?(?=\s|$)'),
    'brake_disc_short': ('Тормозные диски', 'Диск тормозной', r'\bдиск\s+торм\.?(?=\s|$)'),
    'gearbox_mount_cushion': ('Опоры КПП', 'Опора КПП', r'\bподушк(?:а|и)\s+(?:кпп|коробки\s+передач)\b'),
}
# Common full phrases in a reproducible PART-KOM sample. More generic words
# ("ролик", "фильтр", "радиатор", "стойка") remain deliberately unclassified.
COVERAGE = {
    'brake_caliper_repair_kit': ('Ремкомплекты тормозных суппортов', 'Ремкомплект тормозного суппорта', r'^ремкомплект\s+(?:тормозного\s+суппорта|суппорта\s+тормозн\w*)\b'),
    'drive_belt_poly_v': ('Приводные ремни', 'Ремень приводной', r'^ремен(?:ь|я)\s+поликлинов\w*\b|^поликлинов\w*\s+ремен[ья]\b'),
    'steering_end_short': ('Наконечники рулевых тяг', 'Наконечник рулевой тяги', r'^наконечник\s+рулевой\b|^наконечник\s+р/т\b'),
    'valve_cover_gasket': ('Прокладки клапанной крышки', 'Прокладка клапанной крышки', r'\b(?:прокладк(?:а|и)|комплект\s+прокладок)\s+клапанной\s+крышк'),
    'power_steering_pump': ('Насосы ГУР', 'Насос гидроусилителя руля', r'\bнасос\s+(?:гидроусилителя\s+руля|гур)\b'),
    'belt_pulley': ('Ролики приводных ремней', 'Ролик приводного ремня', r'^ролик\s+(?:привод\.?|приводного|обводной)\s*ремен|^ролик\s+обводной\s+приводного\s+ремня\b'),
    'belt_tensioner': ('Ролики-натяжители', 'Ролик натяжной', r'\b(?:ролик\s+натяжной|натяжной\s+ролик)\b'),
    'blower_motor': ('Вентиляторы отопителя', 'Вентилятор отопителя', r'\b(?:э/вентилятор|вентилятор)\s+отоп(?:ителя|\.)\b'),
    'steering_boot': ('Пыльники рулевых реек', 'Пыльник рулевой рейки', r'\bпыльник\s+рулев(?:ой|\.)\s+рейк'),
    'brake_caliper_piston': ('Поршни тормозных суппортов', 'Поршень тормозного суппорта', r'\bпоршень\s+(?:тормозного\s+суппорта|суппорта\s+тормозного)\b'),
    'wheel_hub_short': ('Ступицы колеса', 'Ступица колеса', r'^ступиц(?:а|ы)\b'),
    'strut_bump_stop': ('Отбойники амортизаторов', 'Отбойник амортизатора', r'\bотбойник\s+(?:переднего\s+|заднего\s+)?амортизатора\b'),
    'sealing_o_ring': ('Уплотнительные кольца', 'Кольцо уплотнительное', r'^кольц(?:о|а)\s+уплотнительн\w*\b'),
    'ac_condenser': ('Радиаторы кондиционера', 'Радиатор кондиционера', r'^радиатор\s+(?:кондиц(?:ионера|\.)|конденсора)\b'),
    'radiator_short': ('Радиаторы охлаждения', 'Радиатор охлаждения', r'^радиатор\s+охл\.(?=\s|$)'),
    'control_arm_short': ('Рычаги подвески', 'Рычаг подвески', r'^рычаг\s+(?:передн\w*|задн\w*|пер\.)\s*(?:подв\.?|верхн|нижн)?\b'),
    'automatic_transmission_filter': ('Фильтры АКПП', 'Фильтр АКПП', r'^фильтр\s+(?:акпп|трансмиссионн\w*|коробки\s+передач)\b'),
    'caliper_guide_kit': ('Направляющие суппорта', 'Направляющая суппорта', r'^комплект\s+направляющих\s+(?:тормозного\s+)?суппорта\b'),
    'pad_mounting_kit': ('Комплекты крепления тормозных колодок', 'Комплект крепления тормозных колодок', r'^комплект\s+установочный\s+тормозных\s+колодок\b'),
    'strut_dust_boot': ('Пыльники амортизаторов', 'Пыльник амортизатора', r'\bпыльник\s+(?:переднего\s+|заднего\s+)?амортизатора\b'),
    'voltage_regulator': ('Регуляторы напряжения', 'Регулятор напряжения генератора', r'\bрегулятор\s+напряжения\b'),
    'wheel_bearing_short': ('Ступичные подшипники', 'Подшипник ступицы', r'^подшипник\s+ступичн\w*\b'),
    'glow_plug': ('Свечи накаливания', 'Свеча накаливания', r'\bсвеч(?:а|и)\s+накаливания\b'),
    'brake_pad_wear_sensor': ('Датчики износа тормозных колодок', 'Датчик износа колодок', r'^датчик\s+износа\s+тормозных\s+колодок\b'),
    'steering_rack': ('Рулевые рейки', 'Рулевая рейка', r'\b(?:рулевая\s+рейка|рейка\s+рулевая)\b'),
    'oil_pressure_sensor': ('Датчики давления масла', 'Датчик давления масла', r'\bдатчик[,]?(?:\s+давлени[ея]\s+масла|\s+давления\s+масла)\b'),
    'engine_oil_pump': ('Масляные насосы', 'Насос масляный', r'\bнасос\s+маслян\w*\b'),
    'timing_belt_roller': ('Ролики ГРМ', 'Ролик ГРМ', r'\bролик\s+грм\b|\bролик\s+(?:привода\s+)?грм\b|\b(?:ролик\s+натяжной|натяжной\s+ролик)\s+(?:привода\s+)?ремня\s+грм\b|\bнатяжной\s+ролик\s+(?:привода\s+)?грм\b'),
    'coolant_reservoir': ('Расширительные бачки', 'Бачок расширительный', r'\bбачок\s+расширительн\w*\b'),
    'ac_compressor': ('Компрессоры кондиционера', 'Компрессор кондиционера', r'\bкомпрессор\s+кондиц(?:ионера|\.)\b'),
    'rear_crankshaft_seal': ('Сальники коленвала', 'Сальник коленвала', r'\bсальник\s+коленвала\b'),
    'heater_core': ('Радиаторы отопителя', 'Радиатор отопителя', r'^радиатор\s+отоп(?:ителя|\.)\b'),
    'door_lock': ('Замки дверей', 'Замок двери', r'^замок\s+двери\b'),
    'valve_intake': ('Впускные клапаны', 'Клапан впускной', r'^клапан\s+впускной\b'),
    'valve_exhaust': ('Выпускные клапаны', 'Клапан выпускной', r'^клапан\s+выпускной\b'),
    'crankshaft_pulley': ('Шкивы коленвала', 'Шкив коленвала', r'^шкив\s+коленвала\b'),
    'oil_filter_generic_short': ('Воздушные фильтры', 'Фильтр воздушный', r'^фильтр\s+для\s+.+\(возд\.\)'),
}

# The legacy and v0.29 aliases remain the *same* canonical category, not
# extra product types. Do not create duplicate categories for synonyms.
ALIASES = {
    'stabilizer_link_alias':'stabilizer_link', 'wheel_bearing_qualified':'wheel_bearing',
    'engine_mount_cushion':'engine_mount', 'oxygen_sensor_short':'oxygen_sensor',
    'brake_pads_short':'brake_pads', 'brake_pads_bare':'brake_pads',
    'brake_caliper_short':'brake_caliper', 'stabilizer_bushing_forward':'stabilizer_bushing',
    'stabilizer_bushing_reverse':'stabilizer_bushing', 'strut_adjectival':'strut',
    'engine_oil_filter_short':'oil_filter', 'engine_air_filter_short':'air_filter',
    'cabin_filter_reverse':'cabin_filter', 'spark_plug_short':'spark_plug',
    'stabilizer_link_short':'stabilizer_link', 'brake_disc_short':'brake_disc',
    'alternator_belt':'drive_belt', 'gearbox_mount_cushion':'gearbox_mount',
    'drive_belt_poly_v':'drive_belt','steering_end_short':'tie_rod_end',
    'radiator_short':'radiator','control_arm_short':'control_arm',
    'wheel_hub_short':'wheel_hub','wheel_bearing_short':'wheel_bearing',
    'caliper_guide_kit':'caliper_guide','oil_filter_generic_short':'air_filter',
}
RULES = {**COVERAGE, **REFINED, **EXTENDED, **LEGACY}
TYPE_MAP = {k:(ALIASES.get(k,k), name, stem, regex) for k,(name,stem,regex) in RULES.items()}
# Compile once. A long batch can invoke these tens of millions of times.
COMPILED_RULES = [(canon,re.compile(pattern,re.I)) for canon,_,_,pattern in TYPE_MAP.values()]
# Suppress generic category evidence when the description itself names a
# specific item type. Never suppress genuinely different specific types.
SUPERTYPES = {'bushing': {'arm_bushing','subframe_bushing','suspension_frame_bushing'},
              'shock_absorber': {'strut'},
              'strut': {'strut_mount'}, 'cv_joint': {'cv_boot','fastener_bolt'},
              'belt_tensioner': {'timing_belt_roller'},
              'timing_belt': {'timing_belt_roller'},
              'fuel_injector': {'sealing_o_ring'},
              'oxygen_sensor': {'fastener_nut'},
              'engine_mount': {'bushing'},
              'ball_joint': {'control_arm'},
              'plastic_body_clip': {'trim_fastener'},
              'oil_pan': {'engine_oil_pan_gasket'},
              'timing_belt': {'timing_belt_tensioner','timing_belt_roller'},
              'drive_belt_tensioner': {'timing_belt_tensioner','timing_belt_roller'}}

# Accessory / packaging claims are evidence only; they are NOT automatically
# written to Product attributes or silently omitted from the title.
COUNT_UNITS = re.compile(r'(?<!\w)(\d{1,3})\s*(?:шт\.?|штук\b)',re.I)
COUNT_NUTS = re.compile(r'(?<!\w)(\d{1,3})\s*(?:шт\.?\s*)?гайк\w*',re.I)
COUNT_WASHERS = re.compile(r'(?<!\w)(\d{1,3})\s*(?:шт\.?\s*)?шайб\w*',re.I)
KIT = re.compile(r'\b(?:комплект|компл\.?|к-кт|с\s+гайками|с\s+шайбами)\b|\+\s*\d*\s*(?:гайк|шайб)|(?<!\w)\d+\s*шт\b',re.I)
TECHNICAL_EXECUTION = re.compile(r'\b(?:abs\s*\(|магнитов|hydra|wire\s+mesh)\b',re.I)

def extract_qualifiers(clean: str) -> dict:
    def numbers(pattern):
        found={int(m.group(1)) for m in pattern.finditer(clean)}
        return next(iter(found)) if len(found)==1 else None
    return {
        'kit_mentioned':bool(KIT.search(clean)),
        'pack_units':numbers(COUNT_UNITS),
        'nut_count':numbers(COUNT_NUTS),
        'washer_count':numbers(COUNT_WASHERS),
        'technical_execution_mentioned':bool(TECHNICAL_EXECUTION.search(clean)),
    }


FEMININE_NEW = {'suspension_spring','automotive_lamp','intake_manifold_gasket',
                'exhaust_manifold_gasket','fastener_nut','valve_cover_gasket',
                'valve_intake','valve_exhaust','wheel_hub','strut',
                'wiper_linkage','valve_guide','caliper_guide','sealing_o_ring',
                'valve_cover_gasket','engine_oil_pan_gasket','crankshaft_gear',
                'strut_dust_boot','coolant_reservoir','heater_core','steering_rack'}

def safe_placement(code: str, position: str | None, side: str | None) -> str:
    if code not in FEMININE_NEW:
        return title_placement(code,position,side)
    position={'передний':'передняя','задний':'задняя'}.get(position,position)
    side={'левый':'левая','правый':'правая',
          'обе стороны':'левая/правая'}.get(side,side)
    return (' '+position if position else '')+(' '+side if side else '')


def classify_source(description: str, rulebook=None, supplier: str | None=None) -> dict:
    clean = normalize_description(description)
    hits=[]
    flags=[]
    if rulebook is None:
        # Offline direct API remains usable without a DB. Production always passes
        # a revision-pinned database snapshot; no hardcoded fallback in that path.
        matches = {canonical for canonical, pattern in COMPILED_RULES if pattern.search(clean)}
    else:
        matches=set()
        for key,code,positive,negative,scope,review in rulebook.patterns:
            if scope not in ('any',supplier):continue
            if positive.search(clean) and not (negative and negative.search(clean)):
                matches.add(code);hits.append(key)
                if review:flags.append('classification_rule_requires_review')

    # Independent noun-head check, separate from keyword taxonomy patterns.
    # A verified subcomponent outranks mentions of its surrounding assembly.
    # Never select a type that is not active in the DB rulebook.
    head=detect_head(clean)
    if rulebook is not None:
        matches,conflict=refine_match(matches,head,set(rulebook.types))
        if conflict:flags.append('physical_part_type_mismatch')
    for generic, specific in SUPERTYPES.items():
        if matches.intersection(specific):
            matches.discard(generic)
    codes=sorted(matches)
    # An additional detected part type is never silently chosen arbitrarily.
    code=codes[0] if len(codes)==1 else None
    if len(codes)>1:flags.append('multiple_item_types')
    if not codes:flags.append('unclassified')
    if NON_AUTO.search(clean):flags.append('non_automotive_review')
    if MOJIBAKE.search(description):flags.append('encoding_suspect')
    sides = SIDE_BOTH.sub(' ', clean)
    pair = bool(SIDE_BOTH.search(clean))
    left = bool(SIDE_LEFT.search(sides));right=bool(SIDE_RIGHT.search(sides))
    if pair and not left and not right:side='обе стороны'
    elif left and right or pair and (left or right):
        side=None;flags.append('ambiguous_side')
    else:side='левый' if left else 'правый' if right else None
    front=bool(FRONT.search(clean));rear=bool(REAR.search(clean))
    if front and rear:
        position=None;flags.append('ambiguous_position')
    else:position='передний' if front else 'задний' if rear else None
    attrs={}
    if side:attrs['installation_side']=side
    if position:attrs['installation_position']=position
    if re.search(r'\b(?:снят[аоы]?\s+с\s+производства|не\s+производится|discontinued)\b',clean,re.I):
        flags.append('discontinued')
    qualifiers=extract_qualifiers(clean)
    if qualifiers['kit_mentioned'] or qualifiers['technical_execution_mentioned']:
        flags.append('execution_or_kit_review')
    # Brake system purpose is not explicit in 'Колодки передние' alone.
    if code=='brake_pads' and not re.search(r'торм|диск|барабан',clean,re.I):
        flags.append('generic_pad_type_review')
    # Generic fasteners have no confirmed subsystem or fitment.
    if code in ('engine_oil','transmission_oil','automotive_lamp'):
        flags.append('execution_or_kit_review')
    if code in ('fastener_nut','fastener_bolt'):
        flags.append('generic_fastener_review')
    return {'description':clean,'category_code':code,'type_candidates':codes,
            'matched_rules':hits,
            'attributes':attrs,'qualifiers':qualifiers,'flags':sorted(set(flags))}


@dataclass(frozen=True)
class Verdict:
    title: str
    category_code: str|None
    category_name: str|None
    attributes: dict
    quality: int
    conflicts: list[str]
    evidence: dict
    fingerprint: str


def reconcile(brand: str, article: str, sources: list[dict], rulebook=None) -> Verdict:
    """Sources are one representative row per supplier, selected by indexed SQL.

    Missing evidence is not a conflict; sources must not vote by warehouse counts.
    """
    ordered=sorted(sources,key=lambda s:(s['supplier'],s['batch_id']))
    parsed=[(s,classify_source(s['description'],rulebook,s.get('supplier'))) for s in ordered]
    errors=set()
    if not parsed:errors.add('no_source_evidence')
    if len({s['supplier'] for s,_ in parsed})!=len(parsed):errors.add('duplicate_supplier_evidence')
    for s,p in parsed:
        errors.update(p['flags'])
        if s.get('description_variants',1)>1 or s.get('sku_variants',1)>1:
            errors.add('identity_variants_review')
    types={p['category_code'] for _,p in parsed if p['category_code']}
    if len(types)>1:errors.add('source_category_conflict')
    if not types:errors.add('unclassified')
    if any(not p['category_code'] for _,p in parsed) and types:
        errors.add('source_type_incomplete')
    category_code=next(iter(types)) if len(types)==1 else None
    # Independently verify raw supplier noun-heads, including where a taxonomy
    # regex matches an assembly rather than the physical part being sold.
    semantic=source_checks(category_code,[s.get('description','') for s,_ in parsed])
    errors.update(semantic['issues'])
    # These are explicitly observed quantities, not inferences from missing data.
    # Different explicit quantities across suppliers are blocking conflicts.
    qualifier_claims={}
    for key in ('pack_units','nut_count','washer_count'):
        provided={s['supplier']:p['qualifiers'][key] for s,p in parsed
                  if p['qualifiers'][key] is not None}
        qualifier_claims[key]=provided
        if len(set(provided.values()))>1:
            errors.add(f'source_{key}_conflict')
    attrs={};claims={}
    for key in ('installation_side','installation_position'):
        claim={s['supplier']:p['attributes'][key] for s,p in parsed if key in p['attributes']}
        claims[key]=claim
        if len(set(claim.values()))>1:errors.add(f'source_{key}_conflict')
        elif claim:attrs[key]=next(iter(claim.values()))
    code_to_desc={v[0]:v[1:3] for v in TYPE_MAP.values()}
    if rulebook is not None:code_to_desc.update(rulebook.types)
    category_name=code_to_desc[category_code][0] if category_code in code_to_desc else None
    stem=code_to_desc[category_code][1] if category_code in code_to_desc else ''
    normalized=strip_repeated_identifier(stem,brand,article)
    title = (normalized+safe_placement(category_code or '',attrs.get('installation_position'),attrs.get('installation_side'))+f' {brand} {article}').strip() if stem else f'{brand} {article}'
    title = title[:300] if len(title)<=300 else (title[:280].rstrip()+f' {article}')[:300]
    if len(brand.strip())<2 or len(article.strip())<2:errors.add('ambiguous_identity')
    # Distinguish one-source from corroborated sources; semantic coverage is
    # a quality signal, NEVER a replacement for the hard validation checks.
    base_score=(95 if len({s['supplier'] for s,_ in parsed})>=2 else 89) if category_code else 35
    if semantic['coverage']==len(parsed) and parsed:base_score+=2
    score=max(0,min(100,base_score-min(80,8*len(errors))))
    evidence={'taxonomy_revision':rulebook.revision if rulebook is not None else None,'sources':[{**s,'extracted':p} for s,p in parsed], 'attribute_claims':claims, 'qualifier_claims':qualifier_claims,
              'source_count':len(parsed),'supplier_count':len({s['supplier'] for s,_ in parsed}),
              'description_texts_differ':len({p['description'].casefold() for _,p in parsed})>1,
              'vehicle_fitment_inferred':False,'semantic_head_check':semantic}
    digest=hashlib.sha256(json.dumps({'version':RULE_VERSION,'brand':brand,'article':article,
                            'evidence':evidence},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return Verdict(title,category_code,category_name,attrs,score,sorted(errors),evidence,digest)
