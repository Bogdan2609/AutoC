import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('catalog_audit',str(Path(__file__).resolve().parents[1]/'scripts'/'catalog_full_audit_v031.py'))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def record(description,category,product_id=None,product_status=None):
    return {'candidate_id':'cid','product_id':product_id,'sample_brand':'Mitsubishi','sample_article':'MN183892',
            'proposal_status':'applied' if product_id else 'proposed','category_code':category,
            'title':'Цепь ГРМ левый Mitsubishi MN183892','product_status':product_status,
            'seo_noindex':True,'attributes':{},'quality':92,'conflicts':[],
            'evidence':{'sources':[{'supplier':'partkom','source_line':23630,'description':description,
                    'extracted':{'category_code':category}}]}}

def test_known_false_timing_chain():
    issues=mod.inspect_record(record('Успокоитель цепи ГРМ левый','timing_chain','x','draft'))
    assert any(i['check_code']=='independent_type_mismatch' and i['severity']=='critical' for i in issues)

def test_valid_timing_chain():
    issues=mod.inspect_record(record('Цепь ГРМ Mitsubishi','timing_chain'))
    assert not any(i['check_code']=='independent_type_mismatch' for i in issues)

def test_nesting():
    pairs=[('Пыльник ШРУСа','cv_joint'),('Ремкомплект суппорта','brake_caliper'),
           ('Подшипник передней ступицы','wheel_hub'),('Натяжитель ремня','drive_belt'),
           ('Опора стойки амортизатора','shock_absorber'),('Радиатор кондиционера','radiator')]
    for name,category in pairs:
        assert any(i['check_code']=='independent_type_mismatch' for i in mod.inspect_record(record(name,category))),name

def test_not_all_different_words_errors():
    assert not any(i['check_code']=='independent_type_mismatch' for i in mod.inspect_record(record('Фильтр масляный','oil_filter')))

def test_proposed_with_conflict_blocks():
    r=record('Цепь ГРМ','timing_chain');r['conflicts']=['source_category_conflict']
    assert any(i['check_code']=='proposed_with_flags' for i in mod.inspect_record(r))

def test_no_automatic_correction():
    assert mod.source_type('Успокоитель цепи ГРМ левый')=='timing_chain_guide'
    assert mod.source_type('Цепь ГРМ')=='timing_chain'
