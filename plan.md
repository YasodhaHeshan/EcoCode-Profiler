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
- [ ] **FR-10 (Summary Panel):** Build a sidebar panel (using VS Code's Webview or TreeView API) that ranks functions from most to least energy-consuming and shows their associated suggestions.
- [ ] **FR-12 (Persistence):** Save profiling results in the extension's workspace state so inline annotations and suggestions survive editor restarts.
- [ ] **FR-13 (Export Report):** Add a command to export the raw JSON profiling + suggestions report to a file.
- [ ] **FR-14 (Line-level Heatmap):** Highlight specific lines inside the most expensive function with color-coded background.

### 2. Hardware Measurement (Backend A)
- [ ] **FR-02 (Direct RAPL Reading):** Expand `profiler.py` to read `/sys/class/powercap/intel-rapl` energy counters on Linux.
- [ ] **FR-06 (Wrap-around Correction):** Add logic to handle 32-bit RAPL counter overflow.

### 3. Evaluation & Validation
- [ ] **Offline Calibration:** Use the *Energy-Languages* dataset (Pereira et al.) to derive the optimal `CALIBRATION_COEFFICIENT` via linear regression.
- [ ] **On-Device Validation:** On a supported Intel 12th/13th Gen Linux machine, compare Backend A (RAPL) vs Backend B (TDP) and calculate MAPE. Target: ≤ 15%.
- [ ] **Suggestion Precision Evaluation (NEW):** Run the suggestion engine against benchmark scripts, classify each suggestion as TP or FP, and calculate precision. Target: ≥ 80%.
- [ ] **Usability Assessment:** Distribute the extension to Python developers and collect Likert-scale feedback on annotations, suggestions, and overall usefulness.

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
│   └── requirements.txt     # Python dependencies (psutil)
├── src/
│   └── extension.ts         # VS Code extension entry point (Phase 3)
├── out/                     # Compiled JavaScript output
├── node_modules/            # Node.js dependencies
├── .gitignore
├── package.json
├── tsconfig.json
├── plan.md                  # This file
├── README.md
└── test.py                  # Test script with anti-patterns
```
