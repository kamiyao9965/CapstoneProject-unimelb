"""Round-level orchestration for schema generation and refinement."""

from __future__ import annotations

from pathlib import Path

from src.common.json_artifacts import read_artifact, write_artifact, next_available_path
from src.common.json_contracts import load_contract
from src.schema.loader import load_schema_data
from src.schema.validation import validate_schema_mapping
from src.refine.human_review import apply_review, load_review_decisions
from src.refine.human_review.queue import review_identity, review_manifest
from src.refine.human_review import QUEUE_FILENAME, load_review_queue
from src.refine.pipeline.steps import (
    evaluate_schema,
    generate_schema,
    run_consensus_stage,
    select_discovery_samples,
)
from src.verticals.manifest import default_manifest_path, load_vertical_manifest


def _schema_contract(args) -> str:
    manifest = getattr(args, "vertical_manifest", None) or load_vertical_manifest(
        default_manifest_path("private_health")
    )
    return manifest.contract("discovered_schema")


def next_round_index(out_dir: Path) -> int:
    """Return the first round_N directory that does not exist yet."""
    index = 1
    while (out_dir / f"round_{index}").exists():
        index += 1
    return index


def run_round(args, round_index: int, feedback_in: str | None) -> str | None:
    """Run one schema-generation round.

    Order:
      PDF samples -> schema discovery -> optional consensus
      -> optional human review stop -> holdout schema application
      -> failure discovery -> refinement feedback -> final_schema.json.

    Returns feedback text when holdout evaluation runs, or None when attended
    review pauses the round before holdout extraction.
    """
    round_dir = Path(args.out_dir) / f"round_{round_index}"
    round_dir.mkdir(parents=True, exist_ok=True)
    schema_path = round_dir / "schema.json"
    with_consensus = args.consensus_runs > 1
    draft_path = round_dir / "schema_draft.json" if with_consensus else schema_path

    print(f"\n========== ROUND {round_index} ==========")
    print("[generate] discovering schema" + (" with feedback" if feedback_in else ""))
    schema_build_samples = select_discovery_samples(args)
    schema_data = generate_schema(
        args,
        feedback_in,
        draft_path,
        sample_paths=schema_build_samples,
    )
    print(f"[generate] wrote {draft_path}")

    if with_consensus:
        schema_data, schema_build_samples = _run_consensus_stage(
            args,
            draft_path,
            round_dir,
            schema_path,
            schema_build_samples,
        )

    if with_consensus and args.review_ui:
        _print_review_stop(round_dir, round_dir / "consensus" / QUEUE_FILENAME)
        return None

    if not _manifest(args).supports("evaluation"):
        final_schema_path = publish_final_schema(
            schema_path,
            Path(args.out_dir),
            data_contract=_schema_contract(args), manifest=_manifest(args),
        )
        print(f"[final-schema] wrote {final_schema_path} (evaluation not configured)")
        return ""

    print("[schema-application] extracting holdout PDFs and discovering failures")
    _analysis, feedback_out = evaluate_schema(
        args,
        schema_data,
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[refinement-feedback]\n" + feedback_out)

    final_schema_path = publish_final_schema(
        schema_path,
        Path(args.out_dir),
        data_contract=_schema_contract(args), manifest=_manifest(args),
    )
    print(f"[final-schema] wrote {final_schema_path}")
    return feedback_out


def publish_final_schema(
    schema_path: Path,
    out_dir: Path,
    *,
    data_contract: str = "private_health/discovered_schema",
    manifest=None,
) -> Path:
    """Publish the latest completed round schema to a stable path for extraction."""
    artifact = read_artifact(
        schema_path,
        expected_type="discovered_schema",
        data_contract_schema=load_contract(data_contract, manifest=manifest),
    )
    final_schema_path = next_available_path(out_dir / "final_schema.json")
    write_artifact(
        final_schema_path,
        artifact,
        data_contract_schema=load_contract(data_contract, manifest=manifest),
    )
    return final_schema_path


def _run_consensus_stage(
    args,
    draft_path: Path,
    round_dir: Path,
    schema_path: Path,
    base_sample_paths: tuple[str, ...],
) -> tuple[dict[str, object], tuple[str, ...]]:
    print(f"[consensus] voting over {args.consensus_runs} patch runs")
    outputs = run_consensus_stage(
        args,
        draft_path,
        round_dir,
        base_sample_paths=base_sample_paths,
    )

    artifact = read_artifact(
        outputs.consensus_schema_path,
        expected_type="discovered_schema",
        data_contract_schema=load_contract(_schema_contract(args), manifest=_manifest(args)),
    )
    write_artifact(
        schema_path,
        artifact,
        data_contract_schema=load_contract(_schema_contract(args), manifest=_manifest(args)),
    )
    suffix = " for human review" if args.review_ui else ""
    print(f"[consensus] wrote {schema_path}{suffix}")
    return artifact["data"], outputs.schema_build_samples


def _print_review_stop(round_dir: Path, queue_path: Path) -> None:
    consensus_dir = round_dir / "consensus"
    print(
        "\n--- Human review stop ---\n"
        f"Review queue: {queue_path}\n"
        "1. Review proposals:\n"
        f"     streamlit run src/review_app.py -- --consensus-dir {consensus_dir}\n"
        "2. Apply your decisions (also available from the UI):\n"
        f"     python src/refine/review.py apply --consensus-dir {consensus_dir}\n"
        "3. Resume with the reviewed schema for holdout extraction, failure discovery, "
        "feedback, and final_schema.json:\n"
        f"     python src/refine/loop.py --resume-review {round_dir}"
    )


def resume_review(args) -> int:
    """Evaluate a human-reviewed schema, write feedback, then publish final_schema.json."""
    round_dir = Path(args.resume_review)
    reviewed_path = round_dir / "consensus" / "reviewed_schema.json"
    if not reviewed_path.exists():
        print(
            f"{reviewed_path} not found. Apply your review decisions first:\n"
            f"  python src/refine/review.py apply --consensus-dir {round_dir / 'consensus'}"
        )
        return 1

    queue = load_review_queue(round_dir / "consensus" / QUEUE_FILENAME)
    identity = review_identity(queue)
    manifest = _manifest(args)
    if identity["vertical"] != manifest.vertical:
        raise ValueError("Reviewed queue vertical conflicts with selected manifest.")
    decisions = load_review_decisions(round_dir / "consensus" / "review_decisions.json")
    expected, _ = apply_review(queue, decisions,
        load_schema_data(queue["metadata"]["base_schema_path"], review_manifest(queue)))
    artifact = read_artifact(reviewed_path, expected_type="discovered_schema",
        data_contract_schema=load_contract(_schema_contract(args), manifest=manifest))
    if artifact["provenance"]["run_id"] != identity["queue_id"] or artifact["data"] != expected:
        raise ValueError("Reviewed schema does not match this queue and its current decisions.")
    schema_path = next_available_path(round_dir / "schema.json")
    write_artifact(schema_path, artifact,
        data_contract_schema=load_contract(_schema_contract(args), manifest=manifest))
    print(f"[resume-review] wrote reviewed schema to {schema_path}")
    if not _manifest(args).supports("evaluation"):
        final_schema_path = publish_final_schema(
            schema_path,
            Path(args.out_dir),
            data_contract=_schema_contract(args), manifest=_manifest(args),
        )
        print(f"[final-schema] wrote {final_schema_path} (evaluation not configured)")
        print(
            "Review the deterministic Canonical mapping next:\n"
            f"  streamlit run src/canonical_review_app.py -- --schema {reviewed_path}"
        )
        return 0
    schema_build_samples = _schema_build_samples_from_review_queue(
        round_dir / "consensus" / QUEUE_FILENAME
    )
    print("[schema-application] extracting holdout PDFs and discovering failures")
    _analysis, feedback_out = evaluate_schema(
        args,
        artifact["data"],
        round_dir,
        exclude_paths=schema_build_samples,
    )
    print("\n[refinement-feedback]\n" + feedback_out)
    final_schema_path = publish_final_schema(
        schema_path,
        Path(args.out_dir),
        data_contract=_schema_contract(args), manifest=_manifest(args),
    )
    print(f"[final-schema] wrote {final_schema_path}")
    print(
        "\nReviewed-schema extraction feedback is available at:\n"
        f"  {round_dir / 'refinement_feedback.json'}\n"
        "To feed it into the next round:\n"
        f"  python src/refine/loop.py --resume-feedback {round_dir / 'refinement_feedback.json'}"
    )
    return 0


def _manifest(args):
    return getattr(args, "vertical_manifest", None) or load_vertical_manifest(
        default_manifest_path("private_health")
    )


def _schema_build_samples_from_review_queue(queue_path: Path) -> tuple[str, ...]:
    if not queue_path.exists():
        return tuple()
    queue = load_review_queue(queue_path)
    metadata = queue.get("metadata", {})
    if not isinstance(metadata, dict):
        return tuple()
    samples = metadata.get("schema_build_samples") or []
    if not isinstance(samples, list):
        return tuple()
    return tuple(str(path) for path in samples)
