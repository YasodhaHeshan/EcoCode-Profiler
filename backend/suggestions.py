"""Learned local energy-refactoring suggestions for Python source code."""
import argparse
import ast
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter

MODEL_PATH = os.path.join(os.path.dirname(__file__), "training_data", "code_suggestion_model.json")
MIN_CONFIDENCE = 0.72
SUGGESTIBLE_LABELS = {"manual_list_build", "manual_sum_loop", "string_concat_loop", "range_len_iteration", "list_membership_test", "list_instead_of_generator"}
# Used only when no general-purpose code model has been configured.
FALLBACK_DETAILS = {
    "manual_list_build": ("Build this collection with a comprehension.", "10-30%"),
    "manual_sum_loop": ("Use Python's C-implemented sum() for this accumulation.", "20-40%"),
    "string_concat_loop": ("Collect parts and join them once rather than repeatedly copying a string.", "50-80%"),
    "range_len_iteration": ("Iterate over values directly, or use enumerate() when the index is needed.", "Minor"),
    "list_membership_test": ("Use a set literal for repeated constant membership checks.", "90%+"),
    "list_instead_of_generator": ("Pass a generator expression to avoid materialising an intermediate list.", "Moderate"),
}


def _json_from_model_response(content):
    """Extract the first JSON object even if a model adds a short preamble."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(content):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(content[index:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
        raise ValueError("No JSON object found in the code-model response")


def model_suggestions(source, endpoint, model_name, timeout_seconds):
    """Ask an OpenAI-compatible code model for general, source-aware improvements.

    This supports local Ollama and self-hosted compatible endpoints.  The
    response is treated strictly as data and is validated before it reaches
    VS Code; it is never automatically applied to the source file.
    """
    prompt = """You are an expert Python performance and energy reviewer. Review the complete Python source below. Find any meaningful, behaviour-preserving improvement, including algorithmic complexity, I/O, data structures, concurrency, memory allocation, and Python idioms. Do not invent issues. Return ONLY JSON in this exact form:
{"suggestions":[{"line":positive integer,"title":"short category","message":"specific reason","estimated_savings":"qualified estimate or Unknown","suggested_code":"replacement code or a concise patch","confidence":number from 0 to 1}]}
Each suggestion must identify a real line in the supplied source. Return an empty array when no safe improvement is evident. Never recommend a change that alters observable behavior without explicitly warning in message.

Python source:\n```python\n""" + source + "\n```"
    payload = json.dumps({"model": model_name, "temperature": 0.1, "stream": False, "max_tokens": 1200,
                          "response_format": {"type": "json_object"}, "messages": [
        {"role": "system", "content": "Return valid JSON only. Prefer correctness over speculative micro-optimizations."},
        {"role": "user", "content": prompt},
    ]}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        content = response_payload["choices"][0]["message"]["content"]
        result = _json_from_model_response(content)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
        return [], f"Code model unavailable ({error}); used offline fallback."
    if not isinstance(result, dict) or not isinstance(result.get("suggestions"), list):
        return [], "Code model returned an invalid response; used offline fallback."
    lines, suggestions = len(source.splitlines()), []
    for item in result["suggestions"][:20]:
        if not isinstance(item, dict) or not isinstance(item.get("line"), int) or not 1 <= item["line"] <= lines:
            continue
        if not all(isinstance(item.get(field), str) and item[field].strip() for field in ("title", "message", "suggested_code")):
            continue
        confidence = item.get("confidence", 0)
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = 0
        suggestions.append({
            "line": item["line"], "function": "<module>", "pattern": item["title"].strip()[:80],
            "message": item["message"].strip(), "severity": "warning",
            "estimated_savings": str(item.get("estimated_savings", "Unknown"))[:80],
            "source": f"code model ({model_name})", "confidence": round(confidence, 3),
            "suggested_code": item["suggested_code"].strip(),
        })
    return suggestions, None


def tokenize(source):
    """Normalized lexical tokens and bigrams are the model features."""
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\+?=|\S", source.lower())
    features = ["tok:" + word for word in words]
    features += ["bi:" + words[i] + "_" + words[i + 1] for i in range(len(words) - 1)]
    return features


class SuggestionModel:
    def __init__(self, payload):
        self.labels, self.log_priors = payload["labels"], payload["log_priors"]
        self.log_likelihoods = payload["log_likelihoods"]
        self.default_log_likelihoods = payload["default_log_likelihoods"]

    @classmethod
    def load(cls, model_path=MODEL_PATH):
        with open(model_path, encoding="utf-8") as model_file:
            payload = json.load(model_file)
        if payload.get("model_type") != "multinomial_naive_bayes_code_suggestions":
            raise ValueError("Unexpected suggestion model type")
        return cls(payload)

    def predict(self, source):
        scores = {}
        for label in self.labels:
            score, weights = self.log_priors[label], self.log_likelihoods[label]
            default = self.default_log_likelihoods[label]
            for feature, count in Counter(tokenize(source)).items():
                score += count * weights.get(feature, default)
            scores[label] = score
        maximum = max(scores.values())
        normalizer = maximum + math.log(sum(math.exp(value - maximum) for value in scores.values()))
        label = max(scores, key=scores.get)
        return label, math.exp(scores[label] - normalizer)


def unparse(node):
    return ast.unparse(node).strip()


def replacement_for(label, node):
    """Build a reviewable contextual rewrite; source is never modified."""
    if label == "manual_list_build" and isinstance(node, ast.For) and isinstance(node.target, ast.Name):
        for statement in node.body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "append" and isinstance(call.func.value, ast.Name) and len(call.args) == 1:
                    return f"{call.func.value.id} = [{unparse(call.args[0])} for {unparse(node.target)} in {unparse(node.iter)}]"
    if label in {"manual_sum_loop", "string_concat_loop"} and isinstance(node, ast.For) and isinstance(node.target, ast.Name):
        for statement in node.body:
            if isinstance(statement, ast.AugAssign) and isinstance(statement.op, ast.Add) and isinstance(statement.target, ast.Name):
                expression = unparse(statement.value)
                if label == "manual_sum_loop":
                    return f"{statement.target.id} = sum({expression} for {unparse(node.target)} in {unparse(node.iter)})"
                return f"{statement.target.id} = ''.join(str({expression}) for {unparse(node.target)} in {unparse(node.iter)})"
    if label == "range_len_iteration" and isinstance(node, ast.For):
        sequence = node.iter.args[0].args[0]
        if isinstance(node.target, ast.Name):
            return f"for {node.target.id} in {unparse(sequence)}:\n    ..."
        return f"for index, item in enumerate({unparse(sequence)}):\n    ..."
    if label == "list_membership_test" and isinstance(node, ast.Compare):
        values = next((part for part in node.comparators if isinstance(part, ast.List)), None)
        if values is not None:
            operator = "not in" if isinstance(node.ops[0], ast.NotIn) else "in"
            return f"{unparse(node.left)} {operator} {{{', '.join(unparse(value) for value in values.elts)}}}"
    if label == "list_instead_of_generator" and isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.ListComp):
        return f"{unparse(node.func)}({unparse(node.args[0])[1:-1]})"
    return "Review this expression and apply the refactoring only if behaviour is preserved."


def compatible(label, node):
    if label in {"manual_list_build", "manual_sum_loop", "string_concat_loop"}:
        return isinstance(node, ast.For)
    if label == "range_len_iteration":
        return (isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range" and len(node.iter.args) == 1 and isinstance(node.iter.args[0], ast.Call) and isinstance(node.iter.args[0].func, ast.Name) and node.iter.args[0].func.id == "len")
    if label == "list_membership_test":
        return isinstance(node, ast.Compare) and any(isinstance(value, ast.List) for value in node.comparators)
    return label == "list_instead_of_generator" and isinstance(node, ast.Call) and bool(node.args) and isinstance(node.args[0], ast.ListComp)


def analyse_file(filepath, target_functions=None, model_path=MODEL_PATH, ai_endpoint=None, ai_model=None, ai_timeout=180):
    with open(filepath, encoding="utf-8") as source_file:
        source = source_file.read()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as error:
        return {"error": f"Syntax error in {filepath}: {error}"}
    functions = [(node.lineno, node.end_lineno, node.name) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    ai_endpoint = ai_endpoint or os.environ.get("ECOCODE_SUGGESTION_ENDPOINT")
    ai_model = ai_model or os.environ.get("ECOCODE_SUGGESTION_MODEL", "qwen2.5-coder:7b")
    try:
        ai_timeout = max(15, min(float(ai_timeout), 600))
    except (TypeError, ValueError):
        ai_timeout = 180
    if ai_endpoint:
        suggestions, warning = model_suggestions(source, ai_endpoint, ai_model, ai_timeout)
        for suggestion in suggestions:
            suggestion["function"] = next((name for start, end, name in functions if start <= suggestion["line"] <= end), "<module>")
        if target_functions is not None:
            suggestions = [suggestion for suggestion in suggestions if suggestion["function"] in target_functions]
        if suggestions:
            return {"model_type": "general_code_model", "model": ai_model, "suggestions": suggestions}
        warning = warning or "Code model returned no usable suggestions; used offline fallback."
    try:
        model = SuggestionModel.load(model_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return {"error": f"Suggestion model unavailable: {error}", "suggestions": []}
    suggestions, seen = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.Compare, ast.Call)):
            continue
        function = next((name for start, end, name in functions if start <= node.lineno <= end), "<module>")
        if target_functions is not None and function not in target_functions:
            continue
        label, confidence = model.predict(ast.get_source_segment(source, node) or unparse(node))
        if label not in SUGGESTIBLE_LABELS or confidence < MIN_CONFIDENCE or not compatible(label, node) or (node.lineno, label) in seen:
            continue
        seen.add((node.lineno, label))
        message, savings = FALLBACK_DETAILS[label]
        suggestions.append({"line": node.lineno, "function": function, "pattern": label, "message": message, "severity": "warning", "estimated_savings": savings, "source": "learned code-pattern model", "confidence": round(confidence, 3), "suggested_code": replacement_for(label, node)})
    result = {"model_type": "multinomial_naive_bayes_code_suggestions", "suggestions": suggestions}
    if ai_endpoint and warning:
        result["warning"] = warning
    return result


def main():
    parser = argparse.ArgumentParser(description="EcoCode learned suggestion engine")
    parser.add_argument("script")
    parser.add_argument("--functions", nargs="*", default=None)
    parser.add_argument("--model", default=MODEL_PATH)
    parser.add_argument("--ai-endpoint", help="OpenAI-compatible local/self-hosted code-model endpoint")
    parser.add_argument("--ai-model", help="Code-model name, for example qwen2.5-coder:7b")
    parser.add_argument("--ai-timeout", type=float, default=180, help="Code-model request timeout in seconds (15-600)")
    args = parser.parse_args()
    print(json.dumps(analyse_file(os.path.abspath(args.script), args.functions, args.model, args.ai_endpoint, args.ai_model, args.ai_timeout), indent=2))


if __name__ == "__main__":
    main()
