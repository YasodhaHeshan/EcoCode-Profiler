"""Evaluate EcoCode's energy baseline and offline suggestion model."""
import argparse
import csv
import hashlib
import json
import os
import random
import statistics
import tempfile

from suggestions import analyse_file


ROOT = os.path.dirname(__file__)
DEFAULT_INTEL_CSV = os.path.join(ROOT, "training_data", "intel_energy_measurements.csv")
DEFAULT_SUGGESTION_CASES = os.path.join(ROOT, "training_data", "code_suggestion_examples.jsonl")
DEFAULT_SUGGESTION_MODEL = os.path.join(ROOT, "training_data", "code_suggestion_model.json")
DEFAULT_ON_DEVICE_CSV = os.path.join(ROOT, "training_data", "on_device_validation_filled.csv")


def median(values):
    return statistics.median(values) if values else None


def read_measurements(path):
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    required = {"cpu_model", "benchmark_name", "input_size", "energy_joules", "runtime_seconds"}
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise ValueError(f"Intel CSV is missing columns: {', '.join(sorted(missing))}")
    for row in rows:
        row["energy_joules"] = float(row["energy_joules"])
        row["runtime_seconds"] = float(row["runtime_seconds"])
        row["power_w"] = row["energy_joules"] / row["runtime_seconds"]
    return rows


def train_lookup(rows):
    """Build a hierarchical lookup model.

    A single measurement is a poor median.  Keeping the sample count lets the
    predictor shrink sparse, highly-specific groups toward their parent group
    instead of treating one observation (possibly from another instrument) as
    ground truth.
    """
    groups = {}
    for row in rows:
        for key in (
            ("cpu_model", "benchmark_name", "input_size"),
            ("cpu_model", "benchmark_name"),
            ("benchmark_name",),
            (),
        ):
            group_key = tuple(row[field] for field in key)
            groups.setdefault(key, {}).setdefault(group_key, []).append(row["power_w"])
    return {
        key: {
            group_key: {"median": median(values), "samples": len(values)}
            for group_key, values in values_by_key.items()
        }
        for key, values_by_key in groups.items()
    }


def predict_power(model, row, shrinkage_samples=3):
    """Predict power with empirical-Bayes shrinkage for sparse groups.

    The estimate at each level is blended with its parent.  Thus a group with
    one observation still contributes signal but cannot dominate the estimate.
    """
    hierarchy = (("cpu_model", "benchmark_name", "input_size"),
                 ("cpu_model", "benchmark_name"), ("benchmark_name",), ())
    estimate = None
    level = None
    for fields in reversed(hierarchy):
        key = tuple(row[field] for field in fields)
        group = model[fields].get(key)
        if not group:
            continue
        if estimate is None:
            estimate = group["median"]
            level = "+".join(fields) or "global"
            continue
        weight = group["samples"] / (group["samples"] + shrinkage_samples)
        estimate = weight * group["median"] + (1 - weight) * estimate
        level = "+".join(fields)
    return estimate, level


def evaluate_intel(path, test_fraction, seed, target_mape=15.0):
    rows = read_measurements(path)
    if len(rows) < 2:
        raise ValueError("At least two Intel measurements are required")
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    test_count = max(1, min(len(rows) - 1, round(len(rows) * test_fraction)))
    test_rows, train_rows = shuffled[:test_count], shuffled[test_count:]
    model = train_lookup(train_rows)
    errors, absolute_percentage_errors, levels = [], [], {}
    for row in test_rows:
        predicted_power, level = predict_power(model, row)
        if predicted_power is None:
            continue
        predicted_energy = predicted_power * row["runtime_seconds"]
        error = predicted_energy - row["energy_joules"]
        errors.append(error)
        absolute_percentage_errors.append(abs(error) / row["energy_joules"] * 100)
        levels[level] = levels.get(level, 0) + 1
    return {
        "status": "complete",
        "rows": len(rows),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "matched_test_rows": len(errors),
        "coverage_percent": round(100 * len(errors) / len(test_rows), 2),
        "mae_joules": round(statistics.mean(abs(error) for error in errors), 6) if errors else None,
        "rmse_joules": round((statistics.mean(error * error for error in errors)) ** 0.5, 6) if errors else None,
        "mape_percent": round(statistics.mean(absolute_percentage_errors), 4) if errors else None,
        "target_mape_percent": target_mape,
        "target_met": bool(errors) and statistics.mean(absolute_percentage_errors) <= target_mape,
        "match_levels": levels,
        "split": {"test_fraction": test_fraction, "seed": seed, "method": "seeded random holdout; test rows excluded from every lookup"},
        "estimator": "hierarchical grouped median with sparse-group shrinkage (3 pseudo-samples)",
        "instrument_note": "The source CSV pools Intel-PCM, RAPL-perf, and PowerTOP readings. MAPE is reported over the pooled data; it is not evidence that those instruments are interchangeable.",
    }


def evaluate_on_device(path, target_mape=15.0):
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    required = {"benchmark", "cpu_model", "tdp_estimated_energy_j", "rapl_energy_j"}
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise ValueError(f"On-device CSV is missing columns: {', '.join(sorted(missing))}")

    errors_by_benchmark = {}
    errors = []
    statuses = {}
    for row in rows:
        tdp_energy = float(row["tdp_estimated_energy_j"])
        rapl_energy = float(row["rapl_energy_j"])
        if tdp_energy <= 0 or rapl_energy <= 0:
            raise ValueError(f"Energy values must be positive for run {row.get('run_id', '<unknown>')}")
        error = abs(tdp_energy - rapl_energy) / rapl_energy * 100
        errors.append(error)
        errors_by_benchmark.setdefault(row["benchmark"], []).append(error)
        statuses[row.get("status", "")] = statuses.get(row.get("status", ""), 0) + 1

    benchmark_mape = {
        benchmark: round(statistics.mean(values), 4)
        for benchmark, values in sorted(errors_by_benchmark.items())
    }
    # A benchmark-specific coefficient is fit only from the other runs of the
    # same benchmark. This is a genuine leave-one-run-out prediction, rather
    # than fitting and scoring a run on itself. It makes the calibration
    # requirement explicit: a new benchmark needs at least two warm-up runs.
    calibrated_errors = []
    calibrated_by_benchmark = {}
    for benchmark, benchmark_rows in sorted(
        ((name, [row for row in rows if row["benchmark"] == name])
         for name in errors_by_benchmark), key=lambda item: item[0]):
        if len(benchmark_rows) < 3:
            continue
        for index, held_out in enumerate(benchmark_rows):
            training = benchmark_rows[:index] + benchmark_rows[index + 1:]
            coefficient = median(
                float(item["rapl_energy_j"]) / float(item["tdp_estimated_energy_j"])
                for item in training
            )
            predicted = coefficient * float(held_out["tdp_estimated_energy_j"])
            error = abs(predicted - float(held_out["rapl_energy_j"])) / float(held_out["rapl_energy_j"]) * 100
            calibrated_errors.append(error)
            calibrated_by_benchmark.setdefault(benchmark, []).append(error)
    calibrated_mape = statistics.mean(calibrated_errors) if calibrated_errors else None
    return {
        "status": "complete",
        "source": os.path.basename(path),
        "rows": len(errors),
        "cpu_models": sorted({row["cpu_model"] for row in rows}),
        "data_statuses": statuses,
        "mean_mape_percent": round(statistics.mean(errors), 4) if errors else None,
        "median_mape_percent": round(statistics.median(errors), 4) if errors else None,
        "target_mape_percent": target_mape,
        "target_met": bool(errors) and statistics.mean(errors) <= target_mape,
        "runs_within_target": sum(error <= target_mape for error in errors),
        "benchmark_mean_mape_percent": benchmark_mape,
        "method": "mean absolute percentage error: abs(TDP - RAPL) / RAPL * 100",
        "benchmark_calibrated_leave_one_run_out": {
            "status": "complete" if calibrated_errors else "insufficient_replicates",
            "rows": len(calibrated_errors),
            "mean_mape_percent": round(calibrated_mape, 4) if calibrated_mape is not None else None,
            "median_mape_percent": round(statistics.median(calibrated_errors), 4) if calibrated_errors else None,
            "target_mape_percent": target_mape,
            "target_met": bool(calibrated_errors) and calibrated_mape <= target_mape,
            "runs_within_target": sum(error <= target_mape for error in calibrated_errors),
            "benchmark_mean_mape_percent": {key: round(statistics.mean(value), 4) for key, value in calibrated_by_benchmark.items()},
            "method": "leave-one-run-out median(RAPL/TDP) benchmark calibration; the held-out run is never used to fit its coefficient",
            "deployment_requirement": "Collect at least two same-instrument warm-up runs for each benchmark/CPU/OS configuration before using the calibrated estimate.",
        },
    }


def evaluate_suggestions(cases_path, model_path):
    cases = []
    with open(cases_path, encoding="utf-8") as source:
        for line in source:
            if line.strip():
                cases.append(json.loads(line))
    true_positives = false_positives = false_negatives = 0
    by_label = {}
    with tempfile.TemporaryDirectory(prefix="ecocode-eval-") as directory:
        for index, case in enumerate(cases):
            script_path = os.path.join(directory, f"case_{index}.py")
            with open(script_path, "w", encoding="utf-8") as script:
                script.write(case["code"] + "\n")
            result = analyse_file(script_path, model_path=model_path)
            predicted = [item["pattern"] for item in result.get("suggestions", [])]
            expected = case["label"]
            if expected == "no_suggestion":
                false_positives += len(predicted)
                correct = not predicted
            else:
                matching = sum(pattern == expected for pattern in predicted)
                true_positives += matching
                false_positives += len(predicted) - matching
                false_negatives += int(matching == 0)
                correct = matching > 0
            stats = by_label.setdefault(expected, {"cases": 0, "correct_cases": 0, "predicted_suggestions": 0})
            stats["cases"] += 1
            stats["correct_cases"] += int(correct)
            stats["predicted_suggestions"] += len(predicted)
    total_predictions = true_positives + false_positives
    precision = true_positives / total_predictions if total_predictions else None
    recall_denominator = true_positives + false_negatives
    recall = true_positives / recall_denominator if recall_denominator else None
    f1_score = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {
        "status": "complete",
        "cases": len(cases),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision_percent": round(100 * precision, 2) if precision is not None else None,
        "target_precision_percent": 80.0,
        "target_met": bool(precision) and 100 * precision >= 80.0,
        "recall_percent": round(100 * recall, 2) if recall is not None else None,
        "f1_score_percent": round(100 * f1_score, 2),
        "case_accuracy_percent": round(100 * sum(item["correct_cases"] for item in by_label.values()) / len(cases), 2) if cases else None,
        "by_label": by_label,
        "corpus": os.path.basename(cases_path),
    }


def evaluate_calibration(path, tdp_w=65.0):
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    actual_key = next((key for key in ("energy_joules", "actual_energy_joules", "measured_energy_joules") if key in (rows[0] if rows else {})), None)
    estimate_key = next((key for key in ("estimated_energy_joules", "baseline_energy_joules", "tdp_energy_joules") if key in (rows[0] if rows else {})), None)
    if not actual_key and rows and "energy_j" in rows[0] and "cpu_secs_mean" in rows[0]:
        pairs = [(tdp_w * float(row["cpu_secs_mean"]), float(row["energy_j"])) for row in rows]
        actual_key = "energy_j"
        estimate_key = "tdp_w * cpu_secs_mean"
        predictor = f"{tdp_w:g} W * cpu_secs_mean"
    else:
        pairs = [(float(row[estimate_key]), float(row[actual_key])) for row in rows if float(row[estimate_key]) > 0] if actual_key and estimate_key else []
        predictor = estimate_key
    if not actual_key or not estimate_key:
        return {"status": "pending", "reason": "CSV must provide measured energy and estimated energy columns", "path": path}
    if not pairs or any(estimate <= 0 or actual <= 0 for estimate, actual in pairs):
        raise ValueError("Calibration energy values must be positive")
    coefficient = sum(estimate * actual for estimate, actual in pairs) / sum(estimate * estimate for estimate, _ in pairs)
    return {
        "status": "complete",
        "rows": len(pairs),
        "calibration_coefficient": round(coefficient, 8),
        "method": "ordinary least squares through origin",
        "predictor": predictor,
        "tdp_w": tdp_w if "cpu_secs_mean" in predictor else None,
        "source": path,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate EcoCode models")
    parser.add_argument("--intel-csv", default=DEFAULT_INTEL_CSV)
    parser.add_argument("--suggestion-cases", default=DEFAULT_SUGGESTION_CASES)
    parser.add_argument("--suggestion-model", default=DEFAULT_SUGGESTION_MODEL)
    parser.add_argument("--on-device-csv", default=DEFAULT_ON_DEVICE_CSV)
    parser.add_argument("--calibration-csv")
    parser.add_argument("--calibration-tdp", type=float, default=65.0)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", help="Write the JSON report to this path")
    args = parser.parse_args()
    report = {
        "evaluation_protocol": {
            "stage_1": "Estimation accuracy on genuinely held-out Intel measurement rows; direct answer to RQ1.",
            "stage_2": "Paired on-device RAPL validation against a <= 15% MAPE target; raw and leave-one-run-out calibrated estimates are both reported.",
            "stage_3": "Offline suggestion precision against a >= 80% target.",
            "stage_4": "Indicative usability signal; not a substitute for a participant study.",
        },
        "stage_1_estimation_accuracy": evaluate_intel(args.intel_csv, args.test_fraction, args.seed),
        "stage_3_suggestion_precision": evaluate_suggestions(args.suggestion_cases, args.suggestion_model),
        "offline_calibration": evaluate_calibration(args.calibration_csv, args.calibration_tdp) if args.calibration_csv else {"status": "pending", "reason": "Energy-Languages CSV not supplied"},
        "stage_2_on_device_rapl_validation": evaluate_on_device(args.on_device_csv),
        "stage_4_usability_assessment": {"status": "pending", "reason": "Indicative usability requires developer participants and a pre-specified Likert questionnaire"},
    }
    serialized = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as output:
            output.write(serialized + "\n")
    print(serialized)


if __name__ == "__main__":
    main()
