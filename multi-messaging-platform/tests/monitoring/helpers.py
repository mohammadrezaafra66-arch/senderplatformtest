"""Helpers for parsing Prometheus text exposition in tests."""

from __future__ import annotations

import re


def sum_counter_values(body: str, metric_name: str, *, label_fragment: str = "") -> float:
    total = 0.0
    prefix = f"{metric_name}{{"
    for line in body.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith(prefix) and not line.startswith(f"{metric_name} "):
            continue
        if label_fragment and label_fragment not in line:
            continue
        match = re.search(r"\s([0-9.eE+-]+)$", line)
        if match:
            total += float(match.group(1))
    return total


def metric_body_has_nonzero_histogram(body: str, metric_name: str) -> bool:
    for line in body.splitlines():
        if line.startswith(f"{metric_name}_count{{") or line.startswith(f"{metric_name}_count "):
            match = re.search(r"\s([0-9.eE+-]+)$", line)
            if match and float(match.group(1)) > 0:
                return True
    return False
