"""Replay saved development responses offline; never import/create an API provider."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from src.common.json_codec import loads_json
from src.common.json_contracts import validate_inline_contract
from src.common.structured_output import BusinessDiagnostics
from src.schema.contract import compile_extraction_contract
from src.schema.validation import validate_extraction_record
from src.verticals.manifest import resolve_manifest
from .extraction_diagnostics import relation_issues, validate_runtime
from .source_coverage import inventory


def read(path):
    return loads_json(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial-dir', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.trial_dir / 'run_plan.json')
    if plan.get('split') != 'development':
        raise ValueError('This diagnostic replay is limited to development trials.')
    schema = read(args.trial_dir / 'schema_snapshot.json')
    if schema.get('validation_profile') != 'car_insurance.review_v4':
        raise ValueError('Expected saved v4 schema, not a relabelled older record.')
    text = (args.trial_dir / 'source_representation.md').read_text(encoding='utf-8')
    assert hashlib.sha256(text.encode()).hexdigest() == plan['source_text_sha256'], 'Source drift'
    assert digest(args.baseline) == plan['baseline_result_sha256'], 'Baseline drift'
    contract = compile_extraction_contract(schema)
    manifest = resolve_manifest(vertical='car_insurance')
    clauses = inventory(text)
    assert [asdict(c) for c in clauses] == read(args.trial_dir / 'source_checklist.json'), 'Inventory drift'
    paths = [args.baseline, *sorted(args.trial_dir.glob('response_*.json')),
             args.trial_dir / 'schema_snapshot.json', args.trial_dir / 'source_representation.md']
    hashes = {str(p): digest(p) for p in paths}
    records = []
    for path in sorted(args.trial_dir.glob('response_*.json')):
        entry = dict(file=path.name, candidate_modified=False)
        try:
            data = loads_json(read(path)['text'])
            validate_inline_contract(data, contract)
            entry['shape'] = 'passed'
        except ValueError as exc:
            entry.update(shape='failed', error=str(exc))
            records.append(entry)
            continue
        try:
            validate_runtime(data, lambda payload: validate_extraction_record(schema, payload, manifest=manifest), clauses, text)
            entry['runtime_validation'] = 'passed'
        except BusinessDiagnostics as exc:
            entry.update(runtime_validation='failed', diagnostics=list(exc.errors))
        entry['source_relation_diagnostics'] = relation_issues(data, text)
        records.append(entry)
    baseline_relations = relation_issues(read(args.baseline)['data'], text)
    report = dict(mode='offline_saved_development_response_replay', model_calls=0,
        runtime_revision='v4_diagnostics_r2', schema_human_approved=False,
        baseline_relation_diagnostics=baseline_relations, responses=records, input_sha256=hashes,
        limitation='Baseline relation pass is not full semantic approval. Diagnostics do not repair raw candidates.')
    root = Path(__file__).resolve().parents[2]
    runtime_paths = ['src/car_insurance/extraction_diagnostics.py', 'src/car_insurance/replay_diagnostics.py',
                     'src/common/structured_output.py', 'src/schema_application/extractor.py',
                     'src/car_insurance/source_coverage.py', 'src/car_insurance/schema_revision_v4.py',
                     'src/car_insurance/schema_revision_v3.py']
    report['runtime_sha256'] = {p: digest(root / p) for p in runtime_paths}
    assert all(digest(path) == expected for path, expected in ((Path(p), h) for p, h in hashes.items()))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / 'replay_report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(model_calls=0, baseline_relation_issues=len(baseline_relations),
        responses=[dict(file=r['file'], status=r.get('runtime_validation', r['shape']),
                        diagnostic_count=len(r.get('diagnostics', [])),
                        relation_count=len(r.get('source_relation_diagnostics', []))) for r in records])))


if __name__ == '__main__':
    main()
