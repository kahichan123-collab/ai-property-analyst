# -*- coding: utf-8 -*-
"""tools/provenance.py — data-integrity ledger (spec #20).

Rule enforced product-wide: every material number carries a provenance tag and,
where useful, a confidence. Nothing in the pipeline is allowed to invent a value
and present it as observed fact.

Vocabulary (tools/config.py):
    Observed | User Input | Estimated | Assumption | Research Approximation |
    Model Output
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C


def pv(value, source: str = C.MODEL, basis: str | None = None,
       confidence: str = "medium") -> dict:
    """A single provenance-tagged value."""
    return {"value": value, "provenance": source, "basis": basis,
            "confidence": confidence}


class Ledger:
    """Collects provenance tags so the report can show / audit them."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def add(self, key: str, value, source: str, basis: str | None = None,
            confidence: str = "medium", label: str | None = None) -> None:
        self.items[key] = {"value": value, "provenance": source, "basis": basis,
                           "confidence": confidence, "label": label or key}

    def get(self, key: str):
        it = self.items.get(key)
        return it["value"] if it else None

    def as_dict(self, public: bool = True) -> dict:
        return {k: (v if public else {"provenance": v["provenance"],
                                      "confidence": v["confidence"]})
                for k, v in self.items.items()}

    # ---- views used by the report ---------------------------------------- #
    def grouped(self) -> dict:
        out: dict[str, list] = {label: [] for label in C.PROVENANCE_ORDER}
        for key, it in self.items.items():
            out.setdefault(it["provenance"], []).append(
                {"key": key, "label": it["label"], "value": it["value"],
                 "confidence": it["confidence"], "basis": it["basis"]})
        return {k: v for k, v in out.items() if v}

    def counts(self) -> dict:
        counts = {label: 0 for label in C.PROVENANCE_ORDER}
        for it in self.items.values():
            counts[it["provenance"]] = counts.get(it["provenance"], 0) + 1
        return {k: v for k, v in counts.items() if v}


def data_quality(ledger: Ledger, missing_fields: list | None = None,
                 warnings: list | None = None, extra: dict | None = None) -> dict:
    """Summarise how much of the analysis rests on hard evidence."""
    counts = ledger.counts()
    soft = sum(counts.get(k, 0) for k in (C.ESTIMATED, C.ASSUMED, C.RESEARCH))
    hard = sum(counts.get(k, 0) for k in (C.OBSERVED, C.USER, C.MODEL))
    total = soft + hard
    return {
        "provenance_counts": counts,
        "hard_evidence_items": hard,
        "soft_or_assumed_items": soft,
        "evidence_ratio": round(hard / total, 3) if total else None,
        "unresolved_inputs": list(missing_fields or []),
        "warnings": list(warnings or []),
        "note": ("Model Output is deterministic but rests on the stated inputs; "
                 "Estimated / Assumption / Research Approximation items are the "
                 "main sources of uncertainty."),
        **(extra or {}),
    }
