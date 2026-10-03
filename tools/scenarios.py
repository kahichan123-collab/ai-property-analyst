# -*- coding: utf-8 -*-
"""tools/scenarios.py — scenario engine (spec #11) + rate stress (spec #12).

All shocks come from tools/config.py. A request may NOT inject its own scenario
assumptions, and nothing here is calculated by the LLM.

Rental purpose : shocks rent / vacancy / growth / rate / exit cap and re-runs the
                 deterministic rental model (yield, NOI, cash flow, IRR, ...).
Owner purpose  : shocks rate / expenses and adds an *illustrative* exit using the
                 explicit market-value growth assumption per scenario (labelled
                 Assumption in the report — an owner-occupied flat produces no
                 rent, so IRR necessarily needs a sale assumption).

Rate stress (spec #12) is presented as "Mortgage Affordability Stress Test /
Interest Rate Sensitivity", never as a bank approval rule.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C
from tools import underwriting as uw


def _rental_kwargs(inputs: dict, shocks: dict) -> dict:
    r = dict(inputs.get("rental") or {})
    rate = inputs["rate"] + shocks["rate_add"]
    r["monthly_rent"] = (r.get("monthly_rent") or 0.0) * shocks["rent_mult"]
    r["vacancy"] = C.clamp((r.get("vacancy") or 0.0) + shocks["vacancy_add"], 0.0, 0.90)
    r["rent_growth"] = (r.get("rent_growth") or 0.0) * shocks["rent_growth_mult"]
    r["opex_pct"] = C.clamp((r.get("opex_pct") or 0.0) * shocks["opex_mult"], 0.0, 1.0)
    r["exit_cap"] = max(0.005, (r.get("exit_cap") or 0.035) * shocks["exit_cap_mult"])
    return {
        "price": inputs["price"], "area_sqft": inputs.get("area"),
        "monthly_rent": r["monthly_rent"], "vacancy": r["vacancy"],
        "rates_pct": r.get("rates_pct", 0.06), "opex_pct": r["opex_pct"],
        "annual_capex": r.get("annual_capex", 10000.0),
        "ltv": inputs["ltv"], "annual_rate": rate,
        "tenure_years": inputs["tenure"], "amort": inputs.get("amort", "PI"),
        "rent_growth": r["rent_growth"], "hold_years": r.get("hold_years", 5),
        "exit_cap": r["exit_cap"], "sell_cost": r.get("sell_cost", 0.01),
        "fee_rate": inputs.get("fee_rate", C.DEFAULT_FEE_RATE),
        "disc_rate": inputs.get("disc_rate", C.DEFAULT_DISCOUNT_RATE),
        "mortgage_fees": inputs.get("mortgage_fees", 0.0),
        "valuation_fee": inputs.get("valuation_fee", 0.0),
        "renovation_furnishing": inputs.get("renovation_furnishing", 0.0),
        "initial_reserve": inputs.get("initial_reserve", 0.0),
    }


def _rental_scenario(inputs: dict, shocks: dict) -> dict:
    kw = _rental_kwargs(inputs, shocks)
    r = uw.rental_underwriting(**kw)
    return {
        "label": shocks["label"], "label_zh": shocks["label_zh"],
        "description": shocks["description"],
        "rent_monthly": round(kw["monthly_rent"], 0),
        "vacancy": round(kw["vacancy"], 4),
        "rent_growth": round(kw["rent_growth"], 4),
        "interest_rate": round(kw["annual_rate"], 6),
        "exit_cap": round(kw["exit_cap"], 6),
        "property_value": r["exit_value"],
        "property_value_basis": "income approach: NOI(exit) / exit cap rate",
        "mortgage_payment": r["monthly_payment"],
        "annual_debt_service": r["annual_debt_service"],
        "noi_pre_tax": r["noi_pre_tax_y1"],
        "noi_after_tax": r["noi_after_tax_y1"],
        "annual_cash_flow": r["annual_cash_flow_y1"],
        "monthly_cash_flow": r["monthly_cash_flow_y1"],
        "gross_yield": r["gross_yield"],
        "net_yield": r["net_yield"],
        "cash_on_cash": r["cash_on_cash"],
        "dscr": r["min_dscr"],
        "irr": r["levered_irr"],
        "npv": r["levered_npv"],
        "equity_multiple": r["equity_multiple"],
        "exit_proceeds_net": r["exit_proceeds_net"],
        "initial_cash_required": r["initial_cash_required"],
    }


def _owner_scenario(inputs: dict, shocks: dict) -> dict:
    rate = inputs["rate"] + shocks["rate_add"]
    mgmt = (inputs.get("mgmt") or 0.0) * shocks["opex_mult"]
    kw = {
        "price": inputs["price"], "ltv": inputs["ltv"], "annual_rate": rate,
        "tenure_years": inputs["tenure"], "amort": inputs.get("amort", "PI"),
        "monthly_net_income": inputs.get("income"),
        "existing_monthly_debt": inputs.get("debt", 0.0),
        "monthly_mgmt_rates": mgmt,
        "fee_rate": inputs.get("fee_rate", C.DEFAULT_FEE_RATE), "area_sqft": inputs.get("area"),
        "other_recurring_income": inputs.get("other_income", 0.0),
        "mortgage_fees": inputs.get("mortgage_fees", 0.0),
        "valuation_fee": inputs.get("valuation_fee", 0.0),
        "renovation_furnishing": inputs.get("renovation_furnishing", 0.0),
        "initial_reserve": inputs.get("initial_reserve", 0.0),
    }
    r = uw.owner_underwriting(**kw)

    hold = int(inputs.get("owner_hold_years") or C.OWNER_HOLD_YEARS_DEFAULT)
    sell_cost = (inputs.get("rental") or {}).get("sell_cost", 0.01)
    exit_value = inputs["price"] * (1 + shocks["value_growth_annual"]) ** hold
    remaining = uw._remaining_balance(r["loan_amount"], rate, inputs["tenure"], hold,
                                      inputs.get("amort", "PI"))
    net_proceeds = exit_value * (1 - sell_cost) - remaining
    equity = r["total_initial_cash_required"]
    flows = [-equity] + [-r["annual_housing_cost"]] * hold
    flows[-1] += net_proceeds
    return {
        "label": shocks["label"], "label_zh": shocks["label_zh"],
        "description": shocks["description"],
        "rent_monthly": None,
        "vacancy": None, "rent_growth": None,
        "interest_rate": round(rate, 6),
        "exit_cap": None,
        "property_value": round(exit_value, 2),
        "property_value_basis": (f"market approach: price x (1 {shocks['value_growth_annual']:+.1%})"
                                 f"^{hold} — Assumption"),
        "mortgage_payment": r["monthly_payment"],
        "annual_debt_service": round(r["monthly_payment"] * 12.0, 2),
        "noi_pre_tax": None, "noi_after_tax": None,
        "annual_cash_flow": r["monthly_surplus"],
        "monthly_cash_flow": r["monthly_surplus"] / 12.0 if r["monthly_surplus"] is not None else None,
        "gross_yield": None, "net_yield": None, "cash_on_cash": None, "dscr": None,
        "dsr": r["dsr"],
        "monthly_housing_cost": r["monthly_housing_cost"],
        "monthly_surplus": r["monthly_surplus"],
        "irr": round(uw.irr(flows), 6),
        "npv": round(uw.npv(inputs.get("disc_rate", C.DEFAULT_DISCOUNT_RATE), flows), 2),
        "equity_multiple": round(net_proceeds / equity, 4) if equity > 0 else None,
        "exit_proceeds_net": round(net_proceeds, 2),
        "initial_cash_required": r["total_initial_cash_required"],
        "cash_flows": [round(f, 2) for f in flows],
        "hold_years": hold,
    }


def run_scenarios(purpose: str, inputs: dict) -> dict:
    """BEAR / BASE / BULL for the given purpose. Deterministic, config-driven."""
    out = {}
    for name in C.SCENARIO_ORDER:
        shocks = C.scenario_config(name)
        out[name] = (_rental_scenario(inputs, shocks) if purpose == "rental"
                     else _owner_scenario(inputs, shocks))
    base = out.get("base") or {}
    for name, row in out.items():
        if name == "base":
            continue
        if base.get("irr") is not None and row.get("irr") is not None:
            row["irr_delta"] = round(row["irr"] - base["irr"], 6)
        if base.get("annual_cash_flow") is not None and row.get("annual_cash_flow") is not None:
            row["annual_cash_flow_delta"] = round(
                row["annual_cash_flow"] - base["annual_cash_flow"], 2)
        if base.get("property_value") and row.get("property_value"):
            row["property_value_delta_pct"] = round(
                row["property_value"] / base["property_value"] - 1, 6)
    return {
        "scenarios": out,
        "order": C.SCENARIO_ORDER,
        "config_source": "tools/config.py SCENARIOS (server-side; not user editable)",
        "assumptions": [
            "BEAR: rent -10%, vacancy +5pp, growth x0.5, rate +1.00pp, exit cap x1.15",
            "BASE: user assumptions unchanged",
            "BULL: rent +8%, vacancy -2pp, growth x1.50, rate -0.25pp, exit cap x0.92",
        ],
        "notes": ["All scenario arithmetic is deterministic (tools/underwriting.py).",
                  "Owner-occupier exit value uses an explicit market-growth "
                  "Assumption because an owner-occupied flat has no rent roll."],
    }


def run_rate_stress(purpose: str, inputs: dict) -> dict:
    """Mortgage affordability stress / interest-rate sensitivity (spec #12)."""
    kind = "rental" if purpose == "rental" else "owner"
    if kind == "rental":
        kw = _rental_kwargs(inputs, C.scenario_config("base"))
    else:
        kw = {
            "price": inputs["price"], "ltv": inputs["ltv"], "annual_rate": inputs["rate"],
            "tenure_years": inputs["tenure"], "amort": inputs.get("amort", "PI"),
            "monthly_net_income": inputs.get("income"),
            "existing_monthly_debt": inputs.get("debt", 0.0),
            "monthly_mgmt_rates": inputs.get("mgmt") or 0.0,
            "fee_rate": inputs.get("fee_rate", C.DEFAULT_FEE_RATE), "area_sqft": inputs.get("area"),
            "other_recurring_income": inputs.get("other_income", 0.0),
        }
    rows = uw.rate_sensitivity(kind, kw)
    base = rows[0]
    stressed = rows[-1]
    payment_delta = None
    if base.get("monthly_payment") and stressed.get("monthly_payment"):
        payment_delta = round(stressed["monthly_payment"] - base["monthly_payment"], 2)
    return {
        "kind": kind,
        "framing": C.RATE_STRESS_FRAMING,
        "base_rate": base["rate"],
        "steps": C.RATE_STRESS_STEPS,
        "rows": rows,
        "base_affordability": {
            "rate": base["rate"],
            "monthly_payment": base.get("monthly_payment"),
            "dsr": base.get("dsr"),
            "monthly_surplus": base.get("monthly_surplus"),
            "annual_cash_flow": base.get("annual_cash_flow"),
            "min_dscr": base.get("min_dscr"),
            "band": C.band_label("dsr", base.get("dsr")) if kind == "owner" else None,
        },
        "stress_affordability": {
            "rate": stressed["rate"],
            "monthly_payment": stressed.get("monthly_payment"),
            "dsr": stressed.get("dsr"),
            "monthly_surplus": stressed.get("monthly_surplus"),
            "annual_cash_flow": stressed.get("annual_cash_flow"),
            "min_dscr": stressed.get("min_dscr"),
            "band": C.band_label("stress_dsr", stressed.get("dsr")) if kind == "owner" else None,
        },
        "payment_increase": payment_delta,
        "summary": ("利率 +2.00pp 後月供增加 HK$%s（%.1f%%）。"
                    % (f"{payment_delta:,.0f}" if payment_delta is not None else "—",
                       (payment_delta / base["monthly_payment"] * 100)
                       if (payment_delta is not None and base.get("monthly_payment")) else 0.0)),
    }
