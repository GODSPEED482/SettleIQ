"""Prove the test harness only adds layers on top of the certified image (spec 9.7.2, AC-REL-03).

    python check_layers.py <subject-inspect.json> <harness-inspect.json>

Each argument is the output of `docker image inspect`. The subject's layers must be an exact
prefix of the harness's, so the suite ran against the subject's bytes plus test code only.
"""

import json
import sys


def layers(path: str) -> list[str]:
    [image] = json.loads(open(path, encoding="utf-8").read())
    return image["RootFS"]["Layers"]


def check(subject: list[str], harness: list[str]) -> None:
    if not subject or harness[: len(subject)] != subject or len(harness) <= len(subject):
        raise SystemExit("harness does not extend the subject image unchanged")


if __name__ == "__main__":
    check(layers(sys.argv[1]), layers(sys.argv[2]))
    print("subject layers unchanged")  # noqa: T201
