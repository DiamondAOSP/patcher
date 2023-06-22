CSI = "\033["


def color(c: str):
    return f"{CSI}{c}m"


RESET = color("0")
RED = color("31")
CYAN = color("36")
