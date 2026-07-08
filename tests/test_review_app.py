from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from streamlit.testing.v1 import AppTest

    HAS_STREAMLIT = True
except ModuleNotFoundError:  # pragma: no cover - streamlit is in requirements
    HAS_STREAMLIT = False

from src.refine.candidates.aggregator import FieldDecision
from src.refine.candidates.patch import dump_yaml, load_yaml
from src.refine.human_review import build_review_queue, write_review_queue

APP_PATH = "src/review_app.py"


def build_fixture(tmp: str) -> Path:
    out = Path(tmp)
    base = {
        "vertical": "private_health",
        "fields": [
            {"name": "product_name", "type": "string", "description": "Product name",
             "applies_to": ["hospital"], "required": True, "values": []}
        ],
    }
    base_path = out / "base_schema.yaml"
    dump_yaml(base, base_path)
    decisions = [
        FieldDecision(
            "annual_limit", "extras_cover", "number", "Annual limit", 8, 10, "core",
            aliases=["yearly_limit"], source_runs=["run_001"], average_confidence=0.87,
            patch_types=["add_field"], rationale_samples=["Common"], reject_votes=1,
            reject_rationale_samples=["Sometimes promotional"],
        ),
        FieldDecision(
            "excess", "hospital_cover", "number", "Excess", 6, 10, "conditional",
            patch_types=["rename_field"],
        ),
    ]
    queue = build_review_queue(
        decisions, base, total_runs=10, base_schema_path=base_path,
        generated_at="2026-07-08T00:00:00+00:00",
    )
    write_review_queue(queue, out / "review_queue.yaml")
    return out


@unittest.skipUnless(HAS_STREAMLIT, "streamlit not installed")
class ReviewAppTest(unittest.TestCase):
    def render(self, fixture: Path) -> "AppTest":
        at = AppTest.from_file(APP_PATH, default_timeout=20)
        at.run()
        at.sidebar.text_input[0].set_value(str(fixture))
        at.run()
        return at

    def test_renders_queue_without_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            at = self.render(build_fixture(tmp))
            self.assertFalse(at.exception)
            self.assertEqual(len(at.radio[0].options), 2)

    def test_accept_click_persists_decision_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_fixture(tmp)
            at = self.render(fixture)
            at.button(key="accept:field:annual_limit").click()
            at.run()
            self.assertFalse(at.exception)
            payload = load_yaml(fixture / "review_decisions.yaml")
            self.assertEqual(
                payload["decisions"][0]["id"], "field:annual_limit"
            )
            self.assertEqual(payload["decisions"][0]["action"], "accept")


if __name__ == "__main__":
    unittest.main()
