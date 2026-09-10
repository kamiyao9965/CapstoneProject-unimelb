from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from src.tool_ui.commands import CommandResult
from src.tool_ui.forms import OPERATION_LABELS
from src.verticals.manifest import discover_manifests, OPERATION_CAPABILITIES


class ToolAppTests(unittest.TestCase):
    APP_PATH = Path(__file__).resolve().parents[1] / "src/tool_app.py"

    def app(self):
        return AppTest.from_file(str(self.APP_PATH), default_timeout=20).run()

    def test_default_discovery_page_renders_preview_without_executing(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "Insurance schema operations")
        self.assertIn("Schema discovery", at.selectbox(key="operation").options)
        self.assertIn("Schema refinement", at.selectbox(key="operation").options)
        self.assertNotIn("Compile Canonical Schema", at.selectbox(key="operation").options)
        self.assertTrue(at.button(key="run-command").disabled)
        self.assertIn("src/run.py discover", at.code[0].value)

    def test_every_capability_enabled_operation_renders(self):
        for code, manifest in discover_manifests().items():
            for label, operation in OPERATION_LABELS.items():
                if not manifest.supports(OPERATION_CAPABILITIES[operation]):
                    continue
                with self.subTest(vertical=code, operation=operation):
                    at = self.app()
                    at.selectbox(key="vertical").set_value(code).run()
                    at.selectbox(key="operation").set_value(label).run()
                    self.assertFalse(at.exception)
                    self.assertEqual(len(at.button), 1)
                    if operation == "canonical_compile":
                        self.assertEqual(len(at.checkbox), 0)
                        self.assertTrue(at.button(key="run-command").disabled)

    def test_changes_reset_confirmation_and_vertical_scopes_results(self):
        at = self.app()
        at.checkbox[0].check().run()
        self.assertFalse(at.button(key="run-command").disabled)
        model = next(field for field in at.text_input if field.label == "Model override (optional)")
        model.set_value("test-model").run()
        self.assertTrue(at.button(key="run-command").disabled)
        at.checkbox[0].check().run()
        with patch("src.tool_ui.commands.run_command", side_effect=lambda command: CommandResult(tuple(command), 0, "fixture-result", 0.1)) as runner:
            at.button(key="run-command").click().run()
        runner.assert_called_once()
        self.assertIn("fixture-result", [code.value for code in at.code])
        at.selectbox(key="vertical").set_value("travel_insurance").run()
        self.assertFalse(at.exception)
        self.assertNotIn("fixture-result", [code.value for code in at.code])
        self.assertTrue(at.button(key="run-command").disabled)
        self.assertEqual(next(field for field in at.text_input if field.label == "Model override (optional)").value, "")
        self.assertIn("travel_insurance/manifest.json", at.code[0].value)
        at.selectbox(key="operation").set_value("Schema refinement").run()
        self.assertEqual(next(field for field in at.number_input if field.label == "Consensus proposal runs").value, 5)
        at.selectbox(key="vertical").set_value("private_health").run()
        self.assertEqual(next(field for field in at.number_input if field.label == "Consensus proposal runs").value, 1)
        self.assertTrue(at.button(key="run-command").disabled)


if __name__ == "__main__":
    unittest.main()
