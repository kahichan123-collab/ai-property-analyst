# -*- coding: utf-8 -*-
"""tools/research.py — Research Tools (PHASE 5)

Each function returns a LIST of structured entries:
  {source,title,date,data,relevance,confidence}

Truth rules:
  * We never fabricate evidence.
  * If a live data source is not connected for a query, the tool returns [] plus a
    single availability entry tagged confidence=low and reason, so the report can say
    honestly "no comparable evidence connected".
  * A demo/local dataset can be loaded from RESEARCH_DATA_FILE (JSON) — entries are then
    tagged source="demo" so the report marks them clearly as non-live.

Comparable benchmark math is deterministic and separate from data acquisition.
"""
from __future__ import annotations
import json, os
from statistics import median

DATA_FILE = os.environ.get("RESEARCH_DATA_FILE")  # optional structured local dataset

# Below this many usable comparables no benchmark is produced. One data point is
# not a benchmark: it can be a distressed sale, a different layout or floor, and
# turning it into a "fair value" would be a fabricated certainty. Enforced here
# so user-supplied and research-supplied evidence obey the same rule.
MIN_COMPARABLES_FOR_BENCHMARK = 2


def _entry(source, title, date, data, relevance, confidence, reason=None):
    return {"source": source, "title": title, "date": date, "data": data,
            "relevance": relevance, "confidence": confidence,
            **( {"reason": reason} if reason else {})}


def _load_dataset():
    if not DATA_FILE or not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _none_entry(topic, reason):
    return _entry("unavailable", f"{topic}: no connected live source",
                  None, {}, topic, "low",
                  reason=reason or "no reliable public endpoint wired yet (anti-bot/paid); ")


def search_comparables(subject: dict) -> list:
    ds = _load_dataset().get("comparables", [])
    if ds:
        return [_entry("demo-dataset", c.get("title", "comparable"),
                       c.get("date"), {k: c.get(k) for k in
                       ("price", "usable_area_sqft", "estate", "block", "layout", "date")},
                       "comparable", c.get("confidence", "low")) for c in ds]
    return [_none_entry("comparables", "connect a transaction data source (e.g., land registry / CentaData feed)")]


def search_rental_comparables(subject: dict) -> list:
    ds = _load_dataset().get("rentals", [])
    if ds:
        return [_entry("demo-dataset", r.get("title", "rental"), r.get("date"),
                       {k: r.get(k) for k in ("monthly_rent", "usable_area_sqft", "estate", "layout", "date")},
                       "rental-evidence", r.get("confidence", "low")) for r in ds]
    return [_none_entry("rental evidence", "connect rental listing feed")]


def search_market_context(subject: dict) -> list:
    ds = _load_dataset().get("market", [])
    if ds:
        return [_entry("demo-dataset", m.get("title", "market"), m.get("date"),
                       {k: m.get(k) for k in ("metric", "value", "note", "date")},
                       "market-context", m.get("confidence", "low")) for m in ds]
    return [_none_entry("market context", "connect market/news feed")]


def search_mortgage_context(subject: dict) -> list:
    ds = _load_dataset().get("mortgage", [])
    if ds:
        return [_entry("demo-dataset", m.get("title", "mortgage"), m.get("date"),
                       {k: m.get(k) for k in ("metric", "value", "note", "date")},
                       "mortgage-context", m.get("confidence", "low")) for m in ds]
    return [_none_entry("mortgage context", "connect HKMA/bank rate feed")]


def search_policy_context(subject: dict) -> list:
    ds = _load_dataset().get("policy", [])
    if ds:
        return [_entry("demo-dataset", p.get("title", "policy"), p.get("date"),
                       {k: p.get(k) for k in ("metric", "value", "note", "date")},
                       "policy-context", p.get("confidence", "low")) for p in ds]
    return [_none_entry("policy context", "connect policy/news feed")]


# ---------------- deterministic comparable math (no LLM) ----------------
def compute_comparable_metrics(subject_price, subject_area_sqft, comps) -> dict:
    """comp area must be usable sq ft. Every input flagged: observed fact / model assumption."""
    usable = [c for c in comps or []
              if c.get("price") and c.get("usable_area_sqft")] if comps else []
    if not comps or len(usable) < MIN_COMPARABLES_FOR_BENCHMARK:
        flags = ["model_assumption: no live comparables connected"]
        if comps and len(usable) < MIN_COMPARABLES_FOR_BENCHMARK:
            flags = [f"insufficient evidence: only {len(usable)} comparable(s); "
                     f"at least {MIN_COMPARABLES_FOR_BENCHMARK} are required before a "
                     f"benchmark is produced"]
        return {"comparable_count": len(usable), "median_psf": None,
                "adjusted_benchmark_psf": None,
                "subject_asking_psf": round(subject_price / subject_area_sqft, 0) if subject_area_sqft else None,
                "fair_value_range": None, "premium_pct": None,
                "evidence": [], "flags": flags}
    psfs = [c["price"] / c["usable_area_sqft"] for c in usable]
    med = median(psfs) if psfs else None
    # simple adjusted benchmark: same-estate weight 1.0 else 0.9; same layout +0.02
    adj = []
    for c in usable:
        p = c["price"] / c["usable_area_sqft"]
        f = 1.0
        if c.get("estate") and c.get("subject_estate") and c["estate"] == c["subject_estate"]:
            f = 1.0
        else:
            f = 0.9
        if c.get("layout") and c.get("subject_layout") and c["layout"] == c["subject_layout"]:
            f = 1.02
        adj.append(p * f)
    bench = median(adj) if adj else med
    subj_psf = subject_price / subject_area_sqft if subject_area_sqft else None
    prem = (subj_psf / bench - 1) * 100 if (subj_psf and bench) else None
    lo, hi = (bench * 0.95, bench * 1.05) if bench else (None, None)
    # Per-entry view so the report can show WHICH comparable drove the number
    # and which one is an outlier. A median hides a stale or mistyped comp.
    evidence = []
    for c in usable:
        psf = c["price"] / c["usable_area_sqft"]
        evidence.append({
            "title": c.get("title"), "source": c.get("source"), "date": c.get("date"),
            "estate": c.get("estate"), "layout": c.get("layout"),
            "price": round(c["price"], 0),
            "usable_area_sqft": round(c["usable_area_sqft"], 0),
            "psf": round(psf, 0),
            "vs_benchmark_pct": (round(psf / bench - 1, 4) if bench else None),
        })
    return {"comparable_count": len(usable),
            "median_psf": round(med, 0) if med else None,
            "adjusted_benchmark_psf": round(bench, 0) if bench else None,
            "subject_asking_psf": round(subj_psf, 0) if subj_psf else None,
            "fair_value_range": [round(lo, 0), round(hi, 0)] if lo else None,
            "premium_pct": round(prem, 1) if prem is not None else None,
            "evidence": evidence,
            "flags": ["observed fact: prices from comparables", "model_assumption: adjustment factors"]}
