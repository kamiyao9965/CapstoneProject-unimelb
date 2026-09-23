import csv
from pathlib import Path

import matplotlib.pyplot as plt


INPUT = Path(
    "experiments/threshold_tradeoff_extended_metrics.csv"
)

OUTPUT = Path(
    "experiments/threshold_tradeoff_extended.png"
)


settings = []

review_rates = []
useful_loss_rates = []
rejected_filter_rates = []


with open(
    INPUT,
    "r",
    encoding="utf-8",
) as f:

    reader = csv.DictReader(f)

    for row in reader:

        name = row["setting"]

        # Make names cleaner for chart
        display_name = (
            name
            .replace("_", " ")
            .title()
        )

        settings.append(
            display_name
        )

        review_rates.append(
            float(
                row["review_rate_pct"]
            )
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


x = range(len(settings))

width = 0.25


plt.figure(
    figsize=(10, 5.5)
)


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


plt.xticks(
    x,
    settings
)

plt.ylabel(
    "Percentage (%)"
)

plt.xlabel(
    "Threshold setting"
)

plt.title(
    "Voting Threshold Sensitivity: "
    "Review Workload vs Filtering Risk"
)

plt.ylim(
    0,
    105
)

plt.legend()

plt.tight_layout()


plt.savefig(
    OUTPUT,
    dpi=300
)


plt.show()


print("\nSaved chart to:")
print(OUTPUT)