def build_values(limit):
    values = []
    for i in range(limit):
        if i % 2 == 0:
            values.append(i * 3)
    return values


def compute_sum(values):
    total = 0
    for value in values:
        total += value
    return total


def nested_scan(data):
    matches = []
    for row in data:
        for item in row:
            if item % 5 == 0:
                matches.append(item)
    return matches


def main():
    data = [[i + j for j in range(25)] for i in range(40)]
    values = build_values(3000)
    total = compute_sum(values)
    matches = nested_scan(data)
    print(total, len(matches))


if __name__ == "__main__":
    main()
