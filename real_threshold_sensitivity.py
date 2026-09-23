import json
import csv
from pathlib import Path

BASE = Path(
    "experiments/real_travel_output/schema-gpt55/round_1/consensus"
)

FIELD_FREQUENCY_FILE = BASE / "field_frequency.json"
REVIEW_DECISIONS_FILE = BASE / "review_decisions.json"

# Three threshold settings
SETTINGS = {
    "lenient": {
        "core": 0.6,
        "conditional": 0.4,
        "candidate": 0.2,
    },
    "current": {
        "core": 0.8,
        "conditional": 0.5,
        "candidate": 0.2,
    },
    "strict": {
        "core": 1.0,
        "conditional": 0.8,
        "candidate": 0.4,
    },
}


def classify(ratio, thresholds):
    if ratio >= thresholds["core"]:
        return "core"
    elif ratio >= thresholds["conditional"]:
        return "conditional"
    elif ratio >= thresholds["candidate"]:
        return "candidate"
    else:
        return "noise"


# Load real field-frequency data
with open(FIELD_FREQUENCY_FILE, "r", encoding="utf-8") as f:
    frequency_data = json.load(f)

fields = frequency_data["data"]["fields"]

# Load human review decisions
with open(REVIEW_DECISIONS_FILE, "r", encoding="utf-8") as f:
    review_data = json.load(f)

review_lookup = {}

for item in review_data["data"]["decisions"]:
    field_id = item["id"]

    if field_id.startswith("field:"):
        field_name = field_id.replace("field:", "", 1)
        review_lookup[field_name] = item["action"]


rows = []

for setting_name, thresholds in SETTINGS.items():

    counts = {
        "core": 0,
        "conditional": 0,
        "candidate": 0,
        "noise": 0,
    }

    print("\n" + "=" * 70)
    print(setting_name.upper())
    print("=" * 70)

    for field in fields:

        field_name = field["field"]

        frequency_text = field["frequency"]
        numerator, denominator = frequency_text.split("/")

        ratio = int(numerator) / int(denominator)

        new_decision = classify(
            ratio,
            thresholds,
        )

        counts[new_decision] += 1

        human_action = review_lookup.get(
            field_name,
            "not_reviewed",
        )

        rows.append({
            "setting": setting_name,
            "field": field_name,
            "frequency": frequency_text,
            "ratio": ratio,
            "original_decision": field["decision"],
            "new_decision": new_decision,
            "average_confidence": field.get(
                "average_confidence"
            ),
            "human_review": human_action,
        })

    print("Core:", counts["core"])
    print("Conditional:", counts["conditional"])
    print("Candidate:", counts["candidate"])
    print("Noise:", counts["noise"])


OUTPUT = Path(
    "experiments/real_threshold_sensitivity_results.csv"
)

with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "setting",
            "field",
            "frequency",
            "ratio",
            "original_decision",
            "new_decision",
            "average_confidence",
            "human_review",
        ],
    )

    writer.writeheader()
    writer.writerows(rows)


print("\nSaved results to:")
print(OUTPUT)