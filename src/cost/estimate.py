from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.cost.pricing import cost_usd, resolve_price
from src.common.json_codec import loads_json

from src.verticals.manifest import resolve_manifest


def load_runs(log_path: Path) -> list[dict]:
    if not log_path.exists():
        raise FileNotFoundError(f"Usage log not found: {log_path}")
    runs: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = loads_json(line)
        if not isinstance(payload, dict):
            raise ValueError("Each usage-log line must be a JSON object.")
        runs.append(payload)
    return runs


def per_doc_tokens(runs: list[dict]) -> tuple[float, float]:
    """Average input/output tokens per sampled document across all runs.

    Used as a proxy for per-document extraction cost until the real extraction
    step exists and can report its own token profile.
    """
    total_in = total_out = total_docs = 0
    for run in runs:
        docs = run.get("sample_count") or 0
        if not docs:
            continue
        total_in += run.get("input_tokens") or 0
        total_out += run.get("output_tokens") or 0
        total_docs += docs
    if total_docs == 0:
        return 0.0, 0.0
    return total_in / total_docs, total_out / total_docs


def summarise(runs: list[dict], input_rate: float | None, output_rate: float | None) -> None:
    print(f"Runs found: {len(runs)}\n")
    grand_cost = 0.0
    any_estimate = False

    header = f"{'model':<12} {'docs':>4} {'in_tok':>9} {'out_tok':>9} {'$/run':>9} {'$/doc':>8}"
    print(header)
    print("-" * len(header))

    for run in runs:
        model = run.get("model", "?")
        # Discovery logs record sample_count; extraction logs are one row per
        # document (they carry source_pdf instead), so count those as 1 doc.
        docs = run.get("sample_count") or (1 if run.get("source_pdf") else 0)
        in_tok = run.get("input_tokens") or 0
        out_tok = run.get("output_tokens") or 0
        try:
            price, is_estimate = resolve_price(model, input_rate, output_rate)
        except KeyError as exc:
            print(f"{model:<12} (skipped: {exc})")
            continue
        any_estimate = any_estimate or is_estimate
        run_cost = cost_usd(in_tok, out_tok, price)
        grand_cost += run_cost
        per_doc = run_cost / docs if docs else 0.0
        flag = "*" if is_estimate else " "
        print(f"{model:<12}{flag}{docs:>4} {in_tok:>9} {out_tok:>9} {run_cost:>8.4f} {per_doc:>8.4f}")

    print("-" * len(header))
    print(f"Total discovery cost: ${grand_cost:.4f}")
    if any_estimate:
        print("\n* rate is an UNVERIFIED placeholder - confirm against openai.com/api/pricing")


def project(
    runs: list[dict],
    model: str,
    verticals: dict[str, int],
    input_rate: float | None,
    output_rate: float | None,
    ext_in: float | None,
    ext_out: float | None,
) -> None:
    price, is_estimate = resolve_price(model, input_rate, output_rate)

    proxy_in, proxy_out = per_doc_tokens(runs)
    ext_in = ext_in if ext_in is not None else proxy_in
    ext_out = ext_out if ext_out is not None else proxy_out
    used_proxy = ext_in == proxy_in and ext_out == proxy_out

    per_doc = cost_usd(round(ext_in), round(ext_out), price)

    print(f"Model: {model}")
    print(f"Per-document extraction estimate: {ext_in:.0f} in + {ext_out:.0f} out tokens "
          f"= ${per_doc:.4f}/doc")
    if used_proxy:
        print("  (per-doc tokens are a PROXY from discovery runs; replace with real")
        print("   extraction numbers once the extraction step exists)")
    print()

    header = f"{'vertical':<20} {'docs':>7} {'total $':>12}"
    print(header)
    print("-" * len(header))
    grand = 0.0
    total_docs = 0
    for name, count in verticals.items():
        line_cost = per_doc * count
        grand += line_cost
        total_docs += count
        print(f"{name:<20} {count:>7} {line_cost:>12.2f}")
    print("-" * len(header))
    print(f"{'TOTAL':<20} {total_docs:>7} {grand:>12.2f}")
    if is_estimate:
        print("\n* rate is an UNVERIFIED placeholder - confirm against openai.com/api/pricing")


def parse_vertical(values: list[str] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"--vertical expects NAME=COUNT, got '{item}'")
        name, _, count = item.partition("=")
        result[name.strip()] = int(count)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Estimate schema/extraction cost from token usage logs")
    parser.add_argument("--manifest")
    parser.add_argument("--log", help="Path to token_usage.jsonl")
    parser.add_argument("--model", default="gpt-5", help="Model to price projections with")
    parser.add_argument("--input-rate", type=float, help="USD per 1M input tokens (override)")
    parser.add_argument("--output-rate", type=float, help="USD per 1M output tokens (override)")
    parser.add_argument("--project", action="store_true", help="Project cost across verticals")
    parser.add_argument("--vertical", action="append", metavar="NAME=COUNT",
                        help="Vertical and its document count; repeatable")
    parser.add_argument("--extraction-input-tokens", type=float,
                        help="Per-doc input tokens for extraction (default: proxy from log)")
    parser.add_argument("--extraction-output-tokens", type=float,
                        help="Per-doc output tokens for extraction (default: proxy from log)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = resolve_manifest(args.manifest)
    runs = load_runs(Path(args.log) if args.log else manifest.path("output_root") / "token_usage.jsonl")

    if args.project:
        verticals = parse_vertical(args.vertical)
        if not verticals:
            print("--project needs at least one --vertical NAME=COUNT")
            return 1
        project(
            runs,
            args.model,
            verticals,
            args.input_rate,
            args.output_rate,
            args.extraction_input_tokens,
            args.extraction_output_tokens,
        )
    else:
        summarise(runs, args.input_rate, args.output_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
