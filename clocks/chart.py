import itertools
import math

ch = r"•-\|/ "


def cch(fil, per, a):
    c = divmod(a, per)
    p = round(per, 2)
    b = round(c[1], 2)
    if b == p or b == 0:
        a = (a + 0.25) * 8
        return ch[round(a) % 4 + 1]
    if c[0] < fil:
        return ch[0]
    return ch[-1]


def pie(fil, tot):
    r = min(tot // 2, 8) + 4
    per = 1 / tot
    final = f"{fil} / {tot}"
    final = f"Clock{final:^{4 * r - 9}}".rstrip() + "\n"
    for y in range(-r, r + 1):
        for x in range(-2 * r, 2 * r + 1):
            x /= -2
            i = round((x * x + y * y) / (r * r) - 1, 1)
            a = math.atan2(x, y) / math.pi / 2 + 0.5
            if i < 0:
                a = math.atan2(x, y) / math.pi / 2 + 0.5
                n = cch(fil, per, a)
            elif i > 0:
                n = ch[-1]
            else:
                n = ch[round(a * 8) % 4 + 1]
            final += n
        final = final.rstrip() + "\n"
    return f"**```{final.rstrip()}```**"
