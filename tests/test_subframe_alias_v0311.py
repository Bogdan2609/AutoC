"""Regression for a real Febest/LYNXauto false positive in v0.31.1."""
from app.modules.multi_enrichment.semantic_guard import detect_head, source_checks, refine_match


def test_subframe_bushing_alias_with_rossko_partkom():
    desc = [
        'Сайлентблок подрамника',
        'Сайлентблок подрамника | перед прав/лев |',
    ]
    assert all(detect_head(d).code == 'suspension_frame_bushing' for d in desc)
    assert source_checks('subframe_bushing', desc)['issues'] == []
    assert source_checks('subframe_bushing', [desc[1]])['issues'] == []
    assert refine_match({'subframe_bushing'}, detect_head(desc[0]), {'subframe_bushing'}) == ({'subframe_bushing'}, False)


def test_still_blocks_real_physical_part_errors():
    assert 'physical_part_type_mismatch' in source_checks('timing_chain', ['Успокоитель цепи ГРМ левый'])['issues']
    assert 'physical_part_type_mismatch' in source_checks('timing_chain', ['Натяжитель цепи ГРМ'])['issues']
