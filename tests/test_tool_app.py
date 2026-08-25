from __future__ import annotations

import unittest
from pathlib import Path

try:
    from streamlit.testing.v1 import AppTest

    HAS_STREAMLIT = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_STREAMLIT = False


@unittest.skipUnless(HAS_STREAMLIT, "streamlit not installed")
class ToolAppTests(unittest.TestCase):
    APP_PATH = Path(__file__).resolve().parents[1] / "src/tool_app.py"

    def test_default_discovery_page_renders_preview_without_executing(self) -> None:
        at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()

        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "Insurance schema operations")
        self.assertIn("Schema discovery", at.selectbox[0].options)
        self.assertIn("Travel refinement", at.selectbox[0].options)
        self.assertTrue(at.button(key="run-command").disabled)
        self.assertIn("src/run.py discover", at.code[0].value)

    def test_canonical_compile_form_has_no_paid_operation_confirmation(self) -> None:
        at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()
        at.selectbox[0].set_value("Compile Canonical Schema")
        at.run()

        self.assertFalse(at.exception)
        self.assertEqual(len(at.checkbox), 0)
        self.assertTrue(at.button(key="run-command").disabled)

    def test_every_allowlisted_operation_renders(self) -> None:
        labels = (
            "Schema discovery",
            "Single PDF extraction",
            "Batch extraction",
            "Travel document acquisition",
            "Travel refinement",
            "Compile Canonical Schema",
            "Initialize PostgreSQL storage",
            "Load extraction into PostgreSQL",
        )
        for label in labels:
            with self.subTest(operation=label):
                at = AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()
                at.selectbox[0].set_value(label)
                at.run()

                self.assertFalse(at.exception)
                self.assertEqual(len(at.button), 1)


if __name__ == "__main__":
    unittest.main()
