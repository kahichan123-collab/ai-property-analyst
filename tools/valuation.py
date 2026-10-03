# -*- coding: utf-8 -*-
"""tools/valuation.py — valuation engine (spec #14).

Produces, deterministically:

    Asking price  vs  Estimated fair value  ->  premium / discount  ->  class

Rules that this module enforces:
  * The fair value is derived ONLY from comparable evidence supplied by
    tools/research.py (adjusted benchmark psf x area).
  * With no usable comparable evidence the headline answer is
    ``insufficient_data`` — the engine never invents a fair value.
  * A second, clearly-labelled "income approach (Assumption)" cross-check may be
    shown when rental assumptions + a capitalisation rate exist. It never
    replaces the evidence-based fair value and is never called observed.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C

EVIDENCE_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _confidence_from_evidence(comps: list, count: int) -> str:
    if not comps or not count:
        return "none"
    ranks = [EVIDENCE_CONFIDENCE_RANK.get(str(c.get("confidence", "low")).lower(), 1)
             for c in comps]
    avg = sum(ranks) / len(ranks)
    if count >= 4 and avg >= 2.0:
        return "medium"
    if count >= 2:
        return "low_to_medium"
    return "low"


def valuate(asking_price: float | None, area_sqft: float | None,
            comp_metrics: dict | None, comp_entries: list | None = None,
            rental_uw: dict | None = None, purpose: str = "owner_occupier") -> dict:
    comps = comp_entries or []
    cm = comp_metrics or {}
    asking_psf = (round(asking_price / area_sqft, 0)
                  if (asking_price and area_sqft) else None)

    bench = cm.get("adjusted_benchmark_psf")
    count = cm.get("comparable_count") or 0
    fair_value = round(bench * area_sqft, 0) if (bench and area_sqft) else None
    fv_range = None
    if fair_value and cm.get("fair_value_range"):
        lo, hi = cm["fair_value_range"]
        fv_range = [round(lo * area_sqft, 0), round(hi * area_sqft, 0)]

    premium = None
    if asking_price and fair_value:
        premium = round(asking_price / fair_value - 1, 6)

    classification = C.valuation_class(premium)
    live_evidence = [c for c in comps if str(c.get("source")) == "user-input"]
    demo_evidence = [c for c in comps if str(c.get("source")) == "demo-dataset"]
    # "unavailable" placeholder entries from tools/research.py are NOT evidence:
    # counting them made evidence_source claim "live/connected" when nothing was
    # connected at all.
    other_evidence = [c for c in comps
                      if str(c.get("source")) not in ("user-input", "demo-dataset",
                                                      "unavailable")]

    if premium is None:
        reason = ("no usable comparable evidence connected — fair value is NOT "
                  "estimated. Connect a transaction data source, or supply "
                  "comparables, to enable valuation.")
    else:
        reason = None

    # ---- income-approach cross-check (Assumption, never the headline) ---- #
    income_approach = None
    if rental_uw and asking_price:
        cap = rental_uw.get("exit_cap")
        noi = rental_uw.get("noi_pre_tax_y1")
        if cap and noi and cap > 0:
            iv = round(noi / cap, 0)
            income_approach = {
                "value": iv,
                "cap_rate_used": cap,
                "premium_pct": round(asking_price / iv - 1, 6) if iv else None,
                "provenance": C.ASSUMED,
                "note": ("Exit capitalisation rate supplied by the user is used as a "
                         "market cap-rate proxy. This is an ASSUMPTION, not evidence, "
                         "and does not set the headline fair value."),
            }

    buy_target = round(fair_value * (1 - C.FAIR_VALUE_DISCOUNT_BUFFER), 0) if fair_value else None

    return {
        "asking_price": round(asking_price, 0) if asking_price else None,
        "asking_psf": asking_psf,
        "fair_value": fair_value,
        "fair_value_psf": round(bench, 0) if bench else None,
        "fair_value_range": fv_range,
        "adjusted_benchmark_psf": round(bench, 0) if bench else None,
        "median_psf": cm.get("median_psf"),
        "subject_asking_psf": cm.get("subject_asking_psf") or asking_psf,
        "comparable_count": count,
        "premium_pct": premium,
        "premium_hk": (round(asking_price - fair_value, 0)
                       if (asking_price and fair_value) else None),
        "classification": classification,
        "classification_label": C.VALUATION_LABELS[classification],
        "confidence": _confidence_from_evidence(comps, count),
        "evidence_source": ("user-supplied" if live_evidence
                            else ("demo/research dataset" if demo_evidence
                                  else ("live/connected" if other_evidence else "none"))),
        "evidence_entries": [{"title": c.get("title"), "source": c.get("source"),
                              "date": c.get("date"), "confidence": c.get("confidence"),
                              "estate": c.get("estate"), "layout": c.get("layout")}
                             for c in comps],
        "evidence_breakdown": cm.get("evidence") or [],
        "insufficient_reason": reason,
        "buy_target_price": buy_target,
        "buy_target_basis": f"fair value x (1 - {C.FAIR_VALUE_DISCOUNT_BUFFER:.0%}) buffer",
        "income_approach_crosscheck": income_approach,
        "flags": list(cm.get("flags") or []),
        "provenance": {
            "asking_price": C.USER if asking_price else None,
            "fair_value": C.MODEL if fair_value else None,
            "fair_value_inputs": C.RESEARCH if count else None,
            "income_approach_crosscheck": C.ASSUMED if income_approach else None,
        },
        "note": ("Fair value is a model output from comparable evidence; it is not "
                 "an appraisal and not a guarantee."),
    }
