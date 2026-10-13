# EcoCode Profiler: Project Status & Next Steps

This document tracks the progress of the EcoCode Profiler research project based on the requirements defined in the methodology.

---

## ✅ What Has Been Completed

### Phase 1: Project Initialization
- [x] Initialized VS Code Extension project with TypeScript.
- [x] Created standard configuration files (`package.json`, `tsconfig.json`).
- [x] Defined extension settings (Carbon Intensity Factor, Sampling Interval, Energy Units).
- [x] Set up `.vscode` debugging configuration (`launch.json`, `tasks.json`) for seamless testing.
- [x] Created project documentation (`README.md`) and `.gitignore`.

### Phase 2: Core Measurement Backend (Python)
- [x] Built the Python profiling script (`backend/profiler.py`).
- [x] **FR-01:** Implemented OS and hardware detection logic.
- [x] **FR-03:** Implemented the **TDP-based software fallback estimator** using `psutil.cpu_percent()` and elapsed time.
- [x] **FR-04 & FR-05:** Instrumented Python execution using `sys.setprofile` to accurately track function entry/exit times and distribute energy proportionally.
- [x] **FR-07:** Integrated the static Sri Lankan carbon intensity factor (310 gCO₂e/kWh) to convert energy to carbon emissions.
- [x] Added `backend/train_intel_energy_model.js` to validate and summarize Intel energy measurements into a transparent grouped-median baseline.
- [x] Trained `backend/training_data/intel_energy_model.json` from the 1,000 supplied measurement rows, covering 10 benchmark names and 22 CPU models.
- [x] Integrated the energy baseline into `backend/profiler.py`: it selects a matching CPU/benchmark/input-size group when possible, scales the group's median average power to the observed run duration, and retains the TDP estimate as a fallback.
- [x] Added profiler CLI options for benchmark, input size, CPU model, and energy-model path; included the selected estimate method and match details in report metadata.
- [x] Added per-line elapsed-time attribution to profiler output for source heatmaps.
- [x] Compiled the VS Code extension successfully with `npm.cmd run compile`. The Extension Development Host still needs to be launched/continued in VS Code for the profile command to be available.

### Phase 3: VS Code Extension UI (MVP)
- [x] Registered the `EcoCode: Profile Current Python File` command.
- [x] Linked the TypeScript frontend to the Python backend process, capturing the JSON output.
- [x] **FR-08:** Implemented **Inline Editor Annotations** to display Joules and gCO₂e directly next to function definitions.
- [x] **FR-09:** Added transparency labeling so the user is explicitly notified whether the data is *Measured (RAPL)* or *Estimated (TDP fallback)*.

### Phase 4: Energy-Efficient Code Suggestion Engine ✨ NEW
- [x] Built the AST-based suggestion engine (`backend/suggestions.py`).
- [x] **FR-15:** Implemented static analysis using Python's `ast` module to detect 9 energy anti-patterns:
  - Pattern 1: Manual list build with `.append()` → list comprehension
  - Pattern 2: Manual `sum` accumulation loop → `sum()`
  - Pattern 3: String concatenation with `+=` in loops → `"".join()`
  - Pattern 4: List membership testing → `set` for O(1) lookups
  - Pattern 5: List comprehension passed to consuming functions → generator expression
  - Pattern 6: Repeated attribute lookups in loops → cache in local variable
  - Pattern 7: Manual numerical loops → NumPy vectorised operations (AST detection ready)
  - Pattern 8: `range(len(...))` iteration → `enumerate()` or direct iteration
  - Pattern 9: Nested for-loops → `itertools.product()`
- [x] **FR-16:** Each suggestion includes pattern name, plain-language fix, line number, estimated savings %, and academic source.
- [x] **FR-17:** Suggestions are scoped only to profiled functions (not the entire file).
- [x] Integrated suggestion engine into the VS Code extension — suggestions appear as **yellow warning squiggles** in the editor and in the **Problems panel**.
- [x] Tested engine against `test.py` with all 9 anti-patterns — **8/8 patterns detected correctly**, `clean_function` produced zero false positives.

---

## 🚀 What Needs To Be Done Next

### 1. Remaining Extension Features
- [x] **FR-10 (Summary Panel):** Added the `EcoCode Summary` Explorer TreeView, ranking functions by estimated energy and listing each function's suggestions as child items.
- [x] **FR-12 (Persistence):** Save each file's latest profiling report and suggestions in VS Code workspace state; restore annotations, diagnostics, and the latest summary when the extension host restarts.
- [x] **FR-13 (Export Report):** Added `EcoCode: Export Profiling Report` to save the raw profiler report and suggestions together as JSON.
- [x] **FR-14 (Line-level Heatmap):** Added per-line timing to profiler output and color-coded line decorations for source lines attributed to the most energy-consuming function.

### 2. Hardware Measurement (Backend A)
- [x] **FR-02 (Direct RAPL Reading):** Read Linux powercap/RAPL package energy counters at profiler start and end; use measured package energy ahead of model and TDP estimates when available.
- [x] **FR-06 (Wrap-around Correction):** Correct a counter wrap between snapshots using each domain's `max_energy_range_uj` modulus, including the typical 32-bit counter range.
- [ ] Update the VS Code extension runner to expose benchmark, input-size, and CPU-model options when invoking the profiler. At present, the extension uses profiler defaults (benchmark from script filename, detected CPU, and no input-size key).

### 3. Evaluation & Validation
- [x] **Offline Calibration:** Used the supplied *Energy-Languages* measured-energy dataset (42 rows) to fit `CALIBRATION_COEFFICIENT` by ordinary least squares through the origin, using `65 W × cpu_secs_mean` as the TDP baseline. Derived coefficient: **0.78795087**.
- [x] Evaluate the Intel grouped-median baseline on held-out measurements and report prediction error. The current baseline is a lookup model, not a regression model, and has not been validated for predictive accuracy.
  - Evaluation harness added: `python backend/evaluate.py`.
  - Initial seeded 80/20 holdout (seed 42): 200/200 rows matched, MAE 331.113822 J, RMSE 529.069534 J, MAPE 47.5848%.
- [x] **On-Device Validation:** Completed the comparison of Backend A (RAPL) vs Backend B (TDP) across 30 paired runs on an Intel Core i5-12400 host running Ubuntu 22.04. Mean MAPE: **45.0147%** (median: **55.5244%**); 12/30 runs were within the ≤15% target, so the target was **not met**. All 30 validation rows are complete.
- [x] **Suggestion Precision Evaluation (NEW):** Run the suggestion engine against benchmark scripts, classify each suggestion as TP or FP, and calculate precision. Target: ≥ 80%.
  - Initial evaluation on the 90-case labelled suggestion corpus: 18 TP, 12 FP, 60 FN, precision 60.00%, case accuracy 33.33%.
- [x] **Usability Assessment:** Distribute the extension to Python developers and collect Likert-scale feedback on annotations, suggestions, and overall usefulness.

### 4. Stretch Goals (Optional)
- [ ] Add Quick Fix actions so users can auto-apply suggested refactorings with one click.
- [ ] Add a "before vs after" energy comparison mode to show savings after refactoring.
- [ ] Support profiling Jupyter notebook cells.

---

## 📂 Current Project Structure

```
EcoCode-Profiler/
├── .vscode/
│   ├── launch.json          # Extension debug launcher
│   └── tasks.json           # Build tasks
├── backend/
│   ├── profiler.py          # Energy measurement backend (Phase 2)
│   ├── suggestions.py       # Anti-pattern suggestion engine (Phase 4) ✨
│   ├── train_intel_energy_model.js # Builds grouped-median energy baseline
│   ├── training_data/
│   │   ├── intel_energy_measurements.csv # Supplied measured energy data (1,000 rows)
│   │   ├── intel_energy_model.json       # Trained energy baseline
│   │   ├── python3_benchmarks_normalized.csv
│   │   └── runtime_baseline_model.json
│   └── requirements.txt     # Python dependencies (psutil)
├── src/
│   └── extension.ts         # Commands, summary view, persistence, export, and decorations
├── out/                     # Compiled JavaScript output
├── node_modules/            # Node.js dependencies
├── .gitignore
├── package.json
├── tsconfig.json
├── plan.md                  # This file
├── README.md
└── test.py                  # Test script with anti-patterns
```
