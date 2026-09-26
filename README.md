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
- **Static energy suggestions:** An AST-based analyzer detects common Python patterns such as manual list building, repeated string concatenation, inefficient membership checks, and nested loops. Suggestions appear in the editor and Problems panel.
- **Configurable carbon intensity:** Converts estimated energy to carbon equivalent using a configurable grid intensity (default: 310 gCO₂e/kWh).

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
- The grouped-median Intel model is trained on the supplied measurements and has not yet been validated against held-out data.
- The extension currently passes default benchmark metadata to the profiler; explicit benchmark, input-size, and CPU-model options are available when running the backend directly.
- The TDP estimator and per-function allocation are estimates, not direct per-function hardware measurements.
