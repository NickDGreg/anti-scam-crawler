"""Compatibility layer exposing crawler, data extraction, and legacy probes."""

from __future__ import annotations

from typing import Dict

from .archival_crawler import MappingInputs, MappingResult, run_mapping
from .deepdive_strategist import ProbeInputs, ProbeResult, run_targeted_probe

# Backwards compatibility with the previous CLI/API naming.
ExtractInputs = ProbeInputs


def run_extraction(inputs: ProbeInputs) -> Dict[str, object]:
    """Legacy entry point that delegates to the deep-dive strategist."""
    result = run_targeted_probe(inputs)
    if isinstance(result, ProbeResult):
        return result.to_dict()
    return result  # type: ignore[return-value]


__all__ = [
    "MappingInputs",
    "MappingResult",
    "ProbeInputs",
    "ProbeResult",
    "ExtractInputs",
    "run_mapping",
    "run_extraction",
    "run_targeted_probe",
]
