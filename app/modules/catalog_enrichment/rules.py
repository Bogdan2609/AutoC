"""Deterministic, conservative Russian part-title enrichment.

No guessed vehicle fitment, OE equivalence, sizes, images, or seller promises.
Category decisions use an audited static vocabulary and require a configured
Category row before they can be applied to a Product.
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass

ENGINE_VERSION = '0.28.2'  # v0.28.1: new rules; distinct fingerprint from v0.28
# Code -> (category name, canonical title stem, regex for item type).
# Order resolves specific names before generic terms.
TAXONOMY = {
    'tie_rod_end': ('Наконечники рулевых тяг', 'Наконечник рулевой тяги', r'\bнаконечник(?:и)?\s+(?:рулев(?:ой|ых)|рул\.)\s+тяг(?:и)?\b'),
    'tie_rod': ('Рулевые тяги', 'Тяга рулевая', r'\bтяг(?:а|и)\s+(?:рулев(?:ая|ой|ые)|рул\.?)(?!\w)'),
    'stabilizer_link': ('Стойки стабилизатора', 'Стойка стабилизатора', r'\bстойк(?:а|и)\s+стаб(?:илизатора|\.)?\b'),
    'control_arm': ('Рычаги подвески', 'Рычаг подвески', r'\bрычаг(?:и)?\s+подвеск(?:и|и)\b'),
    'subframe_bushing': ('Сайлентблоки', 'Сайлентблок подрамника', r'\bсайлентблок\s+подрамник(?:а)?\b'),
    'arm_bushing': ('Сайлентблоки', 'Сайлентблок рычага подвески', r'\bсайлентблок\s+рычаг(?:а)?\s+подвеск(?:и)?\b'),
    'bushing': ('Сайлентблоки', 'Сайлентблок', r'\bсайлентблок(?:и)?\b'),
    'suspension_link': ('Тяги подвески', 'Тяга подвески', r'\bтяг(?:а|и)\s+подвеск(?:и)?\b'),
    'brake_pads': ('Тормозные колодки', 'Колодки тормозные', r'\bколодк(?:и|а)\s+тормозн(?:ые|ая)\b|\bтормозн(?:ые|ая)\s+колодк(?:и|а)\b'),
    'brake_disc': ('Тормозные диски', 'Диск тормозной', r'\bдиск\s+тормозн(?:ой|ый)\b|\bтормозн(?:ой|ый)\s+диск\b'),
    'cabin_filter': ('Салонные фильтры', 'Фильтр салона', r'\bфильтр\s+(?:салон(?:а|ный)|салонный)\b'),
    'air_filter': ('Воздушные фильтры', 'Фильтр воздушный', r'\bфильтр\s+воздушн(?:ый|ого)\b'),
    'oil_filter': ('Масляные фильтры', 'Фильтр масляный', r'\bфильтр\s+маслян(?:ый|ого)\b'),
    'fuel_filter': ('Топливные фильтры', 'Фильтр топливный', r'\bфильтр\s+топливн(?:ый|ого)\b'),
    'spark_plug': ('Свечи зажигания', 'Свеча зажигания', r'\bсвеч(?:а|и)\s+зажигания\b'),
    'shock_absorber': ('Амортизаторы', 'Амортизатор', r'\bамортизатор(?:ы)?\b'),
    'wheel_bearing': ('Ступичные подшипники', 'Подшипник ступицы', r'\bподшипник\s+ступиц(?:ы)?\b'),
    'water_pump': ('Водяные насосы', 'Насос водяной', r'\b(?:помпа|насос\s+водян(?:ой|ый))\b'),
    # v0.28.1 extensions; prefer complete noun phrases over generic matches.
    'ball_joint': ('Шаровые опоры', 'Опора шаровая', r'\b(?:опор(?:а|ы)\s+шаров(?:ая|ые|ой)|шаров(?:ая|ые|ой)\s+опор(?:а|ы))\b|\bопор(?:а|ы)\s+шар\.?(?=\s|$)'),
    'idler_arm': ('Маятниковые рычаги', 'Рычаг маятниковый', r'\b(?:рычаг(?:и)?\s+маятников(?:ый|ого|ые)|маятников(?:ый|ые)\s+рычаг(?:и)?)\b|\bрычаг(?:и)?\s+маятн\.?(?=\s|$)'),
    'stabilizer_bushing': ('Втулки стабилизатора', 'Втулка стабилизатора', r'\bвтулк(?:а|и)\s+стаб(?:илизатора|\.)\b'),
    'strut_mount': ('Опоры амортизатора', 'Опора амортизатора', r'\bопор(?:а|ы)\s+(?:амортизатора|стойки\s+амортизатора)\b'),
    'wheel_hub': ('Ступицы колеса', 'Ступица колеса', r'\bступиц(?:а|ы)\s+(?:колеса|колес[ао])\b'),
    'engine_mount': ('Опоры двигателя', 'Опора двигателя', r'\bопор(?:а|ы)\s+(?:двигателя|двс)\b'),
    'timing_belt': ('Ремни ГРМ', 'Ремень ГРМ', r'\bремен(?:ь|я)\s+грм\b|\bремн(?:и|я)\s+грм\b'),
    'thermostat': ('Термостаты', 'Термостат', r'\bтермостат(?:ы)?\b'),
    'cv_joint': ('ШРУСы', 'ШРУС', r'\bшрус(?:ы|а)?\b'),
    'brake_caliper': ('Тормозные суппорты', 'Суппорт тормозной', r'\b(?:суппорт\s+тормозн(?:ой|ый)|тормозн(?:ой|ый)\s+суппорт)\b'),
}
SIDE_LEFT = re.compile(r'(?<!\w)(?:лев(?:ый|ая|ое|ую|ого|ой|\.)?|лв\.?)(?!\w)', re.I)
SIDE_RIGHT = re.compile(r'(?<!\w)(?:прав(?:ый|ая|ое|ую|ого|ой|\.)?|пр\.)(?!\w)', re.I)
# A slash is explicit evidence of shared left/right use. Two separate side
# tokens WITHOUT a slash remain ambiguous and must be sent to review.
_PAIR_LEFT = r'(?:лев(?:ый|ая|ое|ую|ого|ой|\.)?|лв\.?)'
_PAIR_RIGHT = r'(?:прав(?:ый|ая|ое|ую|ого|ой|\.)?|пр\.)'
SIDE_BOTH = re.compile(
    rf'(?<!\w)(?:{_PAIR_LEFT}\s*/\s*{_PAIR_RIGHT}|{_PAIR_RIGHT}\s*/\s*{_PAIR_LEFT})(?!\w)', re.I
)
FRONT = re.compile(r'(?<!\w)(?:передн(?:ий|яя|ее|его|ей)?|перед|пер(?:ед)?\.?)(?!\w)', re.I)
REAR = re.compile(r'(?<!\w)(?:задн(?:ий|яя|ее|его|ей)?|зад)(?!\w)', re.I)
NON_AUTO = re.compile(r'\b(?:для\s+розжига|продукт\s+питан|удобрени|канцеляр)\b', re.I)
MOJIBAKE = re.compile(r'(?:Ð[\x80-\xbf]|Ñ[\x80-\xbf]|Ã[\x80-\xbf]|�)')
BAD = re.compile(r'[<>\x00-\x08\x0b\x0e-\x1f]')
MULTISPACE = re.compile(r'\s+')
ROOTS = {
    'suspension': ('Подвеска и ходовая часть', {'stabilizer_link','control_arm','subframe_bushing','arm_bushing','bushing','suspension_link','shock_absorber','wheel_bearing','ball_joint','stabilizer_bushing','strut_mount','wheel_hub'}),
    'steering': ('Рулевое управление', {'tie_rod','tie_rod_end','idler_arm'}),
    'brakes': ('Тормозная система', {'brake_pads','brake_disc','brake_caliper'}),
    'filters': ('Фильтры', {'air_filter','oil_filter','fuel_filter','cabin_filter'}),
    'engine': ('Двигатель и расходники', {'spark_plug','water_pump','engine_mount','timing_belt','thermostat','cv_joint'}),
}
FEMININE_CODES = {'stabilizer_link','tie_rod','suspension_link','spark_plug','ball_joint','strut_mount','wheel_hub','engine_mount','stabilizer_bushing'}
PLURAL_CODES = {'brake_pads'}


def title_placement(code: str, position: str | None, side: str | None) -> str:
    if code in FEMININE_CODES:
        position = {'передний':'передняя','задний':'задняя'}.get(position,position)
        side = {'левый':'левая','правый':'правая'}.get(side,side)
    elif code in PLURAL_CODES:
        position = {'передний':'передние','задний':'задние'}.get(position,position)
        side = {'левый':'левые','правый':'правые'}.get(side,side)
    if side == 'обе стороны':
        side = 'левая/правая' if code in FEMININE_CODES else ('левые/правые' if code in PLURAL_CODES else 'левый/правый')
    return (' ' + position if position else '') + (' ' + side if side else '')


def normalize_description(raw: str) -> str:
    value = BAD.sub(' ', raw or '')
    value = re.sub(r'[|\[\]{}]+', ' ', value)
    return MULTISPACE.sub(' ', value).strip(' ;,.-')[:300]


def _find_values(description: str, pattern_a, pattern_b, a: str, b: str):
    first = bool(pattern_a.search(description))
    second = bool(pattern_b.search(description))
    if first and second:
        return None, True
    return a if first else (b if second else None), False


@dataclass(frozen=True)
class Enriched:
    title: str
    category_code: str | None
    category_name: str | None
    attributes: dict[str, str]
    score: int
    flags: tuple[str, ...]
    provenance_hash: str


def strip_repeated_identifier(description: str, brand: str, article: str) -> str:
    """Remove only *complete trailing* supplier repeats of the brand/article.

    Exact token/suffix matching is deliberate: do not remove numerical size,
    execution, brand substrings or trailing unknown qualifiers.
    """
    result = description.strip()
    identifiers = (brand.strip(), article.strip())
    for _ in range(3):
        previous = result
        for token in identifiers:
            if not token:
                continue
            # Only remove an entire terminal identifier, not a substring.
            pattern = rf'(?:^|(?<=\s)){re.escape(token)}$'
            match = re.search(pattern, result, re.I)
            if match:
                result = result[:match.start()].rstrip(' ,;.-')
                break
        if result == previous:
            break
    return result


def suggest(brand: str, article: str, description: str, extra_descriptions=(),
            conflict_count: int = 1) -> Enriched:
    """Compute a proposal; never mutate DB or auto-publish a product.

    Multiple divergent source descriptions require review even if classification
    looks plausible, since identity and part execution could be ambiguous.
    """
    clean = strip_repeated_identifier(normalize_description(description), brand, article)
    extras = [normalize_description(s) for s in extra_descriptions if normalize_description(s)]
    flags: list[str] = []
    if not clean:
        flags.append('missing_description')
    if MOJIBAKE.search(description or '') or MOJIBAKE.search(brand):
        flags.append('encoding_suspect')
    if NON_AUTO.search(clean):
        flags.append('non_automotive_review')
    if conflict_count > 1 or len({x.casefold() for x in [clean,*extras] if x}) > 1:
        flags.append('conflicting_sources')

    code = None
    for key, (_, _, pattern) in TAXONOMY.items():
        if re.search(pattern, clean, re.I):
            code = key
            break
    if code is None:
        flags.append('unclassified')
        if len(clean) < 5 and clean:
            flags.append('missing_description')
    attrs = {}
    # Whole slash-delimited side pair: explicit dual-side source notation.
    # A free-standing mix ("лев ... прав") remains a conflict; never infer fitment.
    side_pairs = list(SIDE_BOTH.finditer(clean))
    side_remainder = SIDE_BOTH.sub(' ', clean)
    if len(side_pairs) == 1 and not (SIDE_LEFT.search(side_remainder) or SIDE_RIGHT.search(side_remainder)):
        side, side_conflict = 'обе стороны', False
    else:
        side, side_conflict = _find_values(clean, SIDE_LEFT, SIDE_RIGHT, 'левый', 'правый')
    position, pos_conflict = _find_values(clean, FRONT, REAR, 'передний', 'задний')
    if side_conflict:
        flags.append('side_conflict')
    if pos_conflict:
        flags.append('position_conflict')
    if side:
        attrs['installation_side'] = side
    if position:
        attrs['installation_position'] = position

    # Do not lose supplier-supplied qualifiers. For well-understood names we
    # generate a concise canonical title only if description consists solely
    # of the matched category and safe placement qualifiers; otherwise the
    # cleaned description is retained rather than silently dropping facts.
    if code:
        category_name, stem, pattern = TAXONOMY[code]
        residual = re.sub(pattern, ' ', clean, count=1, flags=re.I)
        residual = SIDE_BOTH.sub(' ', residual)
        residual = SIDE_LEFT.sub(' ', residual)
        residual = SIDE_RIGHT.sub(' ', residual)
        residual = FRONT.sub(' ', residual)
        residual = REAR.sub(' ', residual)
        residual = MULTISPACE.sub(' ', residual).strip(' ,;-.')
        if residual:
            # Unknown residual may encode an execution/kit: never guess its meaning.
            flags.append('unparsed_qualifiers')
        base = stem if not residual else clean
        # Placement modifiers appended only after full normalization, avoiding
        # duplicating the same source words for residual-preserving titles.
        if not residual:
            base += title_placement(code,position,side)
    else:
        category_name = None
        base = clean
    base = base.strip()
    suffix = f'{brand.strip()} {article.strip()}'
    if not base:
        base = suffix
    elif suffix.casefold() not in base.casefold():
        base += ' ' + suffix
    # If overlength, preserve the identifying brand+article suffix.
    if len(base) > 300:
        base = base[:max(0, 299-len(suffix))].rstrip() + ' ' + suffix
    score = max(0, min(100, 92 if code else 40) - 8*len(flags))
    normalized_sources = '|'.join(sorted({x.casefold() for x in [clean, *extras] if x}))
    digest = hashlib.sha256(f'{ENGINE_VERSION}|{brand}|{article}|{normalized_sources}|{conflict_count}'.encode('utf-8')).hexdigest()
    return Enriched(base,code,category_name,attrs,score,tuple(sorted(set(flags))),digest)
