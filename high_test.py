"""High-level EcoCode Profiler stress test with intentional anti-patterns."""

import json
import re
import tempfile
from pathlib import Path


def compare_customer_orders(customers, orders):
    """Nested-loop membership/search pattern."""
    matched_orders = []
    for customer in customers:
        for order in orders:
            if order["customer_id"] == customer["id"]:
                matched_orders.append(order)
    return matched_orders


def generate_large_text(limit=10_000):
    """Repeated string concatenation."""
    text = ""
    for index in range(limit):
        text += "Record " + str(index) + " was processed successfully.\n"
    return text


def inefficient_number_processing(values):
    """Repeated calculations and list membership checks."""
    results = []
    for value in values:
        # Recreates the same list on every iteration.
        if value in [5, 10, 15, 20, 25, 30, 35, 40]:
            results.append(value)

        # Loop-invariant calculation repeated unnecessarily.
        scale = sum(range(1_000))
        results.append(value * scale)
    return results


def sort_to_find_extremes(values):
    """Sorting an entire collection just to find min/max."""
    ordered_values = sorted(values)
    return ordered_values[0], ordered_values[-1]


def process_logs(log_file):
    """Reads a full file into memory and compiles regex repeatedly."""
    content = log_file.read_text(encoding="utf-8")
    matches = []
    for line in content.splitlines():
        # Regex compilation inside the loop.
        pattern = re.compile(r"ERROR|WARNING")
        if pattern.search(line):
            matches.append(line)
    return matches


def repeatedly_write_json(records, output_dir):
    """Repeated JSON serialisation and file opening."""
    output_dir.mkdir(exist_ok=True)
    for index, record in enumerate(records):
        output_file = output_dir / f"record_{index}.json"
        with output_file.open("w", encoding="utf-8") as file:
            file.write(json.dumps(record))


def build_lookup_table(items):
    """Manual dictionary creation."""
    lookup = {}
    for item in items:
        lookup[item["id"]] = item["name"]
    return lookup


def run_high_level_test():
    customers = [{"id": index, "name": f"Customer {index}"} for index in range(300)]
    orders = [
        {"id": index, "customer_id": index % 300, "amount": index * 10}
        for index in range(3_000)
    ]
    values = list(range(1_000))
    records = [{"id": index, "value": index * 2} for index in range(100)]

    compare_customer_orders(customers, orders)
    generate_large_text()
    inefficient_number_processing(values)
    sort_to_find_extremes(values)
    build_lookup_table(customers)

    with tempfile.TemporaryDirectory() as temporary_directory:
        temp_path = Path(temporary_directory)
        log_file = temp_path / "application.log"
        log_file.write_text(
            "\n".join(f"INFO: event {index}" for index in range(500))
            + "\nWARNING: low disk space\nERROR: connection failed",
            encoding="utf-8",
        )
        process_logs(log_file)
        repeatedly_write_json(records, temp_path / "json_records")


run_high_level_test()
