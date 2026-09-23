import csv
from pathlib import Path

import matplotlib.pyplot as plt


INPUT = Path(
    "experiments/scoring_method_metrics.csv"
)

OUTPUT = Path(
    "experiments/scoring_method_comparison.png"
)


methods = []
review_rates = []
useful_loss_rates = []
rejected_filter_rates = []


with open(INPUT, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)

    for row in reader:

        name = row["method"]

        if name == "frequency_only":
            display_name = "Frequency Only"
        else:
            display_name = "Frequency × Confidence"

        methods.append(display_name)

        review_rates.append(
            float(row["review_rate_pct"])
        )

        useful_loss_rates.append(
            float(
                row[
                    "useful_field_loss_rate_pct"
                ]
            )
        )

        rejected_filter_rates.append(
            float(
                row[
                    "rejected_field_filter_rate_pct"
                ]
            )
        )


x = range(len(methods))
width = 0.25


plt.figure(figsize=(8, 5))


plt.bar(
    [i - width for i in x],
    review_rates,
    width=width,
    label="Manual review rate",
)


plt.bar(
    x,
    rejected_filter_rates,
    width=width,
    label="Rejected-field filtering rate",
)


plt.bar(
    [i + width for i in x],
    useful_loss_rates,
    width=width,
    label="Useful-field loss rate",
)


plt.xticks(x, methods)

plt.ylabel("Percentage (%)")

plt.title(
    "Consensus Scoring Method Comparison"
)

plt.ylim(0, 105)

plt.legend()

plt.tight_layout()

plt.savefig(
    OUTPUT,
    dpi=300
)

plt.show()


print("\nSaved chart to:")
print(OUTPUT)