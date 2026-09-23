import csv
from collections import defaultdict
from pathlib import Path


INPUT = Path(
    "experiments/scoring_method_results.csv"
)

OUTPUT = Path(
    "experiments/scoring_method_metrics.csv"
)


stats = defaultdict(lambda: {
    "total_fields": 0,

    "core": 0,
    "conditional": 0,
    "candidate": 0,
    "noise": 0,

    "review_queue": 0,

    "human_useful": 0,
    "human_rejected": 0,

    "useful_as_core": 0,
    "rejected_as_core": 0,

    "useful_as_noise": 0,
    "rejected_as_noise": 0,
})


with open(INPUT, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)

    for row in reader:

        method = row["method"]
        decision = row["decision"]
        human_review = row["human_review"]

        s = stats[method]

        s["total_fields"] += 1
        s[decision] += 1

        # Fields not automatically core/noise still need review
        if decision in {"conditional", "candidate"}:
            s["review_queue"] += 1

        useful = human_review in {"accept", "edit"}
        rejected = human_review == "reject"

        if useful:
            s["human_useful"] += 1

        if rejected:
            s["human_rejected"] += 1

        if useful and decision == "core":
            s["useful_as_core"] += 1

        if rejected and decision == "core":
            s["rejected_as_core"] += 1

        if useful and decision == "noise":
            s["useful_as_noise"] += 1

        if rejected and decision == "noise":
            s["rejected_as_noise"] += 1


rows_out = []

for method, s in stats.items():

    review_rate = (
        s["review_queue"] / s["total_fields"]
        if s["total_fields"] else 0
    )

    useful_loss_rate = (
        s["useful_as_noise"] / s["human_useful"]
        if s["human_useful"] else 0
    )

    rejected_filter_rate = (
        s["rejected_as_noise"] / s["human_rejected"]
        if s["human_rejected"] else 0
    )

    rows_out.append({
        "method": method,

        "core": s["core"],
        "conditional": s["conditional"],
        "candidate": s["candidate"],
        "noise": s["noise"],

        "review_queue": s["review_queue"],
        "review_rate_pct": round(
            review_rate * 100, 1
        ),

        "useful_fields_lost":
            s["useful_as_noise"],

        "useful_field_loss_rate_pct":
            round(useful_loss_rate * 100, 1),

        "rejected_fields_filtered":
            s["rejected_as_noise"],

        "rejected_field_filter_rate_pct":
            round(rejected_filter_rate * 100, 1),

        "useful_fields_promoted_core":
            s["useful_as_core"],

        "rejected_fields_promoted_core":
            s["rejected_as_core"],
    })


with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=rows_out[0].keys(),
    )

    writer.writeheader()
    writer.writerows(rows_out)


print("\nSCORING METHOD METRICS")
print("=" * 80)

for row in rows_out:

    print(f"\n{row['method'].upper()}")
    print("-" * 50)

    print(
        "Decisions:",
        f"core={row['core']},",
        f"conditional={row['conditional']},",
        f"candidate={row['candidate']},",
        f"noise={row['noise']}"
    )

    print(
        "Review rate:",
        row["review_rate_pct"],
        "%"
    )

    print(
        "Useful-field loss rate:",
        row["useful_field_loss_rate_pct"],
        "%"
    )

    print(
        "Rejected-field filtering rate:",
        row[
            "rejected_field_filter_rate_pct"
        ],
        "%"
    )


print("\nSaved to:")
print(OUTPUT)