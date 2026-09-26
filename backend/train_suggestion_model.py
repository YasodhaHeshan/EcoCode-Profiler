"""Train the dependency-free code suggestion model: python backend/train_suggestion_model.py."""
import json
import math
import os
from collections import Counter, defaultdict
from suggestions import tokenize

HERE = os.path.dirname(__file__)
DATASET = os.path.join(HERE, "training_data", "code_suggestion_examples.jsonl")
OUTPUT = os.path.join(HERE, "training_data", "code_suggestion_model.json")


def main():
    with open(DATASET, encoding="utf-8") as dataset:
        examples = [json.loads(line) for line in dataset if line.strip()]
    if len(examples) < 10 or any(not item.get("label") or not item.get("code") for item in examples):
        raise ValueError("The dataset needs at least ten labelled {label, code} examples")
    labels = sorted({item["label"] for item in examples})
    documents, tokens, vocabulary = Counter(), defaultdict(Counter), set()
    for item in examples:
        features = tokenize(item["code"])
        documents[item["label"]] += 1
        tokens[item["label"]].update(features)
        vocabulary.update(features)
    payload = {"model_type": "multinomial_naive_bayes_code_suggestions", "algorithm": "multinomial naive bayes with lexical bigrams", "trained_examples": len(examples), "labels": labels, "log_priors": {}, "log_likelihoods": {}, "default_log_likelihoods": {}}
    for label in labels:
        denominator = sum(tokens[label].values()) + len(vocabulary)
        payload["log_priors"][label] = math.log(documents[label] / len(examples))
        payload["default_log_likelihoods"][label] = math.log(1 / denominator)
        payload["log_likelihoods"][label] = {token: math.log((count + 1) / denominator) for token, count in tokens[label].items()}
    with open(OUTPUT, "w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
    print(json.dumps({"examples": len(examples), "labels": labels, "model": OUTPUT}, indent=2))


if __name__ == "__main__":
    main()
