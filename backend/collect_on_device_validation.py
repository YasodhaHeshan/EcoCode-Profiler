"""Collect paired RAPL and TDP measurements for on-device validation.

Run this script on a supported Intel Linux host. The command map must contain
one command per benchmark; ``{input_size}`` is replaced from the CSV row.
"""
import argparse
import csv
import glob
import json
import os
import platform
import subprocess
import time

import psutil


RAPL_ROOT = "/sys/class/powercap/intel-rapl"


def read_rapl_counters():
    counters = {}
    paths = glob.glob(os.path.join(RAPL_ROOT, "intel-rapl:*") or RAPL_ROOT)
    if os.path.isfile(os.path.join(RAPL_ROOT, "energy_uj")):
        paths.append(RAPL_ROOT)
    for path in dict.fromkeys(paths):
        energy_path = os.path.join(path, "energy_uj")
        range_path = os.path.join(path, "max_energy_range_uj")
        name_path = os.path.join(path, "name")
        if not os.path.isfile(energy_path):
            continue
        try:
            name = open(name_path, encoding="utf-8").read().strip() if os.path.isfile(name_path) else ""
            if name and "package" not in name.lower():
                continue
            counters[path] = {
                "energy_uj": int(open(energy_path, encoding="ascii").read().strip()),
                "max_range_uj": int(open(range_path, encoding="ascii").read().strip()),
            }
        except (OSError, ValueError):
            continue
    return counters


def rapl_delta_joules(start, end):
    total_uj = 0
    for path, before in start.items():
        after = end.get(path)
        if after is None:
            continue
        delta = after["energy_uj"] - before["energy_uj"]
        if delta < 0:
            delta += before["max_range_uj"]
        total_uj += delta
    return total_uj / 1_000_000 if total_uj else None


def load_commands(path):
    with open(path, encoding="utf-8") as command_file:
        commands = json.load(command_file)
    if not isinstance(commands, dict):
        raise ValueError("Command map must be a JSON object")
    return commands


def command_for(command_map, benchmark, input_size):
    command = command_map.get(benchmark)
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        raise ValueError(f"Missing command list for benchmark {benchmark!r}")
    return [part.replace("{input_size}", str(input_size)) for part in command]


def collect_row(row, command, tdp_w, timeout):
    if platform.system() != "Linux":
        raise RuntimeError("RAPL collection requires Linux")
    start_counters = read_rapl_counters()
    if not start_counters:
        raise RuntimeError(f"No readable package RAPL counters under {RAPL_ROOT}")
    psutil.cpu_percent(None)
    started = time.perf_counter()
    completed = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    elapsed = time.perf_counter() - started
    utilization = psutil.cpu_percent(None)
    rapl_energy = rapl_delta_joules(start_counters, read_rapl_counters())
    if rapl_energy is None or rapl_energy <= 0:
        raise RuntimeError("RAPL returned no positive package-energy delta")
    tdp_energy = tdp_w * elapsed * utilization / 100
    mape = abs(tdp_energy - rapl_energy) / rapl_energy * 100
    row.update({
        "elapsed_s": f"{elapsed:.6f}",
        "cpu_utilization_percent": f"{utilization:.3f}",
        "tdp_estimated_energy_j": f"{tdp_energy:.6f}",
        "rapl_energy_j": f"{rapl_energy:.6f}",
        "mape_percent": f"{mape:.4f}",
        "status": "complete" if completed.returncode == 0 else f"command_failed_{completed.returncode}",
    })


def main():
    parser = argparse.ArgumentParser(description="Collect paired RAPL/TDP validation data")
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "training_data", "on_device_validation_template.csv"))
    parser.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "training_data", "on_device_validation_results.csv"))
    parser.add_argument("--commands", required=True, help="JSON map of benchmark names to command argument arrays")
    parser.add_argument("--tdp", type=float, default=65.0, help="CPU TDP in watts")
    parser.add_argument("--timeout", type=float, default=3600, help="Maximum seconds per benchmark run")
    args = parser.parse_args()
    if args.tdp <= 0 or args.timeout <= 0:
        parser.error("--tdp and --timeout must be positive")
    command_map = load_commands(args.commands)
    with open(args.input, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fieldnames = reader.fieldnames
    completed = 0
    for row in rows:
        if row.get("status") == "complete":
            continue
        command = command_for(command_map, row["benchmark"], row["input_size"])
        print(f"Running {row['run_id']}: {' '.join(command)}", flush=True)
        try:
            collect_row(row, command, args.tdp, args.timeout)
            completed += 1
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            row["status"] = f"error: {error}"
            print(f"  {row['status']}", flush=True)
    with open(args.output, "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"output": args.output, "completed": completed, "total": len(rows)}, indent=2))


if __name__ == "__main__":
    main()