# EcoCode Profiler: Project Status & Next Steps

This document tracks the progress of the EcoCode Profiler research project based on the requirements defined in the methodology.

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

---

## 🚀 What Needs To Be Done Next

### 1. Extension Feature Completion
- [ ] **FR-10 (Summary Panel):** Build a sidebar panel (using VS Code's Webview or TreeView API) that ranks the functions in the file from most to least energy-consuming.
- [ ] **FR-12 (Persistence):** Save the profiling results in the extension's workspace state so that the inline annotations survive if the user closes and reopens the editor.
- [ ] **FR-13 (Export Report):** Add a button or command to save the raw JSON profiling output to a file for academic evaluation.

### 2. Hardware Measurement (Backend A)
- [ ] **FR-02 (Direct RAPL Reading):** Expand `profiler.py` to actually read the `/sys/class/powercap/intel-rapl` files on Linux. 
- [ ] **FR-06 (Wrap-around Correction):** Add mathematical logic to the RAPL reader to handle instances where the hardware energy register overflows and resets to zero during a run.

### 3. Evaluation & Validation (Chapter 3 / Evaluation Strategy)
- [ ] **Offline Calibration:** Use the *Energy-Languages* dataset (Pereira et al.) to run a regression analysis and finalize the `CALIBRATION_COEFFICIENT` currently hardcoded to `1.0` in the backend.
- [ ] **On-Device Validation:** Boot up a supported Intel 12th or 13th Gen machine into Linux (e.g., via Live USB). Run the extension and compare the hardware RAPL readings to an independent tool (like Intel Power Gadget or raw powercap readings) to calculate the margin of error.
- [ ] **Usability Check:** Have a subset of your survey respondents install the extension and rate its usefulness.

### 4. Stretch Goals (Optional)
- [ ] **FR-14 (Line-level Heatmap):** Highlight specific lines inside the most expensive function.
