"""
Test script with intentional energy anti-patterns for testing the suggestion engine.
"""

# Pattern 1: Manual list build with .append()
def build_squares():
    result = []
    for i in range(1000):
        result.append(i * i)
    return result

# Pattern 2: Manual sum loop
def manual_sum():
    total = 0
    for i in range(10000000):
        total += i
    return total

# Pattern 3: String concatenation in a loop
def build_string():
    result = ""
    for i in range(1000):
        result += str(i)
    return result

# Pattern 4: List membership test
def check_membership(x):
    if x in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        return True
    return False

# Pattern 5: List comprehension passed to sum()
def sum_with_list_comp():
    return sum([x * x for x in range(1000)])

# Pattern 8: range(len(...))
def iterate_with_range_len():
    data = [1, 2, 3, 4, 5]
    for i in range(len(data)):
        print(data[i])

# Pattern 9: Nested loops
def nested_loops():
    for i in range(100):
        for j in range(100):
            pass

# A clean function (should NOT trigger suggestions)
def clean_function():
    return sum(x * x for x in range(1000))


# Run everything
build_squares()
manual_sum()
build_string()
check_membership(5)
sum_with_list_comp()
iterate_with_range_len()
nested_loops()
clean_function()
