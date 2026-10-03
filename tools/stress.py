# -*- coding: utf-8 -*-
"""tools/stress.py — deterministic stress tests (PHASE 8 spec #VIII)"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.underwriting import owner_underwriting, rental_underwriting

SCENARIOS = ["base", "rate_+1%", "rate_+2%", "price_-10%", "price_-15%", "income_-10%", "expense_higher"]


def stress_owner(**base) -> dict:
    out = {"base": owner_underwriting(**base)}
    out["rate_+1%"] = owner_underwriting(**{**base, "annual_rate": base["annual_rate"] + 0.01})
    out["rate_+2%"] = owner_underwriting(**{**base, "annual_rate": base["annual_rate"] + 0.02})
    if base.get("monthly_net_income"):
        out["income_-10%"] = owner_underwriting(
            **{**base, "monthly_net_income": base["monthly_net_income"] * 0.90})
    else:
        out["income_-10%"] = None
    out["price_-10%"] = owner_underwriting(**{**base, "price": base["price"] * 0.90})
    out["price_-15%"] = owner_underwriting(**{**base, "price": base["price"] * 0.85})
    out["expense_higher"] = owner_underwriting(
        **{**base, "monthly_mgmt_rates": base.get("monthly_mgmt_rates", 0) + 1500})
    return out


def summarize_owner_stress(rows: dict) -> list:
    """Rows of {scenario, dsr, surplus, payment, affordability, flags}."""
    out = []
    for name in SCENARIOS:
        r = rows.get(name)
        if r is None:
            continue
        out.append({"scenario": name,
                    "dsr": r["dsr"],
                    "monthly_payment": r["monthly_payment"],
                    "monthly_surplus": r["monthly_surplus"],
                    "affordability_score": r["affordability_score"],
                    "downside_flags": [x for x in r["risks"] if x != "no_major_flag"]})
    return out
