"""Write a generic Actions summary without reading or exposing credentials."""
import json
import os
from pathlib import Path


def main():
    lines = ["## AirTicketMoniting", "", "Download `flight-report` for the HTML report and logs.", ""]
    lines.append("WeChat notifications: " + ("configured" if os.environ.get("HAS_NOTIFIER") == "true" else "not configured"))
    path = Path("docs/data/latest.json")
    if path.exists() and os.environ.get("ACTIVE_TRIP") == "true":
        results = json.loads(path.read_text(encoding="utf-8"))["data"]["results"]
        lines.extend(["", "| Date | Route | Fare (CNY) | Flights | Depart | Arrive |",
                      "|---|---|---:|---|---|---|"])
        for row in sorted(results, key=lambda r: (r["date"], r["price"])):
            lines.append(f"| {row['date']} | {row['from']} → {row['to']} | {row['price']:.0f} | "
                         f"{row['flight_no']} | {row['depart_time']} | {row['arrive_time']} |")
        if not results:
            lines.append("No qualifying fare found in this run.")
    else:
        lines.extend(["", "No active itinerary. Set origin, destination, dates and cabin in `trip.yaml`."])
    lines.extend(["", "Alerts are sent for the first qualifying fare, then only for lower fares.",
                  "Times are local to each airport; fares must be confirmed on the booking page."])
    output = os.environ.get("GITHUB_STEP_SUMMARY")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
