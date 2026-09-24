def slow_function():
    sum = 0
    for i in range(10000000):
        sum += i

def fast_function():
    sum = 0
    for i in range(10000):
        sum += i

slow_function()
fast_function()
