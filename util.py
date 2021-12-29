import math
import which


def which_or_None(str):
    try:
        return which.which(str)
    except which.WhichError:
        return None


# Safe conversion of logarithm to floor integer value
def safe_int_log(value, base):
    log = int(math.floor(math.log(value, base)))
    while base ** log > value:
        log -= 1
    while base ** (log + 1) <= value:
        log += 1
    return log


__all__ = ["safe_int_log"]
