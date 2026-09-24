# EcoCode Profiler

EcoCode Profiler is a hybrid real-time energy consumption and carbon estimation extension for Python development in Visual Studio Code. This project addresses the gap in developer-facing, in-editor energy tooling by providing real-time, function-level feedback.

## Features

- **Hybrid Measurement Engine:** Uses direct RAPL hardware counters on supported hardware (Intel 12th/13th Gen on Linux via `powercap`) and gracefully falls back to a TDP-based software estimator on other platforms (like Windows or AMD processors).
- **Inline Editor Feedback:** Displays precise energy (Joules) and carbon (gCO₂e) footprint estimates as inline annotations right next to your function definitions.
- **Configurable Carbon Intensity:** Uses a configurable regional grid carbon-intensity factor to convert energy consumed into an approximate carbon equivalent (Defaults to 310 gCO₂e/kWh).
- **Transparent Labelling:** Explicitly labels data as *Measured (RAPL)* or *Estimated (TDP fallback)* in the UI.

## Architecture

The project consists of two components:
1. **VS Code Extension (TypeScript):** Manages the UI, settings, and editor decorations.
2. **Measurement Backend (Python):** Instruments the Python code using `sys.setprofile`, tracks CPU utilization, handles RAPL register reads (if available), and outputs a structured JSON report.

## Requirements

- VS Code 1.80+
- Python 3.x
- Node.js & npm (for building the extension)
- `psutil` (Python package for the TDP fallback estimator)

## Setup & Installation for Development

1. Clone this repository.
2. Open the folder in VS Code.
3. Run `npm install` to install extension dependencies.
4. Run `pip install -r backend/requirements.txt` to install the backend Python dependencies.
5. Press `F5` to launch the Extension Development Host.
6. Open a Python file and run the command: `EcoCode: Profile Current Python File`.

## Configuration

You can configure the following settings in VS Code (Settings > Extensions > EcoCode Profiler):
- **Carbon Intensity Factor:** Adjust the static carbon-intensity factor based on your region's grid.
- **Sampling Interval:** Update the polling frequency (ms) for the profiler.
- **Energy Units:** Toggle between Joules, mJ, and kWh.
