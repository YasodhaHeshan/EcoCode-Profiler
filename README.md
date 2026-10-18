# EcoCode Profiler

EcoCode Profiler is a Visual Studio Code extension for measuring and estimating the energy use and carbon impact of Python programs. It combines Linux Intel RAPL package measurements, a trained Intel energy baseline, and a software estimator, then presents results alongside code suggestions.

## Features

- **Energy measurement and estimation:** On supported Linux systems, reads Intel RAPL package energy counters before and after a run. It corrects a counter wrap using the domain's `max_energy_range_uj` value. If RAPL is unavailable, it tries the supplied Intel energy baseline, then falls back to a TDP-based estimate.
- **Intel energy baseline:** `backend/training_data/intel_energy_model.json` contains grouped median measurements trained from 1,000 rows, covering 10 benchmark names and 22 CPU models. The model can match by benchmark, CPU model, and optionally input size. It is a lookup baseline, not a general-purpose regression model.
- **Function annotations:** Adds estimated joules and carbon equivalent next to function definitions.
- **Energy summary:** The **EcoCode Summary** Explorer view ranks functions by energy and lists their related optimization suggestions.
- **Line heatmap:** Colors source lines attributed to the highest-energy defined function. Run profiling again after changing the target code to refresh the heatmap.
- **Persistent reports:** Saves the latest profile and suggestions per file in VS Code workspace state, restoring annotations and diagnostics when the workspace is reopened.
- **JSON export:** The **EcoCode: Export Profiling Report** command saves the profiler report and suggestions together.
- **General code-model suggestions:** Configure an OpenAI-compatible local or self-hosted code model to review any Python source for algorithmic, I/O, memory, data-structure, and idiomatic improvements. The built-in offline fallback uses transparent structural rules across the supported pattern catalogue; neither mode edits source automatically.
- **Configurable carbon intensity:** Converts estimated energy to carbon equivalent using a configurable grid intensity (default: 310 gCO₂e/kWh).
- **Offline calibration:** The TDP fallback uses `CALIBRATION_COEFFICIENT = 0.78795087`, fitted from 42 measured Energy-Languages benchmark rows with ordinary least squares through the origin.

## How the estimate is selected

1. **RAPL package measurement** when readable Linux powercap counters are available.
2. **Intel energy baseline** when a benchmark match is available.
3. **TDP software estimate** when neither measured counters nor a baseline match is available.

The profiler apportions whole-run energy across functions according to their measured execution-time share. The per-function numbers are estimates; RAPL measures package energy for the whole run, not each function independently.

## Requirements

- Visual Studio Code 1.80 or newer
- Node.js and npm
- Python 3
- Python package `psutil` (`backend/requirements.txt`)
- Linux Intel system with readable `/sys/class/powercap` counters for direct RAPL measurement; other systems use the available model or software fallback

## Run the extension in development

1. Open the repository folder in VS Code.
2. Run `npm install` in the repository.
3. Install the Python dependency with `pip install -r backend/requirements.txt`.
4. Press `F5` to open the Extension Development Host.
5. In that window, open a Python file and run **EcoCode: Profile Current Python File** from the Command Palette.
6. Open **EcoCode Summary** in the Explorer and expand a function to inspect its suggestions. The heatmap appears in the editor for the highest-energy defined function when line timings are available.
7. Run **EcoCode: Export Profiling Report** to save a JSON report.

Open the project as a folder (rather than opening only an individual file) so VS Code can load the extension project, dependencies, and workspace persistence correctly.

## Train or rebuild the Intel energy baseline

The checked-in model was generated from `backend/training_data/intel_energy_measurements.csv`. To regenerate it after updating the CSV, run from the repository root:

```sh
node backend/train_intel_energy_model.js
```

The script validates required columns and measurements, then writes `backend/training_data/intel_energy_model.json`.

To retrain the code-suggestion classifier after adding labelled `{label, code}` rows to `backend/training_data/code_suggestion_examples.jsonl`, run:

```bash
node backend/train_suggestion_model.js
```

## Run model evaluations

Run the reproducible holdout evaluation, suggestion-corpus precision check, and paired RAPL/TDP validation:

```sh
python backend/evaluate.py --calibration-csv backend/training_data/python3_benchmarks_energy_measured.csv --output backend/evaluation_results.json
```

The report is organised as four stages: Stage 1 measures estimation accuracy on genuinely held-out data and is the direct answer to RQ1; Stage 2 validates paired on-device readings against a ≤15% MAPE target; Stage 3 reports suggestion precision against a ≥80% target; and Stage 4 records an indicative usability signal.

Stage 1 now uses a hierarchical grouped-median estimator: sparse groups are shrunk toward their CPU/benchmark and benchmark parents instead of letting a single observation determine a prediction. The evaluator keeps all held-out rows out of every lookup. It also records that the Intel training CSV pools Intel-PCM, RAPL-perf, and PowerTOP readings, so its pooled MAPE must not be interpreted as instrument-equivalent accuracy.

The current seeded Stage 1 result is **40.6724% MAPE** (MAE 299.698786 J; RMSE 489.282599 J; 200/200 matched rows), which does not meet the ≤15% target. It is an honest RQ1 result, not a target claim.

Stage 2 reports both the raw TDP baseline and a benchmark-aware, leave-one-run-out calibration. The raw 30-run paired dataset has 45.0147% mean MAPE and fails the target (12/30 runs within it). With a coefficient fitted from only the other two runs of the same benchmark, the held-out mean MAPE is 2.8378% (30/30 within target). This calibrated mode requires at least two same-instrument warm-up runs for each benchmark/CPU/OS configuration; it must not be used for unseen benchmarks without collecting those runs first.

The current 90-case suggestion-corpus evaluation reports 78 true positives, zero false positives, and zero false negatives (100% precision, recall, F1, and case accuracy). It validates coverage of this curated pattern catalogue only; it is not a generalisation claim for arbitrary Python code.

For broad, whole-file suggestions, configure `ecocode.suggestionModel.endpoint` to an OpenAI-compatible endpoint (for example Ollama's `http://localhost:11434/v1/chat/completions`) and set `ecocode.suggestionModel.name` to an installed coding model such as `qwen2.5-coder:7b`.
The default model timeout is 180 seconds; set `ecocode.suggestionModel.timeoutSeconds` (15–600) higher on CPU-only machines.

## Run the profiler directly

The backend accepts explicit benchmark metadata for matching the energy baseline:

```sh
python backend/profiler.py path/to/script.py \
  --benchmark neural_net_inference \
  --input-size resnet50 \
  --cpu-model "Intel Core i7-12700KF"
```

`--benchmark`, `--input-size`, `--cpu-model`, and `--energy-model` are optional. If omitted, the benchmark defaults to the script filename, the CPU model is detected when possible, and the default model JSON is loaded from `backend/training_data`.

## VS Code settings

- **EcoCode Profiler: Carbon Intensity Factor** — grid intensity in gCO₂e/kWh (default 310).
- **EcoCode Profiler: Sampling Interval** — configured interval in milliseconds.
- **EcoCode Profiler: Energy Units** — display preference (`J`, `mJ`, or `kWh`).

## Current limitations

- RAPL support is Linux-only and depends on accessible Intel powercap counters.
- Stage 1’s revised 40.6724% MAPE still fails the ≤15% target. The next valid improvement is matched, single-instrument RAPL collection—not changing the pass/fail label or pooling incompatible measurements.
- The offline suggestion score is measured on a curated pattern catalogue. An independent unseen corpus is required before making a broader accuracy claim.
- The extension currently passes default benchmark metadata to the profiler; explicit benchmark, input-size, and CPU-model options are available when running the backend directly.
- The TDP estimator and per-function allocation are estimates, not direct per-function hardware measurements.
