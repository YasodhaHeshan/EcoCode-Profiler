def list_build(limit):
    values = []
    for i in range(limit):
        values.append(i * 2 + 1)
    return values


def repeated_membership(items, lookups):
    found = []
    for value in items:
        if value in lookups:
            found.append(value)
    return found


def expensive_sum(values):
    total = 0
    for value in values:
        total += value
    return total


def nested_work(rows):
    output = []
    for row in rows:
        for item in row:
            if item % 3 == 0:
                output.append(item * item)
    return output


def main():
    rows = [[(i * j) % 17 for j in range(60)] for i in range(80)]
    values = list_build(15000)
    lookups = set(range(0, 2000, 2))
    filtered = repeated_membership(values, lookups)
    total = expensive_sum(filtered)
    squares = nested_work(rows)
    print(total, len(squares), len(filtered))


if __name__ == "__main__":
    main()
