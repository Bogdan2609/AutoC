"""Offline v0.44 acceptance report with hashed evidence and fail-closed NO_GO.

Usage: python scripts/acceptance_report_v044.py --output /tmp/report.json
Optional: --evidence-dir folder --manifest folder/manifest.json --junit tests.xml
All paths are local files. No network or database operations are used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.preproduction_v044.assessment import assess, load_evidence_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    if (args.evidence_dir is None) != (args.manifest is None):
        parser.error('Use both --evidence-dir and --manifest')
    if args.output.exists() and args.output.is_symlink():
        parser.error('Refusing to overwrite symlink')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text('utf8'))['project']['version']
    evidence = (load_evidence_manifest(args.manifest, base_dir=args.evidence_dir)
                if args.manifest else {})
    report = assess(evidence, junit=args.junit, source_version=version)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    temp = output.with_suffix(output.suffix + '.tmp')
    if temp.exists() and temp.is_symlink():
        parser.error('Refusing to write symlink')
    temp.write_text(serialized, encoding='utf8')
    temp.replace(output)
    print('ACCEPTANCE REPORT WRITTEN: ' + str(output))
    print('Evidence entries: ' + str(report['evidence_count']))
    print('Unverified gates: ' + str(report['missing_gate_count'] + report['human_validation_pending_count']))
    print('LIVE MONEY DECISION: NO_GO')
    return 0


if __name__ == '__main__':
    sys.exit(main())
