"""Read existing timed-out development Responses; never create/cancel/delete one."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


def inspect(client, run_dir):
    plan = json.loads((run_dir / 'run_plan.json').read_text(encoding='utf-8'))
    status_path = run_dir / 'run_status.json'
    raw = status_path.read_bytes()
    status = json.loads(raw)
    if plan.get('split') != 'development' or plan.get('provider') != 'openai':
        raise ValueError('Only existing OpenAI development runs are allowed.')
    match = re.search(r'\b(resp_[A-Za-z0-9]+)\b', status.get('error', ''))
    if status.get('error_type') != 'TimeoutError' or not match:
        raise ValueError('No recorded response ID in a TimeoutError; no request sent.')
    result = dict(run_dir=str(run_dir), response_id=match[1],
                  original_status_sha256=hashlib.sha256(raw).hexdigest(),
                  checked_at=datetime.now(timezone.utc).isoformat(), operation='responses.retrieve', model_creations=0)
    try:
        response = client.responses.retrieve(match[1])
        usage = getattr(response, 'usage', None)
        result.update(status=response.status, created_at=response.created_at,
                      completed_at=getattr(response, 'completed_at', None),
                      usage=usage.model_dump() if usage else None,
                      provider_error_code=getattr(getattr(response, 'error', None), 'code', None),
                      output_text=getattr(response, 'output_text', None))
    except Exception as exc:
        # Avoid logging headers, credentials or arbitrary request bodies.
        result.update(status='lookup_failed', error_type=type(exc).__name__,
                      http_status=getattr(exc, 'status_code', None))
    assert status_path.read_bytes() == raw
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    # Only credentials for the existing provider are read; never printed.
    credentials = dict(os.environ)
    for line in args.env_file.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() in {'OPENAI_API_KEY', 'MY_OPENAI_API_KEY'}:
            credentials[key.strip()] = value.strip().strip('\"\'')
    from src.common.model_config import ModelSelection, resolve_api_key
    # Resolver accepts an explicit mapping, preserving the repository key precedence.
    key, _ = resolve_api_key(ModelSelection('openai', 'gpt-5', 'markdown'), environment=credentials)
    if not key:
        raise RuntimeError('Configured key unavailable; no request sent.')
    from openai import OpenAI
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with OpenAI(api_key=key, timeout=30, max_retries=0) as client:
        for i, run_dir in enumerate(args.run_dir):
            result = inspect(client, run_dir)
            with (args.output_dir / f'lookup_{i+1}.json').open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
            print(json.dumps({k: v for k, v in result.items() if k != 'output_text'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
