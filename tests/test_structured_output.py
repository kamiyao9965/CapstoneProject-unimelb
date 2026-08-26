from __future__ import annotations

import unittest
from dataclasses import replace

from src.common.json_contracts import load_contract
from src.common.model_config import ModelSelection
from src.common.model_provider import (
    ModelResponse,
    ModelUsage,
    ProviderResponseError,
    ProviderRequest,
    StructuredOutputSpec,
)
from src.common.structured_output import (
    StructuredOutputFailure,
    run_structured_output,
)
from src.schema.validation import validate_schema_mapping
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


class SequenceProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text=self.responses[len(self.requests) - 1],
            provider=request.selection.provider,
            model=request.selection.model,
            response_id=f"response-{len(self.requests)}",
        )


def request() -> ProviderRequest:
    return ProviderRequest(
        selection=ModelSelection("openai", "gpt-5", "markdown"),
        system_prompt="system",
        user_text="generate",
        document_paths=(),
        timeout_seconds=1,
        cleanup_documents=True,
        request_params={},
        background=False,
        poll_interval=0,
        log=None,
        structured_output=StructuredOutputSpec(
            name="discovered_schema",
            schema=load_contract("private_health/discovered_schema"),
        ),
    )


class StructuredOutputTest(unittest.TestCase):
    def test_valid_output_returns_after_one_attempt(self) -> None:
        import json

        provider = SequenceProvider([json.dumps(VALID_DISCOVERED_SCHEMA)])

        result = run_structured_output(
            provider,
            request(),
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        self.assertEqual(result.data, VALID_DISCOVERED_SCHEMA)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(len(provider.requests), 1)

    def test_validation_errors_are_sent_to_a_repair_attempt(self) -> None:
        import json

        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid["fields"] = [dict(VALID_DISCOVERED_SCHEMA["fields"][0])]
        invalid["fields"][0]["values"] = "wrong"
        provider = SequenceProvider([
            json.dumps(invalid),
            json.dumps(VALID_DISCOVERED_SCHEMA),
        ])

        result = run_structured_output(
            provider,
            request(),
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        self.assertEqual(result.data, VALID_DISCOVERED_SCHEMA)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("$.fields[0].values", provider.requests[1].user_text)
        self.assertIn("Return the complete corrected JSON object", provider.requests[1].user_text)

    def test_business_validation_error_is_repaired(self) -> None:
        import json

        invalid = dict(VALID_DISCOVERED_SCHEMA)
        invalid["fields"] = list(VALID_DISCOVERED_SCHEMA["fields"]) * 2
        provider = SequenceProvider([
            json.dumps(invalid),
            json.dumps(VALID_DISCOVERED_SCHEMA),
        ])

        run_structured_output(
            provider,
            request(),
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        self.assertIn("duplicate field name", provider.requests[1].user_text)

    def test_business_repair_keeps_large_json_without_resending_pdf_context(self) -> None:
        import json

        invalid = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        product_type = next(
            field for field in invalid["fields"] if field["name"] == "product_type"
        )
        product_type["required"] = False
        invalid["notes"] = ["x" * 15000 + "TAIL_MARKER"]
        provider = SequenceProvider([
            json.dumps(invalid),
            json.dumps(VALID_DISCOVERED_SCHEMA),
        ])
        source_request = replace(
            request(), user_text="PDF_SOURCE_CONTEXT_SHOULD_NOT_BE_RESENT"
        )

        run_structured_output(
            provider,
            source_request,
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        repair_text = provider.requests[1].user_text
        self.assertNotIn("PDF_SOURCE_CONTEXT_SHOULD_NOT_BE_RESENT", repair_text)
        self.assertIn("TAIL_MARKER", repair_text)
        self.assertIn('field whose name is exactly "product_type"', repair_text)
        self.assertIn('"required": true', repair_text)

    def test_business_repair_explains_canonical_extras_service_key(self) -> None:
        import json

        invalid = json.loads(json.dumps(VALID_DISCOVERED_SCHEMA))
        invalid["extras_services"] = [
            {"canonical_name": "GeneralDental", "description": "Dental", "aliases": []}
        ]
        invalid["fields"].append({
            "name": "extras_benefits", "type": "list[object]",
            "description": "Benefits", "applies_to": ["extras"],
            "required": False, "values": [], "aliases": [], "enum_ref": None,
            "unique_items": False,
            "item_fields": [{
                "name": "service", "type": "enum", "required": True,
                "description": None, "values": [], "enum_ref": "extras_services",
            }],
        })
        provider = SequenceProvider([
            json.dumps(invalid),
            json.dumps(VALID_DISCOVERED_SCHEMA),
        ])

        run_structured_output(
            provider,
            request(),
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        repair_text = provider.requests[1].user_text
        self.assertIn('field named exactly "extras_benefits"', repair_text)
        self.assertIn('service_name: type="enum", required=true', repair_text)
        self.assertIn('enum_ref="extras_services"', repair_text)
        self.assertIn('"service" to "service_name"', repair_text)

    def test_three_invalid_attempts_fail_closed(self) -> None:
        provider = SequenceProvider(["not json", "[]", "{}"])

        with self.assertRaises(StructuredOutputFailure) as caught:
            run_structured_output(
                provider,
                request(),
                data_contract="private_health/discovered_schema",
                business_validator=validate_schema_mapping,
            )

        failure = caught.exception.result
        self.assertIsNone(failure.data)
        self.assertEqual(len(failure.attempts), 3)
        self.assertEqual(len(provider.requests), 3)
        self.assertTrue(failure.errors)

    def test_non_standard_numbers_and_duplicate_keys_are_repaired(self) -> None:
        import json

        provider = SequenceProvider([
            '{"amount": NaN}',
            '{"amount": 1, "amount": 2}',
            '{"amount": 3}',
        ])
        numeric_request = replace(
            request(),
            structured_output=StructuredOutputSpec(
                name="numeric_result",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount"],
                    "properties": {"amount": {"type": "number"}},
                },
            ),
        )

        result = run_structured_output(
            provider,
            numeric_request,
            data_contract_schema=numeric_request.structured_output.schema,
        )

        self.assertEqual(result.data, json.loads('{"amount": 3}'))
        self.assertEqual(len(result.attempts), 3)

    def test_completed_provider_failure_preserves_usage_without_retrying(self) -> None:
        response = ModelResponse(
            text="",
            provider="anthropic",
            model="claude-sonnet-4-5",
            response_id="refusal-1",
            usage=ModelUsage(input_tokens=8, output_tokens=3, total_tokens=11),
        )

        class RefusingProvider:
            def generate(self, request: ProviderRequest) -> ModelResponse:
                raise ProviderResponseError("Provider refused structured output.", response)

        with self.assertRaises(StructuredOutputFailure) as caught:
            run_structured_output(
                RefusingProvider(),
                request(),
                data_contract="private_health/discovered_schema",
            )

        failure = caught.exception.result
        self.assertEqual(len(failure.attempts), 1)
        self.assertIs(failure.attempts[0].response, response)
        self.assertEqual(failure.attempts[0].response.usage.total_tokens, 11)
        self.assertIn("refused", failure.errors[0]["message"])

    def test_empty_provider_response_is_retried_and_can_recover(self) -> None:
        import json

        empty_response = ModelResponse(
            text="",
            provider="deepseek",
            model="deepseek-v4-pro",
            response_id="empty-1",
            usage=ModelUsage(input_tokens=8, output_tokens=3, total_tokens=11),
        )

        class EmptyThenValidProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.requests: list[ProviderRequest] = []

            def generate(self, provider_request: ProviderRequest) -> ModelResponse:
                self.calls += 1
                self.requests.append(provider_request)
                if self.calls == 1:
                    raise ProviderResponseError(
                        "DeepSeek structured output response was empty.",
                        empty_response,
                    )
                return ModelResponse(
                    text=json.dumps(VALID_DISCOVERED_SCHEMA),
                    provider="deepseek",
                    model="deepseek-v4-pro",
                )

        provider = EmptyThenValidProvider()
        result = run_structured_output(
            provider,
            request(),
            data_contract="private_health/discovered_schema",
            business_validator=validate_schema_mapping,
        )

        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(provider.calls, 2)
        self.assertIn("response was empty", provider.requests[1].user_text)


if __name__ == "__main__":
    unittest.main()
