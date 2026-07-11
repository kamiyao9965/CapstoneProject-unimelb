from __future__ import annotations

import unittest

from src.common.model_config import (
    ModelSelection,
    resolve_api_key,
    resolve_selection,
)


class ModelSelectionTest(unittest.TestCase):
    def test_defaults_to_openai_gpt5_and_pdf(self) -> None:
        selection = resolve_selection(environment={})

        self.assertEqual(selection, ModelSelection("openai", "gpt-5", "pdf"))

    def test_explicit_values_override_environment_defaults(self) -> None:
        selection = resolve_selection(
            provider="anthropic",
            model="claude-test",
            document_input="markdown",
            environment={
                "LLM_PROVIDER": "deepseek",
                "LLM_MODEL": "deepseek-test",
                "LLM_DOCUMENT_INPUT": "pdf",
            },
        )

        self.assertEqual(selection, ModelSelection("anthropic", "claude-test", "markdown"))

    def test_preserves_the_model_identifier_casing(self) -> None:
        selection = resolve_selection(model="Provider.Model-V1", environment={})

        self.assertEqual(selection.model, "Provider.Model-V1")

    def test_rejects_unknown_provider_and_document_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported provider"):
            resolve_selection(provider="unknown", environment={})

        with self.assertRaisesRegex(ValueError, "Unsupported document input"):
            resolve_selection(document_input="html", environment={})

    def test_resolves_only_the_selected_provider_key(self) -> None:
        key, environment_name = resolve_api_key(
            ModelSelection("anthropic", "claude-test", "pdf"),
            environment={
                "MY_OPENAI_API_KEY": "openai-key",
                "ANTHROPIC_API_KEY": "anthropic-key",
                "DEEPSEEK_API_KEY": "deepseek-key",
            },
        )

        self.assertEqual(key, "anthropic-key")
        self.assertEqual(environment_name, "ANTHROPIC_API_KEY")

    def test_openai_key_keeps_project_then_standard_precedence(self) -> None:
        selection = ModelSelection("openai", "gpt-5", "pdf")

        key, environment_name = resolve_api_key(
            selection,
            environment={
                "MY_OPENAI_API_KEY": "project-key",
                "OPENAI_API_KEY": "standard-key",
            },
        )

        self.assertEqual(key, "project-key")
        self.assertEqual(environment_name, "MY_OPENAI_API_KEY")

    def test_openai_model_environment_variable_remains_a_legacy_fallback(self) -> None:
        selection = resolve_selection(environment={"OPENAI_MODEL": "gpt-legacy"})

        self.assertEqual(selection.model, "gpt-legacy")


if __name__ == "__main__":
    unittest.main()
