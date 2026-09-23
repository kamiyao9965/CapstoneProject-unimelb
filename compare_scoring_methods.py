import csv
from pathlib import Path


INPUT = Path(
    "experiments/real_threshold_sensitivity_extended_results.csv"
)

OUTPUT = Path(
    "experiments/scoring_method_results.csv"
)


# Use only one copy of each real field.
# The "current" rows contain the same underlying 59 fields,
# frequencies, confidence scores and human review decisions.
rows = []

with open(INPUT, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)

    for row in reader:
        if row["setting"] == "current":
            rows.append(row)


# Keep the existing/current decision thresholds fixed.
CORE_THRESHOLD = 0.8
CONDITIONAL_THRESHOLD = 0.5
CANDIDATE_THRESHOLD = 0.2


def classify(score):
    if score >= CORE_THRESHOLD:
        return "core"
    elif score >= CONDITIONAL_THRESHOLD:
        return "conditional"
    elif score >= CANDIDATE_THRESHOLD:
        return "candidate"
    else:
        return "noise"


results = []


for row in rows:

    frequency_ratio = float(row["ratio"])
    confidence = float(row["average_confidence"])

    # Method A: existing frequency-only score
    frequency_score = frequency_ratio

    # Method B: frequency weighted by model confidence
    weighted_score = frequency_ratio * confidence

    for method, score in [
        ("frequency_only", frequency_score),
        ("frequency_x_confidence", weighted_score),
    ]:

        decision = classify(score)

        results.append({
            "method": method,
            "field": row["field"],
            "frequency": row["frequency"],
            "frequency_ratio": frequency_ratio,
            "average_confidence": confidence,
            "score": round(score, 4),
            "decision": decision,
            "human_review": row["human_review"],
        })


with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "method",
            "field",
            "frequency",
            "frequency_ratio",
            "average_confidence",
            "score",
            "decision",
            "human_review",
        ],
    )

    writer.writeheader()
    writer.writerows(results)


print("\nSCORING METHOD COMPARISON")
print("=" * 70)


for method in [
    "frequency_only",
    "frequency_x_confidence",
]:

    method_rows = [
        r for r in results
        if r["method"] == method
    ]

    counts = {
        "core": 0,
        "conditional": 0,
        "candidate": 0,
        "noise": 0,
    }

    for r in method_rows:
        counts[r["decision"]] += 1

    print(f"\n{method.upper()}")
    print("-" * 50)
    print("Core:", counts["core"])
    print("Conditional:", counts["conditional"])
    print("Candidate:", counts["candidate"])
    print("Noise:", counts["noise"])


print("\nSaved to:")
print(OUTPUT)