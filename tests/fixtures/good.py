"""A small module with nothing to report."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def common(a: list[int], b: list[int]) -> list[int]:
    wanted = set(b)
    return [x for x in a if x in wanted]


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()
