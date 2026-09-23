import csv
from pathlib import Path

INPUT = Path("experiments/threshold_tradeoff_summary.csv")
OUTPUT = Path("experiments/threshold_tradeoff_metrics.csv")

rows_out = []

with open(INPUT, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)

    for row in reader:
        setting = row["setting"]

        total_fields = int(row["total_fields"])
        review_queue = int(row["review_queue"])
        useful_fields = int(row["human_accept_or_edit"])
        useful_as_noise = int(row["useful_as_noise"])
        rejected_as_noise = int(row["rejected_as_noise"])

        # There are 50 rejected fields in the human review results
        rejected_fields = 50

        review_rate = review_queue / total_fields if total_fields else 0

        useful_field_loss_rate = (
            useful_as_noise / useful_fields
            if useful_fields else 0
        )

        rejected_field_filter_rate = (
            rejected_as_noise / rejected_fields
            if rejected_fields else 0
        )

        rows_out.append({
            "setting": setting,
            "review_queue": review_queue,
            "review_rate_pct": round(review_rate * 100, 1),
            "useful_fields_lost": useful_as_noise,
            "useful_field_loss_rate_pct": round(useful_field_loss_rate * 100, 1),
            "rejected_fields_filtered": rejected_as_noise,
            "rejected_field_filter_rate_pct": round(rejected_field_filter_rate * 100, 1),
        })

with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "setting",
            "review_queue",
            "review_rate_pct",
            "useful_fields_lost",
            "useful_field_loss_rate_pct",
            "rejected_fields_filtered",
            "rejected_field_filter_rate_pct",
        ],
    )

    writer.writeheader()
    writer.writerows(rows_out)

print("\nTHRESHOLD METRICS")
print("=" * 80)

for row in rows_out:
    print(f"\n{row['setting'].upper()}")
    print("Review queue:", row["review_queue"])
    print("Review rate:", row["review_rate_pct"], "%")
    print("Useful fields lost:", row["useful_fields_lost"])
    print("Useful-field loss rate:", row["useful_field_loss_rate_pct"], "%")
    print("Rejected fields filtered:", row["rejected_fields_filtered"])
    print("Rejected-field filtering rate:", row["rejected_field_filter_rate_pct"], "%")

print("\nSaved to:")
print(OUTPUT)