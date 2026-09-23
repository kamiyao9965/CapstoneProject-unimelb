import csv
from collections import defaultdict
from pathlib import Path


INPUT = Path("experiments/real_threshold_sensitivity_results.csv")
OUTPUT = Path("experiments/threshold_tradeoff_summary.csv")


# Store statistics for each threshold setting
stats = defaultdict(lambda: {
    "total_fields": 0,

    "core": 0,
    "conditional": 0,
    "candidate": 0,
    "noise": 0,

    # Human review workload:
    # fields that are neither automatically promoted as core
    # nor discarded as noise
    "review_queue": 0,

    # Useful fields according to human review
    "human_accept_or_edit": 0,

    # Good outcome:
    # useful field automatically promoted to core
    "useful_as_core": 0,

    # Dangerous outcome:
    # human-rejected field automatically promoted to core
    "rejected_as_core": 0,

    # Dangerous outcome:
    # human-accepted/edited field discarded as noise
    "useful_as_noise": 0,

    # Good filtering:
    # human-rejected field discarded as noise
    "rejected_as_noise": 0,
})


with open(INPUT, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)

    for row in reader:
        setting = row["setting"]
        decision = row["new_decision"]
        human_review = row["human_review"]

        s = stats[setting]

        s["total_fields"] += 1
        s[decision] += 1

        # Treat conditional and candidate as requiring manual review
        if decision in {"conditional", "candidate"}:
            s["review_queue"] += 1

        # Human accept/edit are treated as fields worth keeping
        useful = human_review in {"accept", "edit"}
        rejected = human_review == "reject"

        if useful:
            s["human_accept_or_edit"] += 1

        if useful and decision == "core":
            s["useful_as_core"] += 1

        if rejected and decision == "core":
            s["rejected_as_core"] += 1

        if useful and decision == "noise":
            s["useful_as_noise"] += 1

        if rejected and decision == "noise":
            s["rejected_as_noise"] += 1


fieldnames = [
    "setting",
    "total_fields",
    "core",
    "conditional",
    "candidate",
    "noise",
    "review_queue",
    "human_accept_or_edit",
    "useful_as_core",
    "rejected_as_core",
    "useful_as_noise",
    "rejected_as_noise",
]


with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=fieldnames,
    )

    writer.writeheader()

    for setting, values in stats.items():

        row = {"setting": setting}
        row.update(values)

        writer.writerow(row)


print("\nTHRESHOLD TRADE-OFF SUMMARY")
print("=" * 90)

for setting, s in stats.items():

    print(f"\n{setting.upper()}")
    print("-" * 50)

    print("Total fields:", s["total_fields"])

    print(
        "Schema decisions:",
        f'core={s["core"]},',
        f'conditional={s["conditional"]},',
        f'candidate={s["candidate"]},',
        f'noise={s["noise"]}',
    )

    print(
        "Manual review queue:",
        s["review_queue"]
    )

    print(
        "Useful fields (human accept/edit):",
        s["human_accept_or_edit"]
    )

    print(
        "Useful fields promoted to core:",
        s["useful_as_core"]
    )

    print(
        "Rejected fields promoted to core:",
        s["rejected_as_core"]
    )

    print(
        "Useful fields incorrectly discarded as noise:",
        s["useful_as_noise"]
    )

    print(
        "Rejected fields correctly discarded as noise:",
        s["rejected_as_noise"]
    )


print("\nSaved summary to:")
print(OUTPUT)