"""Medium-level EcoCode Profiler test with intentional anti-patterns."""


def build_even_squares(limit=20_000):
    squares = []
    for number in range(limit):
        if number % 2 == 0:
            squares.append(number * number)
    return squares


def calculate_total(limit=100_000):
    total = 0
    for number in range(limit):
        total += number * 3
    return total


def create_report_line(limit=5_000):
    report = ""
    for number in range(limit):
        report += f"Item {number}: processed\n"
    return report


def find_status(status):
    # List membership should ideally use a set.
    if status in ["new", "pending", "running", "complete", "failed"]:
        return True
    return False


def sum_of_cubes(limit=20_000):
    # List comprehension is unnecessarily materialized.
    return sum([number ** 3 for number in range(limit)])


def print_records():
    records = ["alpha", "beta", "gamma", "delta"]
    for index in range(len(records)):
        print(index, records[index])


build_even_squares()
calculate_total()
create_report_line()
find_status("running")
sum_of_cubes()
print_records()
