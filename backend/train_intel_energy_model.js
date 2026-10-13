// Train a transparent energy baseline from measured Intel CPU runs.
// Predictions are grouped medians so the model remains inspectable and does
// not imply generalization beyond the supplied benchmark and CPU labels.
const fs = require('fs');
const path = require('path');

const inputPath = process.argv[2] || path.join(__dirname, 'training_data', 'intel_energy_measurements.csv');
const outputPath = path.join(__dirname, 'training_data', 'intel_energy_model.json');

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = lines.shift().split(',');
  return lines.map((line, i) => {
    const values = line.split(',');
    if (values.length !== headers.length) throw new Error(`Malformed CSV row ${i + 2}`);
    return Object.fromEntries(headers.map((header, j) => [header, values[j]]));
  });
}
function median(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
function summarize(rows) {
  return {
    samples: rows.length,
    median_energy_joules: median(rows.map(r => r.energy)),
    median_runtime_seconds: median(rows.map(r => r.runtime)),
    median_average_power_w: median(rows.map(r => r.energy / r.runtime)),
  };
}

const source = parseCsv(fs.readFileSync(inputPath, 'utf8').replace(/^\uFEFF/, ''));
const required = ['cpu_model', 'cpu_generation', 'benchmark_name', 'input_size', 'energy_joules', 'runtime_seconds'];
if (!source.length || required.some(key => !Object.hasOwn(source[0], key))) {
  throw new Error(`Input must contain rows and columns: ${required.join(', ')}`);
}
const rows = source.map((r, i) => {
  const energy = Number(r.energy_joules), runtime = Number(r.runtime_seconds);
  if (!r.cpu_model || !r.benchmark_name || !r.input_size || !Number.isFinite(energy) || energy <= 0 || !Number.isFinite(runtime) || runtime <= 0) {
    throw new Error(`Invalid energy measurement on row ${i + 2}`);
  }
  return { ...r, energy, runtime };
});

function groupBy(keyFn) {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return Object.fromEntries([...groups].map(([key, values]) => [key, summarize(values)]));
}

const model = {
  model_type: 'measured_intel_energy_median_baseline',
  target: 'energy_joules',
  training_rows: rows.length,
  measurement_domain: 'package energy',
  cpu_generations: [...new Set(rows.map(r => r.cpu_generation))].sort(),
  energy_sources: [...new Set(rows.map(r => r.energy_source))].sort(),
  sparse_group_shrinkage_samples: 3,
  global: summarize(rows),
  by_benchmark: groupBy(r => r.benchmark_name),
  by_cpu_model_and_benchmark: groupBy(r => `${r.cpu_model}|${r.benchmark_name}`),
  by_cpu_model_benchmark_and_input_size: groupBy(r => `${r.cpu_model}|${r.benchmark_name}|${r.input_size}`),
  limitations: 'Hierarchical grouped-median lookup baseline trained on supplied measurements. Sparse groups are shrunk toward parent groups using three pseudo-samples. It is not a regression model; predictions are supported only for matching benchmark and CPU keys. Dataset has mixed measurement sources and operating systems, which are retained as provenance rather than treated as interchangeable.',
};
fs.writeFileSync(outputPath, JSON.stringify(model, null, 2) + '\n');
console.log(JSON.stringify({ rows: rows.length, benchmarks: Object.keys(model.by_benchmark).length, cpu_models: new Set(rows.map(r => r.cpu_model)).size, model: outputPath }, null, 2));
