"""Parity fixture: decorators, async, f-strings, match, walrus, unicode."""
import os
from pathlib import Path as P

def deco(fn):
    return fn

@deco
async def fetch(url: str) -> str:
    return f"hello {url}"

class Box:
    """A small container."""

    def __init__(self, value):
        self.value = value

    def grow(self, n):
        self.value += n
        return self.value

def classify(n):
    label = "even" if (n := n + 1) % 2 == 0 else "odd"
    match label:
        case "even":
            return Box(n).grow(1)
        case _:
            return os.cpu_count()

def cafe(x):
    return classify(x)
