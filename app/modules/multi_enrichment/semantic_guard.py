"""v0.31.1 independent *physical-part-head* check against supplier descriptions.

This is intentionally not a fitment oracle. Unrecognized text is not proof of
correctness. Specific, supplier-confirmed noun phrases can veto keyword-only
classifications. A general bushing class is compatible with an arm bushing but
is reported as less precise, not as a different physical part.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Head:
    code: str
    evidence: str

# More specific noun-heads precede general names. None of these patterns infer
# a vehicle, material, cross-reference, or installation applicability.
HEAD_RULES = (
    ('timing_chain_guide', r'\b(?:успокоител\w*|башмак\w*|направляющ\w*)\s+цеп\w*(?:\s+(?:грм|газораспредел\w*))?\b'),
    ('timing_chain_tensioner', r'\bнатяжител\w*\s+цеп\w*(?:\s+грм)?\b'),
    ('timing_belt_roller', r'\b(?:ролик\s+натяжител\w*|ролик\s+натяжн\w*|натяжн\w*\s+ролик)\s+ремн\w*\s+грм\b'),
    ('belt_tensioner', r'\b(?:ролик\s+натяжител\w*|ролик\s+натяжн\w*|натяжн\w*\s+ролик)\s+(?:приводн\w*\s+)?ремн\w*\b'),
    ('brake_caliper_piston', r'\bпоршен\w*\s+(?:тормозн\w*\s+)?суппорт\w*\b'),
    ('brake_caliper_repair_kit', r'\bрем\s*комплект\w*\s+(?:тормозн\w*\s+)?суппорт\w*\b'),
    ('arm_bushing', r'\b(?:сайлентблок\w*|втулк\w*)\s+(?:передн\w*\s+|задн\w*\s+)?рычаг\w*\b'),
    ('suspension_frame_bushing', r'\bсайлентблок\w*\s+(?:подрамник\w*|рам\w*)\b'),
    ('stabilizer_bushing', r'\bвтулк\w*\s+стабилизатор\w*\b'),
    ('cv_boot', r'\bпыльник\w*\s+(?:внутренн\w*\s+|наружн\w*\s+)?(?:шрус\w*|шарнир\w*\s+равн\w*\s+углов\w*)\b'),
    ('strut_mount', r'\bопор\w*\s+(?:(?:передн\w*|задн\w*)\s+)?(?:стойк\w*\s+амортизатор\w*|амортизатор\w*\s+стойк\w*)\b'),
    ('wheel_bearing', r'\bподшипник\w*\s+(?:ступиц\w*|ступичн\w*)\b'),
    ('wheel_hub', r'\bступиц\w*\s+(?:колес\w*|в\s+сборе)\b'),
    ('timing_chain', r'\bцеп\w*\s+(?:грм|газораспредел\w*)\b'),
    ('drive_belt_tensioner', r'\bнатяжител\w*\s+(?:приводн\w*\s+)?ремн\w*\b'),
    ('brake_caliper', r'\bсуппорт\w*\s+тормозн\w*\b'),
    ('bushing', r'\bсайлентблок\w*\b'),
    ('stabilizer_link', r'\b(?:стойк\w*|тяг\w*)\s+стабил(?:изатор\w*|\.?)\b'),
    ('steering_boot', r'\bпыльник\w*\s+рулев\w*\s+рейк\w*'),
    ('tie_rod_end', r'\bнаконечник\w*\s+(?:рулев\w*\s+)?тяг\w*'),
    ('tie_rod', r'\bтяг\w*\s+рулев\w*'),
    ('ball_joint', r'\b(?:опор\w*\s+шаров\w*|шаров\w*\s+опор\w*)\b'),
    ('brake_pads', r'\b(?:колодк\w*\s+тормозн\w*|тормозн\w*\s+колодк\w*)\b'),
    ('brake_disc', r'\b(?:диск\w*\s+тормозн\w*|тормозн\w*\s+диск\w*)\b'),
    ('cabin_filter', r'\bфильтр\w*\s+(?:салон\w*|салона)\b'),
    ('oil_filter', r'\bфильтр\w*\s+(?:маслян\w*|маслян\w*|масл\w*)\b'),
    ('air_filter', r'\bфильтр\w*\s+воздушн\w*\b'),
    ('fuel_filter', r'\bфильтр\w*\s+топливн\w*\b'),
    ('shock_absorber', r'\bамортизатор\w*\b'),
    ('control_arm', r'\bрычаг\w*\s+(?:передн\w*\s+|задн\w*\s+)?подвеск\w*\b'),
    ('engine_mount', r'\b(?:опор\w*|подушк\w*)\s+двигател\w*\b'),
    ('spark_plug', r'\bсвеч\w*\s+зажиган\w*\b'),
    ('glow_plug', r'\bсвеч\w*\s+накаливан\w*\b'),
    ('fuel_pump', r'\bнасос\w*\s+топливн\w*\b'),
    ('water_pump', r'\b(?:насос\w*\s+водян\w*|помп\w*\s+водян\w*)\b'),
    ('ac_condenser', r'\bрадиатор\w*\s+кондиц\w*\b'),
    ('radiator', r'\bрадиатор\w*\s+охлажден\w*\b'),
    ('valve_cover_gasket', r'\bпрокладк\w*\s+(?:клапанн\w*\s+крышк\w*|крышк\w*\s+клапан\w*)\b'),
    ('wheel_seal', r'\bсальник\w*\s+ступиц\w*\b'),
    ('steering_rack', r'\b(?:рейк\w*\s+рулев\w*|рулев\w*\s+рейк\w*)\b'),
)
# Only a LEADING noun phrase asserts the physical item. A phrase in a vehicle
# application note, kit content or after an unrelated first noun is not a head.
COMPILED_HEADS = tuple((code, re.compile(r'^\s*'+expr, re.I)) for code,expr in HEAD_RULES)
# The *physical item* decides the category, not the assembly referenced later.
SUBCOMPONENT_OF = {
    'timing_chain_guide': {'timing_chain'},
    'timing_chain_tensioner': {'timing_chain'},
    'timing_belt_roller': {'timing_belt', 'drive_belt_tensioner', 'belt_tensioner'},
    'belt_tensioner': {'drive_belt_tensioner', 'drive_belt', 'timing_belt'},
    'brake_caliper_piston': {'brake_caliper', 'brake_caliper_repair_kit'},
    'brake_caliper_repair_kit': {'brake_caliper'},
    'cv_boot': {'cv_joint'},
    'strut_mount': {'strut', 'shock_absorber'},
    'wheel_bearing': {'wheel_hub'},
    'arm_bushing': {'bushing'},
    'suspension_frame_bushing': {'bushing'},
    'stabilizer_bushing': {'bushing'},
}
# Compatible generic representation is a precision issue, not a false claim
# that the item is a different physical part.
COMPATIBLE_GENERIC = {
    'arm_bushing': {'bushing'},
    # Category subframe_bushing and detected head suspension_frame_bushing
    # both refer to a suspension-subframe rubber bushing.
    'suspension_frame_bushing': {'bushing', 'subframe_bushing'},
    'stabilizer_bushing': {'bushing'},
}
NON_AUTOMOTIVE = re.compile(r'\b(?:жидкост\w*\s+для\s+розжига|средств\w*\s+от\s+комар\w*)\b',re.I)


def detect_head(text: str) -> Head | None:
    """Select the first explicit head; no guessing from brand/article codes."""
    normalized=' '.join((text or '').casefold().replace('ё','е').split())
    for code,pattern in COMPILED_HEADS:
        m=pattern.search(normalized)
        if m:return Head(code=code,evidence=m.group(0))
    return None


def source_checks(category_code: str | None, descriptions: list[str]) -> dict:
    """Return deterministic reasons for a *stored* classification/preflight.

    Two recognized but distinct heads cannot be silently reconciled. The only
    permissible precision mismatch is an explicit subtype of generic bushing.
    """
    issues=set(); heads=[]
    for description in descriptions:
        if NON_AUTOMOTIVE.search(description or ''):issues.add('non_automotive_item')
        head=detect_head(description)
        if head and head.code=='timing_chain_guide' and not re.search(r'\b(?:грм|газораспредел\w*)\b',description,re.I):
            issues.add('part_subsystem_unconfirmed')
        if head:
            heads.append(head.code)
            if category_code and head.code != category_code:
                if category_code in COMPATIBLE_GENERIC.get(head.code,set()):
                    continue
                issues.add('physical_part_type_mismatch')
    if len(set(heads))>1:
        if not all(h in COMPATIBLE_GENERIC.get(other,set()) or other in COMPATIBLE_GENERIC.get(h,set()) for h in heads for other in heads):
            issues.add('supplier_physical_part_disagreement')
    return {'issues':sorted(issues),'heads':heads,
            'coverage':len(heads),'sources':len(descriptions)}


def refine_match(existing: set[str], head: Head | None, available: set[str]) -> tuple[set[str], bool]:
    """Prefer an independently confirmed *matched* specific rule.

    A semantic phrase alone may veto a wrong category, but may NOT manufacture
    a classification when an administrator disables a database rule.
    """
    if head is None:return existing,False
    if head.code in existing and head.code in available:
        return existing-SUBCOMPONENT_OF.get(head.code,set()),False
    if existing and any(code not in COMPATIBLE_GENERIC.get(head.code,set()) for code in existing):
        return existing,True
    return existing,False
