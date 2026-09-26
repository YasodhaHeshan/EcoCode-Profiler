// Train the code-suggestion classifier without external ML dependencies.
// Usage: node backend/train_suggestion_model.js
const fs = require('fs');
const path = require('path');

const here = __dirname;
const examples = fs.readFileSync(path.join(here, 'training_data', 'code_suggestion_examples.jsonl'), 'utf8')
  .split(/\r?\n/).filter(Boolean).map(JSON.parse);
const tokenize = source => {
  const words = (source.toLowerCase().match(/[A-Za-z_][A-Za-z_0-9]*|\+?=|\S/g) || []);
  return words.map(word => `tok:${word}`).concat(words.slice(0, -1).map((word, i) => `bi:${word}_${words[i + 1]}`));
};
if (examples.length < 10 || examples.some(item => !item.label || !item.code)) throw new Error('Dataset needs at least ten labelled {label, code} examples.');
const labels = [...new Set(examples.map(item => item.label))].sort();
const documents = Object.fromEntries(labels.map(label => [label, 0]));
const counts = Object.fromEntries(labels.map(label => [label, new Map()]));
const vocabulary = new Set();
for (const example of examples) {
  documents[example.label]++;
  for (const feature of tokenize(example.code)) {
    vocabulary.add(feature);
    counts[example.label].set(feature, (counts[example.label].get(feature) || 0) + 1);
  }
}
const model = { model_type: 'multinomial_naive_bayes_code_suggestions', algorithm: 'multinomial naive bayes with lexical bigrams', trained_examples: examples.length, labels, log_priors: {}, log_likelihoods: {}, default_log_likelihoods: {} };
for (const label of labels) {
  const total = [...counts[label].values()].reduce((sum, value) => sum + value, 0) + vocabulary.size;
  model.log_priors[label] = Math.log(documents[label] / examples.length);
  model.default_log_likelihoods[label] = Math.log(1 / total);
  model.log_likelihoods[label] = Object.fromEntries([...counts[label]].map(([feature, count]) => [feature, Math.log((count + 1) / total)]));
}
const output = path.join(here, 'training_data', 'code_suggestion_model.json');
fs.writeFileSync(output, JSON.stringify(model, null, 2) + '\n');
console.log(JSON.stringify({ examples: examples.length, labels, output }, null, 2));
