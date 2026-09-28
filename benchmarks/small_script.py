def small_work():
    total = 0
    for i in range(1000):
        total += i * i
    return total


def main():
    result = small_work()
    print(result)


if __name__ == "__main__":
    main()
