# -*- coding: utf-8 -*-
"""tools/acquisition.py — full initial-cash requirement model (spec #9).

Deterministic. No LLM. The point is that "Down Payment" and "Total Initial Cash
Required" are different numbers and must never be conflated:

    Purchase price
    - Mortgage amount            (price x LTV)
    = Down payment
    + Stamp duty (HK AVD scale)
    + Agency fee                 (buyer side)
    + Legal / conveyancing fee
    + Mortgage-related fees      (bank fees, mortgage insurance — explicit input)
    + Valuation fee
    + Renovation / furnishing
    + Initial reserve
    = Total Initial Cash Required

Backward compatibility note: with ``fee_rate=0.015`` and all optional line items
at 0, ``total_initial_cash_required`` equals the legacy
``down + AVD + price * fee_rate`` used by the PHASE-3 anchors.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C
from tools.underwriting import get_avd

# Optional items default to zero so nothing is silently added to the cash call.
OPTIONAL_KEYS = ("mortgage_fees", "valuation_fee", "renovation_furnishing",
                 "initial_reserve")

LINE_LABELS = {
    "down_payment": ("首期 Down Payment", "Purchase price − mortgage amount"),
    "stamp_duty": ("從價印花稅 AVD Stamp Duty", "HK AVD Scale (2024 撤辣後)"),
    "agency_fee": ("代理佣金 Agency Fee", "buyer-side commission"),
    "legal_fee": ("律師費／契約費用 Legal Fee", "conveyancing + disbursements"),
    "mortgage_fees": ("按揭相關費用 Mortgage Fees", "bank fees / mortgage insurance"),
    "valuation_fee": ("估價費 Valuation Fee", "bank valuation"),
    "renovation_furnishing": ("裝修／傢俬 Renovation & Furnishing", "user input"),
    "initial_reserve": ("初始儲備 Initial Reserve", "user input"),
}


def _split_fee_rate(fee_rate: float | None) -> tuple[float, float]:
    """Legacy combined 'agency + legal' rate split 2:1 when not given explicitly."""
    if fee_rate is None:
        return C.ACQUISITION_DEFAULTS["agency_fee_pct"], C.ACQUISITION_DEFAULTS["legal_fee_pct"]
    return round(fee_rate * 2.0 / 3.0, 6), round(fee_rate / 3.0, 6)


def mortgage_insurance_estimate(price: float, ltv: float) -> float:
    """Advisory HK mortgage-insurance (MIP) approximation for LTV > 70%.

    Labelled an Assumption in the report and NOT added to the cash call unless
    the caller passes it explicitly as ``mortgage_fees``.
    """
    if ltv <= 0.70:
        return 0.0
    loan = price * ltv
    # coarse banding loosely mirroring HKMC-style pricing; advisory only.
    if ltv <= 0.80:
        rate = 0.0125
    elif ltv <= 0.85:
        rate = 0.0190
    elif ltv <= 0.90:
        rate = 0.0260
    else:
        rate = 0.0350
    return round(loan * rate, 0)


def acquisition_costs(price: float, ltv: float = 0.60, fee_rate: float | None = None,
                      agency_fee_pct: float | None = None,
                      legal_fee_pct: float | None = None,
                      mortgage_fees: float = 0.0,
                      valuation_fee: float = 0.0,
                      renovation_furnishing: float = 0.0,
                      initial_reserve: float = 0.0,
                      monthly_housing_cost: float | None = None,
                      area_sqft: float | None = None) -> dict:
    if price is None or price <= 0:
        raise ValueError("acquisition_costs: price must be > 0")
    if not 0.0 <= ltv <= 1.0:
        raise ValueError("acquisition_costs: ltv must be within 0-1")

    d_agency, d_legal = _split_fee_rate(fee_rate)
    agency_pct = d_agency if agency_fee_pct is None else float(agency_fee_pct)
    legal_pct = d_legal if legal_fee_pct is None else float(legal_fee_pct)

    mortgage_amount = round(price * ltv, 2)
    down_payment = round(price - mortgage_amount, 2)
    stamp_duty = round(get_avd(price), 2)
    agency_fee = round(price * agency_pct, 2)
    legal_fee = round(price * legal_pct, 2)
    optional = {
        "mortgage_fees": round(float(mortgage_fees or 0.0), 2),
        "valuation_fee": round(float(valuation_fee or 0.0), 2),
        "renovation_furnishing": round(float(renovation_furnishing or 0.0), 2),
        "initial_reserve": round(float(initial_reserve or 0.0), 2),
    }

    lines = {
        "down_payment": down_payment,
        "stamp_duty": stamp_duty,
        "agency_fee": agency_fee,
        "legal_fee": legal_fee,
        **optional,
    }
    transaction_costs = round(stamp_duty + agency_fee + legal_fee, 2)
    other_upfront = round(sum(optional.values()), 2)
    total = round(down_payment + transaction_costs + other_upfront, 2)

    # Legacy anchor view: down + AVD + combined fee_rate (agency+legal).
    core = round(down_payment + transaction_costs, 2)

    recommended_reserve = (round((monthly_housing_cost or 0.0) * C.RESERVE_MONTHS_RECOMMENDED, 2)
                           if monthly_housing_cost else None)

    return {
        "purchase_price": round(price, 2),
        "mortgage_amount": mortgage_amount,
        "down_payment": down_payment,
        "ltv": ltv,
        "stamp_duty": stamp_duty,
        "agency_fee": agency_fee,
        "legal_fee": legal_fee,
        "agency_fee_pct": round(agency_pct, 6),
        "legal_fee_pct": round(legal_pct, 6),
        **optional,
        "transaction_costs": transaction_costs,
        "other_upfront_costs": other_upfront,
        "total_initial_cash_required": total,
        "total_initial_cash_core": core,
        "total_pct_of_price": round(total / price, 6),
        "recommended_reserve": recommended_reserve,
        "reserve_months": C.RESERVE_MONTHS_RECOMMENDED if recommended_reserve else None,
        "total_with_recommended_reserve": (round(total + recommended_reserve, 2)
                                           if recommended_reserve else total),
        "mortgage_insurance_estimate": mortgage_insurance_estimate(price, ltv),
        "price_psf": round(price / area_sqft, 2) if area_sqft else None,
        "lines": [
            {"key": k,
             "label_zh": LINE_LABELS[k][0],
             "label_en": LINE_LABELS[k][1],
             "amount": v,
             "pct_of_price": round(v / price, 6),
             "provenance": (C.MODEL if k in ("down_payment", "stamp_duty")
                            else (C.USER if k in OPTIONAL_KEYS else C.ASSUMED))}
            for k, v in lines.items()
        ],
        "note": ("Down payment and Total Initial Cash Required are different "
                 "numbers. Mortgage insurance / bank fees / renovation and any "
                 "reserve are only included when explicitly provided."),
    }


def max_price_for_cash(available_cash: float, ltv: float,
                       agency_fee_pct: float | None = None,
                       legal_fee_pct: float | None = None,
                       extra_upfront: float = 0.0,
                       fee_rate: float | None = None) -> float | None:
    """Largest purchase price whose TOTAL initial cash fits ``available_cash``.

    Bisection because the AVD scale is piecewise with marginal-relief bands.
    """
    if available_cash is None or available_cash <= 0:
        return None
    if not 0.0 <= ltv < 1.0:
        return None

    def cash_for(price: float) -> float:
        acq = acquisition_costs(price, ltv=ltv, fee_rate=fee_rate,
                                agency_fee_pct=agency_fee_pct,
                                legal_fee_pct=legal_fee_pct)
        return acq["total_initial_cash_core"] + float(extra_upfront or 0.0)

    lo, hi = 0.0, max(1_000_000.0, available_cash * 20.0)
    if cash_for(hi) <= available_cash:
        return round(hi, 0)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if cash_for(mid) <= available_cash:
            lo = mid
        else:
            hi = mid
    return round(lo, 0)
