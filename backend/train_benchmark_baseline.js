// Normalize the supplied benchmark CSV and fit a small runtime baseline.
// This is deliberately a runtime model: the source file contains no energy labels.
const fs = require('fs');
const path = require('path');

const inputPath = process.argv[2];
if (!inputPath) {
  console.error('Usage: node backend/train_benchmark_baseline.js <python3_benchmarks.csv>');
  process.exit(2);
}

function parseCsvLine(line) {
  // Current source file has no quoted commas; reject them rather than silently
  // misreading a future CSV with a more complex dialect.
  if (line.includes('"')) throw new Error('Quoted CSV fields are not supported in this input.');
  return line.split(',');
}
function numeric(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function meanRange(value) {
  const m = value.trim().match(/^([\d.]+)(?:\s*-\s*([\d.]+))?$/);
  return m ? (Number(m[1]) + Number(m[2] || m[1])) / 2 : null;
}
function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

const lines = fs.readFileSync(inputPath, 'utf8').trim().split(/\r?\n/);
const headers = parseCsvLine(lines.shift());
const rows = lines.map((line, i) => {
  const values = parseCsvLine(line);
  if (values.length !== headers.length) throw new Error(`Malformed CSV row ${i + 2}`);
  const row = Object.fromEntries(headers.map((key, j) => [key, values[j]]));
  const benchmark = row.source.replace(/\s*#\d+\b/g, '').replace(/\s*\(.*\)$/, '').trim();
  const cpu = meanRange(row.cpu_secs_mean_or_95CI);
  const elapsed = numeric(row.elapsed_secs);
  if (cpu === null || elapsed === null) throw new Error(`Invalid timing on row ${i + 2}`);
  return {
    benchmark,
    source_variant: row.source,
    cpu_secs_mean: cpu,
    elapsed_secs: elapsed,
    mem_kb: numeric(row.mem_KB),
    gz_bytes: numeric(row.gz_bytes),
    make_secs: numeric(row.make_secs),
    input_n: numeric(row.N),
    cpu_model: '',
    energy_j: '',
    label_status: 'energy_unmeasured',
  };
});

const outputDir = path.join(__dirname, 'training_data');
fs.mkdirSync(outputDir, { recursive: true });
const normalized = path.join(outputDir, 'python3_benchmarks_normalized.csv');
const fields = Object.keys(rows[0]);
const csv = [fields.join(','), ...rows.map(row => fields.map(k => row[k]).join(','))].join('\n') + '\n';
fs.writeFileSync(normalized, csv);

// Fit a transparent benchmark-specific median runtime baseline. It can be used
// to sanity-check the data pipeline, but cannot estimate energy or transfer to
// other CPUs because the CSV does not identify its machine.
const groups = {};
for (const row of rows) (groups[row.benchmark] ||= []).push(row.elapsed_secs);
const model = {
  model_type: 'benchmark_median_runtime_baseline',
  target: 'elapsed_secs',
  training_rows: rows.length,
  cpu_model_known: false,
  energy_labels_present: false,
  benchmarks: Object.fromEntries(Object.entries(groups).map(([name, times]) => [name, {
    samples: times.length,
    median_elapsed_secs: median(times),
  }])),
  limitations: 'Runtime-only baseline from an unidentified CPU; not an energy model and not validated for Intel 12th/13th gen CPUs.',
};
const modelPath = path.join(__dirname, 'training_data', 'runtime_baseline_model.json');
fs.writeFileSync(modelPath, JSON.stringify(model, null, 2) + '\n');
console.log(JSON.stringify({ rows: rows.length, benchmark_groups: Object.keys(groups).length, normalized, model: modelPath, target: model.target, energy_trained: false }, null, 2));
