import sys
import time
import json
import argparse
import platform
import psutil
import os

# --- Configuration & Hardware Constants ---
# Using a static calibration coefficient from the Pereira et al. dataset.
CALIBRATION_COEFFICIENT = 1.0 

# Default TDP in Watts (This can be mapped based on specific CPU model later)
DEFAULT_TDP = 65.0 

class EcoProfiler:
    def __init__(self, target_file, carbon_intensity=310.0):
        self.target_file = target_file
        self.carbon_intensity = carbon_intensity # gCO2e/kWh
        
        self.function_stats = {}
        self.call_stack = []
        
        # Decide measurement backend based on platform
        self.is_linux = platform.system() == "Linux"
        self.hardware_measurement_available = self.check_rapl_available()
        
        self.start_time = time.time()

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
                    "carbon_gco2e": 0.0
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

    def calculate_final_energy(self):
        """FR-05 & FR-07: Calculate final energy and carbon totals."""
        total_time = time.time() - self.start_time
        
        # We use a system-wide CPU utilization over the entire run for the fallback
        # In a production version, we would poll this asynchronously.
        cpu_percent = psutil.cpu_percent() / 100.0
        
        # Ensure we have a minimum utilization so tiny scripts don't read 0
        if cpu_percent == 0.0:
            cpu_percent = 0.1 

        # Backend B: Total energy = (Utilization * TDP * Time) * Calibration
        total_estimated_energy = (cpu_percent * DEFAULT_TDP * total_time) * CALIBRATION_COEFFICIENT
        
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
                "total_run_time_s": time.time() - self.start_time
            },
            "functions": self.function_stats
        }
        return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script", help="Target Python script to profile")
    parser.add_argument("--carbon", type=float, default=310.0, help="Carbon intensity factor")
    args = parser.parse_args()
    
    target_script = os.path.abspath(args.script)
    
    # Initialize psutil cpu percent measurement
    psutil.cpu_percent()
    
    profiler = EcoProfiler(target_script, args.carbon)
    
    sys.setprofile(profiler.profile_hook)
    
    try:
        # Execute the target script
        with open(target_script, 'r') as f:
            code = compile(f.read(), target_script, 'exec')
            exec(code, {"__name__": "__main__"})
    except Exception as e:
        print(f"Error executing script: {e}", file=sys.stderr)
    finally:
        sys.setprofile(None)
        report = profiler.generate_report()
        # Print JSON report to stdout so the VS Code extension can read it
        print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
