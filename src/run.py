from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
from src.evaluation.reporter import EvaluationReporter
from src.models import ScraperConfig
from src.pipeline.extractor import LLMExtractor
from src.pipeline.ingestor import PDFIngestor
from src.pipeline.router import FormatRouter
from src.schema.discovery import SchemaDiscovery
from src.schema.loader import SchemaLoader
from src.schema.validator import SchemaValidator
from src.scraper.crawler import DynamicCrawler, StaticCrawler, scraper_from_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Konkrd extraction pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser("crawl", help="Discover and download product PDFs")
    crawl.add_argument("--vertical", required=True)
    crawl.add_argument("--config", required=True)

    discover = subparsers.add_parser("discover", help="Propose a draft schema from sample PDFs")
    discover.add_argument("--vertical", required=True)
    discover.add_argument("--samples", nargs="+", required=True)
    discover.add_argument("--output")

    extract = subparsers.add_parser("extract", help="Extract one PDF into structured JSON")
    extract.add_argument("--pdf", required=True)
    extract.add_argument("--schema", required=True)
    extract.add_argument("--output")
    extract.add_argument("--provider", default=None)
    extract.add_argument("--model", default=None)

    batch = subparsers.add_parser("batch", help="Run extraction over a vertical")
    batch.add_argument("--vertical", required=True)
    batch.add_argument("--schema", required=True)
    batch.add_argument("--input-root")
    batch.add_argument("--evaluate", action="store_true")
    batch.add_argument("--provider", default=None)
    batch.add_argument("--model", default=None)

    return parser


def command_crawl(args: argparse.Namespace) -> int:
    payload = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    config = ScraperConfig.model_validate(payload)
    scraper = scraper_from_config(config)
    results = scraper.run()
    success = sum(1 for item in results if item.status == "success")
    duplicates = sum(1 for item in results if item.status == "duplicate")
    failures = sum(1 for item in results if item.status == "failed")
    print(f"Downloaded: {success}, duplicates: {duplicates}, failed: {failures}")
    return 0 if failures == 0 else 1


def command_discover(args: argparse.Namespace) -> int:
    discovery = SchemaDiscovery()
    schema_yaml = discovery.discover(args.samples, args.vertical)
    if args.output:
        Path(args.output).write_text(schema_yaml, encoding="utf-8")
        print(f"Wrote schema draft to {args.output}")
    else:
        print(schema_yaml)
    return 0


def command_extract(args: argparse.Namespace) -> int:
    schema_loader = SchemaLoader()
    schema = schema_loader.load(args.schema)
    issues = SchemaValidator().validate(schema)
    if issues:
        print("Schema validation issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    ingestor = PDFIngestor()
    router = FormatRouter()
    extractor = LLMExtractor(schema=schema, provider=args.provider, model=args.model)

    parsed = ingestor.ingest(args.pdf)
    extraction_input = router.route(parsed)
    result = extractor.extract(extraction_input)

    output_path = args.output or default_output_path(schema.vertical, Path(args.pdf))
    result.write_json(output_path)
    print(f"Wrote extraction to {output_path}")
    return 0


def command_batch(args: argparse.Namespace) -> int:
    config = load_config()
    schema = SchemaLoader().load(args.schema)
    issues = SchemaValidator().validate(schema)
    if issues:
        print("Schema validation issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    input_root = Path(args.input_root) if args.input_root else config.data_dir / args.vertical / "raw" / "PDFs"
    pdf_paths = sorted(input_root.rglob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found under {input_root}")
        return 1

    ingestor = PDFIngestor()
    router = FormatRouter()
    extractor = LLMExtractor(schema=schema, provider=args.provider, model=args.model)

    reports = []
    gt_store = None
    evaluator = None
    reporter = None
    if args.evaluate and args.vertical == "private_health":
        gt_store = PrivateHealthGroundTruthStore(config.data_dir / "private_health" / "labelled")
        evaluator = ExtractionEvaluator()
        reporter = EvaluationReporter()

    for pdf_path in pdf_paths:
        parsed = ingestor.ingest(str(pdf_path))
        extraction_input = router.route(parsed)
        result = extractor.extract(extraction_input)
        output_path = default_output_path(schema.vertical, pdf_path)
        result.write_json(output_path)
        print(f"Extracted {pdf_path.name} -> {output_path}")

        if gt_store and evaluator:
            product_match, ground_truth = gt_store.load_ground_truth(pdf_path)
            if ground_truth:
                reports.append(
                    evaluator.evaluate(
                        extracted=result,
                        ground_truth=ground_truth,
                        product_key=product_match.id_master if product_match else None,
                    )
                )

    if reports and evaluator and reporter:
        summary = evaluator.aggregate(reports)
        report_root = config.outputs_dir / args.vertical / "evaluation"
        reporter.write_json(reports, summary, report_root / "report.json")
        reporter.write_markdown(reports, summary, report_root / "report.md")
        print(f"Wrote evaluation reports to {report_root}")
    elif args.evaluate:
        print("Evaluation skipped: only private_health currently has a ground-truth adapter.")

    return 0


def default_output_path(vertical: str, pdf_path: Path) -> Path:
    config = load_config()
    relative_parts = pdf_path.with_suffix(".json").parts[-4:]
    return config.outputs_dir / vertical / "extractions" / Path(*relative_parts)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "crawl":
        return command_crawl(args)
    if args.command == "discover":
        return command_discover(args)
    if args.command == "extract":
        return command_extract(args)
    if args.command == "batch":
        return command_batch(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
