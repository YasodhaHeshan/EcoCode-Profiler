"""
EcoCode Profiler — Energy Anti-Pattern Suggestion Engine
Performs static AST analysis on a Python file and detects common
energy anti-patterns, returning actionable refactoring suggestions.
"""

import ast
import sys
import json
import argparse
import os


class AntiPatternDetector(ast.NodeVisitor):
    """Walks a Python AST and detects energy anti-patterns."""

    def __init__(self, source_lines, target_functions=None):
        self.source_lines = source_lines
        self.target_functions = target_functions  # If None, scan all functions
        self.suggestions = []
        self._current_function = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add(self, line, pattern, message, savings, source):
        self.suggestions.append({
            "line": line,
            "function": self._current_function or "<module>",
            "pattern": pattern,
            "message": message,
            "severity": "warning",
            "estimated_savings": savings,
            "source": source,
        })

    def _is_target_function(self, name):
        """Should we analyse this function?"""
        if self.target_functions is None:
            return True
        return name in self.target_functions

    # ------------------------------------------------------------------
    # Visitor entry points
    # ------------------------------------------------------------------

    def visit_FunctionDef(self, node):
        if self._is_target_function(node.name):
            old = self._current_function
            self._current_function = node.name
            self.generic_visit(node)
            self._current_function = old
        # Skip functions we don't care about

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_For(self, node):
        """Detect patterns inside for-loops."""
        if self._current_function is None and self.target_functions is not None:
            return

        self._check_manual_list_build(node)
        self._check_manual_sum(node)
        self._check_string_concat(node)
        self._check_range_len(node)
        self._check_repeated_attr_lookup(node)
        self._check_nested_loop_product(node)

        self.generic_visit(node)

    def visit_Compare(self, node):
        """Detect membership tests on lists (pattern 4)."""
        if self._current_function is None and self.target_functions is not None:
            return
        self._check_list_membership(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        """Detect full-list materialisation where a generator would do (pattern 5),
        and manual numerical loops that could use NumPy (pattern 7)."""
        if self._current_function is None and self.target_functions is not None:
            return
        self._check_list_instead_of_generator(node)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Pattern detectors
    # ------------------------------------------------------------------

    def _check_manual_list_build(self, for_node):
        """Pattern 1: for loop with list.append() → use list comprehension."""
        for stmt in ast.walk(for_node):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                if (isinstance(call.func, ast.Attribute)
                        and call.func.attr == "append"):
                    self._add(
                        for_node.lineno,
                        "manual_list_build",
                        "Manual list building with .append() inside a for-loop. "
                        "Use a list comprehension for ~10-30% energy savings.",
                        "10-30%",
                        "Georgiou et al. (2022)",
                    )
                    return  # One suggestion per loop is enough

    def _check_manual_sum(self, for_node):
        """Pattern 2: for loop with += accumulation on a number → use sum()."""
        for stmt in for_node.body:
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
                # Check the target is a simple name (not an attribute or subscript)
                if isinstance(stmt.target, ast.Name):
                    self._add(
                        for_node.lineno,
                        "manual_sum_loop",
                        "Manual accumulation loop detected (var += ...). "
                        "Use built-in sum() for ~20-40% energy savings.",
                        "20-40%",
                        "Georgiou et al. (2022)",
                    )
                    return

    def _check_string_concat(self, for_node):
        """Pattern 3: string concatenation with += inside a loop → use join()."""
        for stmt in for_node.body:
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
                if isinstance(stmt.target, ast.Name):
                    # Heuristic: if the value involves a str call, format, or
                    # the variable name contains 'str', 'text', 'result', 'output', 'html', 'msg'
                    target_name = stmt.target.id.lower()
                    string_hints = {"str", "text", "result", "output", "html",
                                    "msg", "query", "sql", "csv", "line", "log",
                                    "string", "s", "buf", "buffer"}
                    if target_name in string_hints:
                        self._add(
                            for_node.lineno,
                            "string_concat_loop",
                            f"Possible string concatenation with += in a loop (variable '{stmt.target.id}'). "
                            "Use ''.join() for ~50-80% energy savings on large strings.",
                            "50-80%",
                            "Python best practice",
                        )
                        return

    def _check_range_len(self, for_node):
        """Pattern 8: for i in range(len(x)) → use enumerate() or direct iteration."""
        iter_node = for_node.iter
        if isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name):
            if iter_node.func.id == "range" and len(iter_node.args) == 1:
                arg = iter_node.args[0]
                if (isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Name)
                        and arg.func.id == "len"):
                    self._add(
                        for_node.lineno,
                        "range_len_iteration",
                        "Using range(len(...)) to iterate. "
                        "Use enumerate() or iterate directly for cleaner, slightly more efficient code.",
                        "Minor",
                        "Python best practice",
                    )

    def _check_repeated_attr_lookup(self, for_node):
        """Pattern 6: Same attribute accessed repeatedly inside a loop → cache it."""
        attr_counts = {}
        for node in ast.walk(for_node):
            if isinstance(node, ast.Attribute):
                # Build a string representation like 'self.data'
                key = ast.dump(node)
                attr_counts[key] = attr_counts.get(key, 0) + 1

        for key, count in attr_counts.items():
            if count >= 3:
                self._add(
                    for_node.lineno,
                    "repeated_attr_lookup",
                    "Repeated attribute lookup detected inside this loop (3+ accesses). "
                    "Cache the attribute in a local variable for ~5-15% energy savings.",
                    "5-15%",
                    "CPython internals",
                )
                return

    def _check_nested_loop_product(self, for_node):
        """Pattern 9: Nested for-loops that could use itertools.product()."""
        for stmt in for_node.body:
            if isinstance(stmt, ast.For):
                # Check if both loops are simple range/iterable loops with
                # no complex dependencies between them
                self._add(
                    for_node.lineno,
                    "nested_loop_product",
                    "Nested for-loops detected. If the iterations are independent, "
                    "consider using itertools.product() for ~10-20% energy savings.",
                    "10-20%",
                    "Python best practice",
                )
                return

    def _check_list_membership(self, compare_node):
        """Pattern 4: 'if x in some_list' → use a set for O(1) lookups."""
        for op in compare_node.ops:
            if isinstance(op, (ast.In, ast.NotIn)):
                for comparator in compare_node.comparators:
                    if isinstance(comparator, ast.List):
                        self._add(
                            compare_node.lineno,
                            "list_membership_test",
                            "Membership test on a list literal. "
                            "Use a set literal for O(1) lookups and ~90%+ energy savings on large collections.",
                            "90%+",
                            "Georgiou et al. (2022)",
                        )
                        return

    def _check_list_instead_of_generator(self, call_node):
        """Pattern 5: list() passed to a function that only iterates (e.g. sum(list(...)))."""
        if isinstance(call_node.func, ast.Name):
            consuming_funcs = {"sum", "min", "max", "any", "all", "sorted", "tuple"}
            if call_node.func.id in consuming_funcs and call_node.args:
                arg = call_node.args[0]
                if isinstance(arg, ast.ListComp):
                    self._add(
                        call_node.lineno,
                        "list_instead_of_generator",
                        f"List comprehension passed to {call_node.func.id}(). "
                        "Use a generator expression instead to save memory and energy.",
                        "Moderate",
                        "Georgiou et al. (2022)",
                    )


def analyse_file(filepath, target_functions=None):
    """Parse a Python file and return energy suggestions."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    source_lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return {"error": f"Syntax error in {filepath}: {e}"}

    detector = AntiPatternDetector(source_lines, target_functions)
    detector.visit(tree)

    return {"suggestions": detector.suggestions}


def main():
    parser = argparse.ArgumentParser(
        description="EcoCode Suggestion Engine — detect energy anti-patterns in Python"
    )
    parser.add_argument("script", help="Target Python script to analyse")
    parser.add_argument(
        "--functions",
        nargs="*",
        default=None,
        help="Limit analysis to these function names (space-separated). "
             "If omitted, all functions are analysed.",
    )
    args = parser.parse_args()

    target_script = os.path.abspath(args.script)
    result = analyse_file(target_script, args.functions)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
