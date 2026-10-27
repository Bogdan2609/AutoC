"""No external side effects: v0.44 acceptance contract tests."""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree

import pytest

from app.preproduction_v044.assessment import (
    GATES, SCHEMA, assess, junit_summary, load_evidence_manifest,
)


def test_all_roadmap_acceptance_domains_are_explicit():
    codes = [gate.code for gate in GATES]
    assert len(codes) == len(set(codes)) >= 14
    assert set(code[:3] for code in codes) == {'G44'}
    assert {'technical', 'external', 'legal', 'governance'} == {gate.kind for gate in GATES}


def test_no_evidence_is_no_go():
    report = assess(source_version='0.44.0')
    assert report['schema'] == SCHEMA
    assert report['live_money_decision'] == 'NO_GO'
    assert not report['automatic_approval_possible']
    assert report['missing_gate_count'] == len(GATES)
    assert report['evidence_count'] == 0


def test_even_all_evidence_does_not_authorize_money():
    data = {gate.code: [{'name': 'result.txt', 'sha256': 'a' * 64}] for gate in GATES}
    report = assess(data, source_version='0.44.0')
    assert report['evidence_count'] == len(GATES)
    assert report['missing_gate_count'] == 0
    assert report['live_money_decision'] == 'NO_GO'
    assert report['human_validation_pending_count'] == len(GATES)


def test_evidence_cannot_override_status():
    data = {'G44-02A': [{'name':'demo.txt', 'sha256':'b'*64, 'status':'GO', 'secret':'password'}]}
    report = assess(data)
    row = next(r for r in report['gates'] if r['code'] == 'G44-02A')
    assert row['status'] == 'REQUIRES_HUMAN_VALIDATION'
    assert '"secret":' not in json.dumps(report)
    assert 'password' not in json.dumps(report)
    assert report['live_money_decision'] == 'NO_GO'


def test_invalid_evidence_is_rejected():
    with pytest.raises(ValueError):
        assess({'G44-02A': [{'name':'evidence', 'sha256':'INVALID'}]})
    with pytest.raises(ValueError):
        assess({'G44-02A': 'evidence'})


def test_manifest_rejects_path_escape(tmp_path: Path):
    folder = tmp_path / 'items'
    folder.mkdir()
    source = folder / 'data.txt'
    source.write_text('unit-test-only')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'G44-01A':['data.txt']}))
    assert load_evidence_manifest(manifest, base_dir=folder)['G44-01A'][0]['sha256']
    manifest.write_text(json.dumps({'G44-01A':['../other.txt']}))
    with pytest.raises(ValueError):
        load_evidence_manifest(manifest, base_dir=folder)
    manifest.write_text(json.dumps({'UNKNOWN':['data.txt']}))
    with pytest.raises(ValueError):
        load_evidence_manifest(manifest, base_dir=folder)


def test_junit_observation_does_not_authorize_go(tmp_path: Path):
    suites = Element('testsuites')
    suite = SubElement(suites, 'testsuite', name='regression')
    SubElement(suite, 'testcase', name='passing')
    SubElement(SubElement(suite, 'testcase', name='skipped'), 'skipped')
    path = tmp_path / 'pytest.xml'
    ElementTree(suites).write(path)
    summary = junit_summary(path)
    assert summary['tests'] == 2 and summary['passed'] == 1 and summary['skipped'] == 1
    report = assess(junit=path)
    assert report['live_money_decision'] == 'NO_GO'
    assert next(x for x in report['gates'] if x['code'] == 'G44-01C')['status'] == 'MISSING_EVIDENCE'


def test_malformed_junit_rejected(tmp_path: Path):
    file = tmp_path/'broken.xml'
    file.write_text('<error/>')
    with pytest.raises(ValueError):
        junit_summary(file)


@pytest.mark.asyncio
async def test_admin_gate_does_not_expose_secrets_or_activate(client, admin_headers):
    path = '/api/v1/backoffice/preproduction-v044/gates'
    anonymous = await client.get(path)
    assert anonymous.status_code in (401, 403)
    authorized = await client.get(path, headers=admin_headers)
    assert authorized.status_code == 200, authorized.text
    data = authorized.json()
    assert data['read_only'] is True
    assert data['live_money_decision'] == 'NO_GO'
    assert not data['automated_go_possible']
    assert len(data['gates']) == len(GATES)
    serialized = json.dumps(data).lower()
    for secret in ('credential_profile', 'key1', 'password', 'phone', 'requisite_id'):
        assert secret not in serialized
    assert (await client.post(path, headers=admin_headers)).status_code == 405


def test_admin_security_dependency_on_v044_router():
    from fastapi.routing import APIRoute, iter_route_contexts
    from app.core.security import require_admin
    from app.main import app
    matched = [r for r in iter_route_contexts(app.routes)
               if isinstance(r.original_route, APIRoute)
               and r.path.startswith('/api/v1/backoffice/preproduction-v044/')]
    assert len(matched) == 1
    assert any(dep.call is require_admin for dep in matched[0].dependant.dependencies)


def test_staff_rbac_classifies_v043_and_v044_read_side():
    from app.modules.admin_staff.policy import resource_scope, role_allowed
    for path in ('/api/v1/backoffice/operations-health/summary',
                 '/api/v1/backoffice/preproduction-v044/gates'):
        assert resource_scope(path) == 'system'
        assert role_allowed('owner', 'system', 'GET')
        assert not role_allowed('viewer', 'system', 'POST')
