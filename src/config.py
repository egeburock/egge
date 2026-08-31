import tomllib
from pathlib import Path


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def tf_seconds(tf: str) -> int:
    num, unit = tf[:-1], tf[-1]
    return int(num) * (1 if unit == "s" else 60)
