"""Classified offline replay of saved development trials and retrieved responses."""
import argparse
from collections import Counter
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
from .extraction_diagnostics import classify_diagnostic, validate_runtime
from .source_coverage import inventory


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return loads_json(path.read_text(encoding='utf-8'))


def audit(run_dir, text):
    plan = read(run_dir / 'run_plan.json')
    if plan.get('split') != 'development':
        raise ValueError('Development trials only')
    schema = read(run_dir / 'schema_snapshot.json')
    source = (run_dir / 'source_representation.md').read_text(encoding='utf-8')
    assert hashlib.sha256(source.encode()).hexdigest() == plan['source_text_sha256']
    clauses = inventory(source)
    assert [asdict(c) for c in clauses] == read(run_dir / 'source_checklist.json')
    manifest = resolve_manifest(vertical='car_insurance')
    try:
        data = loads_json(text)
        validate_inline_contract(data, compile_extraction_contract(schema))
    except ValueError as exc:
        return dict(status='shape_failed', diagnostics=[dict(category='structure_or_business_rule',message=str(exc))])
    try:
        validate_runtime(data, lambda d: validate_extraction_record(schema, d, manifest=manifest), clauses, source)
        errors = []
    except BusinessDiagnostics as exc:
        errors = [classify_diagnostic(e) for e in exc.errors]
    return dict(status='checks_failed' if errors else 'checks_passed_not_human_approved', diagnostics=errors,
                categories=dict(Counter(e['category'] for e in errors)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', default=[])
    parser.add_argument('--lookup-dir', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    inputs = {}
    records = []
    def keep(path):
        inputs[str(path)] = digest(path)
    def check(run_dir, response_file, response_text, origin):
        keep(response_file)
        for name in ['run_plan.json', 'schema_snapshot.json', 'source_representation.md', 'source_checklist.json']:
            keep(run_dir / name)
        result = audit(run_dir, response_text)
        records.append(dict(run_dir=str(run_dir), response_file=str(response_file), origin=origin, **result))
    for run_dir in args.run_dir:
        for response_file in sorted(run_dir.glob('response_*.json')):
            check(run_dir, response_file, read(response_file)['text'], 'saved_original')
    if args.lookup_dir:
        for path in sorted(args.lookup_dir.glob('lookup_*.json')):
            response = read(path)
            if response.get('status') == 'completed' and response.get('output_text'):
                check(Path(response['run_dir']), path, response['output_text'], 'read_only_retrieval')
    assert all(digest(Path(p)) == h for p, h in inputs.items())
    root = Path(__file__).resolve().parents[2]
    runtime = ['src/car_insurance/source_coverage.py','src/car_insurance/extraction_diagnostics.py',
               'src/car_insurance/audit_saved_trials.py']
    report = dict(model_creations=0, candidate_modified=False, records=records, input_sha256=inputs,
        runtime_sha256={p:digest(root / p) for p in runtime},
        limitations='Diagnostic categories are triage, not semantic gold. Checker defects are recorded separately after counterexample verification.')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / 'audit_report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    for r in records:
        print(json.dumps({k:v for k,v in r.items() if k != 'diagnostics'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
