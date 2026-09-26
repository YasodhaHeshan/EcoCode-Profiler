import sys
import time
import json
import argparse
import platform
import psutil
import os
import re

# --- Configuration & Hardware Constants ---
# Using a static calibration coefficient from the Pereira et al. dataset.
CALIBRATION_COEFFICIENT = 1.0 

# Default TDP in Watts (This can be mapped based on specific CPU model later)
DEFAULT_TDP = 65.0 
DEFAULT_ENERGY_MODEL = os.path.join(os.path.dirname(__file__), "training_data", "intel_energy_model.json")


def detect_cpu_model():
    """Return a best-effort CPU model string without requiring external tools."""
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as cpuinfo:
                for line in cpuinfo:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.uname().processor or ""


def normalize_cpu_model(model):
    """Normalize common naming differences while retaining exact model numbers."""
    return re.sub(r"[^a-z0-9]", "", model.lower())

class EcoProfiler:
    def __init__(self, target_file, carbon_intensity=310.0, benchmark=None,
                 input_size=None, cpu_model=None, energy_model_path=DEFAULT_ENERGY_MODEL):
        self.target_file = target_file
        self.carbon_intensity = carbon_intensity # gCO2e/kWh
        self.benchmark = benchmark or os.path.splitext(os.path.basename(target_file))[0]
        self.input_size = input_size
        self.cpu_model = cpu_model or detect_cpu_model()
        self.energy_model_path = energy_model_path
        self.energy_model = self.load_energy_model(energy_model_path)
        self.energy_estimation = {"method": "tdp_fallback", "model_group": None, "model_samples": None}
        self.total_estimated_energy_j = 0.0
        
        self.function_stats = {}
        self.call_stack = []
        self.line_trace_state = {}
        
        # Decide measurement backend based on platform
        self.is_linux = platform.system() == "Linux"
        self.hardware_measurement_available = self.check_rapl_available()
        
        self.start_time = time.time()

    @staticmethod
    def load_energy_model(model_path):
        try:
            with open(model_path, "r", encoding="utf-8") as model_file:
                model = json.load(model_file)
            if model.get("model_type") != "measured_intel_energy_median_baseline":
                raise ValueError("Unexpected energy model type")
            return model
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"Energy model unavailable ({error}); using TDP estimate.", file=sys.stderr)
            return None

    def find_energy_baseline(self):
        """Find the most specific matching measured group in the trained model."""
        if not self.energy_model:
            return None
        model = self.energy_model
        cpu_norm = normalize_cpu_model(self.cpu_model)
        matched_cpu = None
        if cpu_norm:
            for key in model.get("by_cpu_model_and_benchmark", {}):
                cpu_key, benchmark_key = key.split("|", 1)
                if benchmark_key == self.benchmark and normalize_cpu_model(cpu_key) in cpu_norm:
                    matched_cpu = cpu_key
                    break
        if matched_cpu and self.input_size:
            key = f"{matched_cpu}|{self.benchmark}|{self.input_size}"
            baseline = model.get("by_cpu_model_benchmark_and_input_size", {}).get(key)
            if baseline:
                return baseline, "cpu_benchmark_input", key
        if matched_cpu:
            key = f"{matched_cpu}|{self.benchmark}"
            baseline = model.get("by_cpu_model_and_benchmark", {}).get(key)
            if baseline:
                return baseline, "cpu_benchmark", key
        baseline = model.get("by_benchmark", {}).get(self.benchmark)
        if baseline:
            return baseline, "benchmark", self.benchmark
        return None

    def check_rapl_available(self):
        """FR-01: Detect if hardware RAPL is available (Linux powercap)"""
        if self.is_linux:
            return os.path.exists("/sys/class/powercap/intel-rapl")
        return False

    def profile_hook(self, frame, event, arg):
        """FR-04: Instrument Python file using sys.setprofile."""
        if event not in ("call", "return"):
            return
            
        func_name = frame.f_code.co_name
        filename = frame.f_code.co_filename
        
        # Only profile functions in the target script, ignore stdlib and imports
        if filename != self.target_file:
            return
            
        current_time = time.time()
        
        if event == "call":
            self.call_stack.append((func_name, current_time))
            if func_name not in self.function_stats:
                self.function_stats[func_name] = {
                    "calls": 0,
                    "total_time_s": 0.0,
                    "total_energy_j": 0.0,
                    "carbon_gco2e": 0.0,
                    "line_time_s": {}
                }
        
        elif event == "return":
            if not self.call_stack:
                return
            
            top_func, start_time = self.call_stack.pop()
            if top_func == func_name:
                elapsed_time = current_time - start_time
                self.function_stats[func_name]["calls"] += 1
                self.function_stats[func_name]["total_time_s"] += elapsed_time
                
        return self.profile_hook

    def trace_hook(self, frame, event, arg):
        """Accumulate elapsed time by source line for the target script."""
        if frame.f_code.co_filename != self.target_file:
            return None
        frame_id = id(frame)
        now = time.perf_counter()
        previous = self.line_trace_state.get(frame_id)
        if previous is not None and event in ("line", "return", "exception"):
            function_name, line_number, started = previous
            elapsed = max(0.0, now - started)
            stats = self.function_stats.get(function_name)
            if stats is not None:
                key = str(line_number)
                stats["line_time_s"][key] = stats["line_time_s"].get(key, 0.0) + elapsed
        if event == "line":
            self.line_trace_state[frame_id] = (frame.f_code.co_name, frame.f_lineno, now)
        else:
            self.line_trace_state.pop(frame_id, None)
        return self.trace_hook

    def calculate_final_energy(self):
        """FR-05 & FR-07: Calculate final energy and carbon totals."""
        total_time = time.time() - self.start_time
        
        # We use a system-wide CPU utilization over the entire run for the fallback
        # In a production version, we would poll this asynchronously.
        cpu_percent = psutil.cpu_percent() / 100.0
        
        # Ensure we have a minimum utilization so tiny scripts don't read 0
        if cpu_percent == 0.0:
            cpu_percent = 0.1 

        # Scale the matched measured median power to this run's observed duration.
        # If no benchmark match exists, retain the original utilization/TDP estimate.
        match = self.find_energy_baseline()
        if match:
            baseline, match_level, match_key = match
            total_estimated_energy = baseline["median_average_power_w"] * total_time
            self.energy_estimation = {
                "method": "intel_energy_model",
                "model_group": match_key,
                "match_level": match_level,
                "model_samples": baseline["samples"],
                "model_median_energy_j": baseline["median_energy_joules"],
                "model_median_runtime_s": baseline["median_runtime_seconds"],
            }
        else:
            total_estimated_energy = (cpu_percent * DEFAULT_TDP * total_time) * CALIBRATION_COEFFICIENT
            self.energy_estimation["fallback_reason"] = (
                "No matching benchmark in model" if self.energy_model
                else "Energy model could not be loaded"
            )
        self.total_estimated_energy_j = total_estimated_energy
        
        # Distribute energy to functions based on their time share
        for func, stats in self.function_stats.items():
            if total_time > 0:
                time_share = stats["total_time_s"] / total_time
            else:
                time_share = 0
                
            func_energy_j = total_estimated_energy * time_share
            stats["total_energy_j"] = func_energy_j
            
            # Convert Joules to kWh, then to gCO2e
            energy_kwh = func_energy_j / 3600000.0
            stats["carbon_gco2e"] = energy_kwh * self.carbon_intensity
            
    def generate_report(self):
        self.calculate_final_energy()
        
        report = {
            "metadata": {
                "hardware_measured": self.hardware_measurement_available,
                "carbon_intensity_factor": self.carbon_intensity,
                "total_run_time_s": time.time() - self.start_time,
                "total_estimated_energy_j": self.total_estimated_energy_j,
                "energy_estimation": self.energy_estimation,
                "benchmark": self.benchmark,
                "input_size": self.input_size,
                "cpu_model": self.cpu_model,
            },
            "functions": self.function_stats
        }
        return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script", help="Target Python script to profile")
    parser.add_argument("--carbon", type=float, default=310.0, help="Carbon intensity factor")
    parser.add_argument("--benchmark", help="Benchmark key from intel_energy_measurements.csv (defaults to script filename)")
    parser.add_argument("--input-size", help="Input size key from intel_energy_measurements.csv")
    parser.add_argument("--cpu-model", help="CPU model label; defaults to the detected host CPU")
    parser.add_argument("--energy-model", default=DEFAULT_ENERGY_MODEL, help="Path to trained Intel energy model JSON")
    args = parser.parse_args()
    
    target_script = os.path.abspath(args.script)
    
    # Initialize psutil cpu percent measurement
    psutil.cpu_percent()
    
    profiler = EcoProfiler(target_script, args.carbon, args.benchmark,
                           args.input_size, args.cpu_model, args.energy_model)
    
    sys.setprofile(profiler.profile_hook)
    sys.settrace(profiler.trace_hook)

    # Redirect the target script's stdout to stderr so it doesn't
    # corrupt the JSON output that the VS Code extension reads from stdout.
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        # Execute the target script
        with open(target_script, 'r') as f:
            code = compile(f.read(), target_script, 'exec')
            exec(code, {"__name__": "__main__"})
    except Exception as e:
        print(f"Error executing script: {e}", file=sys.stderr)
    finally:
        sys.setprofile(None)
        sys.settrace(None)
        # Restore stdout and write the JSON report
        sys.stdout = original_stdout
        report = profiler.generate_report()
        # Print JSON report to stdout so the VS Code extension can read it
        print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
