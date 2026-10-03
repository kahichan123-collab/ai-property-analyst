# -*- coding: utf-8 -*-
"""tools/boundary.py — transaction boundary engine (spec #17).

Instead of "is this worth buying?", this module answers "under what conditions
is it worth buying?" — every number derived deterministically:

    Maximum Buy Price        largest price that still satisfies every constraint
    Target Rent              lowest rent that clears DSCR + yield requirements
    Maximum Comfortable LTV  largest leverage that keeps stress DSR acceptable
    Interest-rate Ceiling    rate at which DSR hits the bank cap
    Minimum Rental Yield     net yield required (rate + spread, with a floor)

Plus a downside boundary: equity cushion, break-even vacancy and break-even rate.

All targets come from tools/config.py BOUNDARY_TARGETS. No LLM.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C
from tools import underwriting as uw
from tools.acquisition import acquisition_costs, max_price_for_cash


def _min_constraint(items: list[dict]) -> tuple[float | None, dict | None]:
    usable = [i for i in items if i.get("value") is not None]
    if not usable:
        return None, None
    best = min(usable, key=lambda i: i["value"])
    return best["value"], best


def _bisect(fn, lo: float, hi: float, target_zero_above: bool = True,
            iters: int = 120) -> float | None:
    """Find x where fn(x) crosses 0. fn must be monotone (checked loosely)."""
    try:
        f_lo, f_hi = fn(lo), fn(hi)
    except Exception:
        return None
    if f_lo is None or f_hi is None:
        return None
    if f_lo < 0 and f_hi < 0:
        return None
    if f_lo > 0 and f_hi > 0:
        return None
    a, b = lo, hi
    for _ in range(iters):
        m = (a + b) / 2.0
        v = fn(m)
        if v is None:
            return None
        if (v < 0) == (f_lo < 0):
            a = m
        else:
            b = m
    return round((a + b) / 2.0, 6)


def compute(purpose: str, resolved: dict, payload: dict, uw_out: dict | None,
            valuation: dict | None, acquisition: dict | None) -> dict:
    T = C.BOUNDARY_TARGETS
    fin = payload.get("financing") or {}
    pf = payload.get("finance") or {}
    rental = payload.get("rental") or {}
    uw_out = uw_out or {}
    valuation = valuation or {}

    price = resolved.get("asking_price")
    area = resolved.get("usable_area_sqft")
    ltv = fin.get("ltv")
    rate = fin.get("mortgage_rate")
    tenure = fin.get("tenure_years")
    amort = fin.get("amortization", "PI")
    income = (pf.get("monthly_net_income") or 0) + (pf.get("other_recurring_income") or 0)
    debt = pf.get("existing_monthly_debt") or 0.0
    mgmt = (payload.get("property") or {}).get("monthly_mgmt_rates") or 0.0
    # Fallbacks come from config, NOT literals: maximum_buy_price is the tightest
    # constraint and the verdict's PASS trigger, so if it defaulted differently
    # from orchestrator/underwriting a changed policy number would move the
    # underwriting and the boundary apart.
    fee_rate = fin.get("fee_rate", C.DEFAULT_FEE_RATE)
    disc = fin.get("discount_rate", C.DEFAULT_DISCOUNT_RATE)

    out: dict = {"asking_price": price, "basis": {}, "notes": []}

    # ------------------------------------------------------------------ #
    #  1. Maximum buy price constraints                                   #
    # ------------------------------------------------------------------ #
    price_constraints: list[dict] = []

    if income and price and ltv and rate is not None:
        v = uw.price_for_dsr(income, debt, T["max_base_dsr"], ltv, rate, tenure, amort)
        price_constraints.append({
            "key": "base_dsr", "value": v,
            "label": f"基準 DSR ≤ {T['max_base_dsr']:.0%}",
            "basis": f"月供 ≤ 入息 {T['max_base_dsr']:.0%} − 現有債務"})
        v2 = uw.price_for_dsr(income, debt, T["max_stress_dsr"], ltv,
                              rate + max(C.RATE_STRESS_STEPS), tenure, amort)
        price_constraints.append({
            "key": "stress_dsr", "value": v2,
            "label": f"壓力 DSR ≤ {T['max_stress_dsr']:.0%}（利率 +2.00pp）",
            "basis": f"利率 {rate + 0.02:.3%} 下月供 ≤ 入息 {T['max_stress_dsr']:.0%}"})
        mcp = pf.get("max_comfortable_monthly_payment")
        if mcp:
            allowed = max(0.0, mcp - mgmt)
            loan = uw.loan_from_payment(allowed, rate, tenure, amort)
            price_constraints.append({
                "key": "comfortable_payment", "value": round(loan / ltv, 0) if loan else None,
                "label": "自訂舒適月供上限",
                "basis": f"每月供款 ≤ HK${allowed:,.0f}（已扣管理費）"})

    spendable = pf.get("available_cash_for_transaction")
    if spendable is None:
        spendable = pf.get("liquid_cash")
    if spendable:
        reserve = pf.get("emergency_reserve") or 0.0
        budget = max(0.0, spendable - reserve)
        v = max_price_for_cash(budget, ltv, fee_rate=fee_rate,
                               extra_upfront=(acquisition or {}).get("other_upfront_costs", 0.0))
        price_constraints.append({
            "key": "available_cash", "value": v,
            "label": f"可用現金（保留應急儲備 HK${reserve:,.0f}）",
            "basis": f"首期＋稅費＋其他前期支出 ≤ HK${budget:,.0f}"})

    if valuation.get("buy_target_price"):
        price_constraints.append({
            "key": "fair_value", "value": valuation["buy_target_price"],
            "label": f"公允價值 −{C.FAIR_VALUE_DISCOUNT_BUFFER:.0%} 安全邊際",
            "basis": "可比證據推算之公允價值 × 折讓緩衝"})

    # rental-specific price ceilings
    if purpose == "rental":
        noi = uw_out.get("noi_pre_tax_y1")
        noi_after = uw_out.get("noi_after_tax_y1")
        rent = rental.get("monthly_rent") or 0
        if noi:
            v = uw.break_even_price(noi, ltv, rate, tenure, amort, T["target_dscr"])
            price_constraints.append({
                "key": "dscr_target", "value": v,
                "label": f"DSCR ≥ {T['target_dscr']:.2f}x",
                "basis": "稅前 NOI ÷ 年度供款"})
            v = uw.break_even_price(noi, ltv, rate + 0.02, tenure, amort, T["min_dscr"])
            price_constraints.append({
                "key": "stress_dscr", "value": v,
                "label": f"利率 +2.00pp 後 DSCR ≥ {T['min_dscr']:.2f}x",
                "basis": "壓力利率下租金仍須覆蓋供款"})
            y_req = max(T["min_net_yield_floor"], (rate or 0) + T["yield_spread_over_rate"])
            aft_coeff = uw._noi_coefficient(rental.get("vacancy", 0.05),
                                            rental.get("rates_pct", 0.06),
                                            rental.get("opex_pct", 0.08))
            aft_coeff = aft_coeff - 0.12 * max(0.0, (1 - rental.get("vacancy", 0.05))
                                               - rental.get("rates_pct", 0.06))
            capex = rental.get("annual_capex", 0) or 0.0
            v = (round((noi_after - capex) / y_req, 0)
                 if (noi_after is not None and y_req > 0 and aft_coeff > 0) else None)
            price_constraints.append({
                "key": "net_yield", "value": v,
                "label": f"淨租金回報 ≥ {y_req:.2%}",
                "basis": "（稅後 NOI − 維修預備）÷ 買入價"})
            # annual cash flow >= 0
            if noi_after is not None:
                allowed_annual = noi_after - capex
                loan = uw.loan_from_payment(allowed_annual / 12.0, rate, tenure, amort)
                price_constraints.append({
                    "key": "positive_cash_flow", "value": round(loan / ltv, 0) if loan else None,
                    "label": "年度現金流 ≥ 0",
                    "basis": "稅後 NOI − 維修預備 ≥ 年度供款"})

    max_price, binding = _min_constraint(price_constraints)
    out["maximum_buy_price"] = {
        "value": max_price,
        "binding_constraint": binding["key"] if binding else None,
        "binding_label": binding["label"] if binding else None,
        "constraints": price_constraints,
        "discount_to_asking_pct": (round(max_price / price - 1, 6)
                                   if (max_price and price) else None),
        "provenance": C.MODEL,
    }

    # ------------------------------------------------------------------ #
    #  2. Target rent                                                     #
    # ------------------------------------------------------------------ #
    rent_constraints: list[dict] = []
    if purpose == "rental" and price and ltv and rate is not None:
        be = uw.break_even_rent(price, ltv, rate, tenure, amort,
                                rental.get("vacancy", 0.05), rental.get("rates_pct", 0.06),
                                rental.get("opex_pct", 0.08), T["target_dscr"], area)
        if be:
            rent_constraints.append({
                "key": "dscr_target", "value": be["monthly_rent"],
                "label": f"DSCR ≥ {T['target_dscr']:.2f}x",
                "basis": "讓稅前 NOI 覆蓋 1.25 倍年度供款"})
        y_req = max(T["min_net_yield_floor"], (rate or 0) + T["yield_spread_over_rate"])
        vac = rental.get("vacancy", 0.05)
        rp = rental.get("rates_pct", 0.06)
        op = rental.get("opex_pct", 0.08)
        coeff = uw._noi_coefficient(vac, rp, op)
        aft = coeff - 0.12 * max(0.0, (1 - vac) - rp)
        capex = rental.get("annual_capex", 0) or 0.0
        if aft > 0:
            pgi_needed = (y_req * price + capex) / aft
            rent_constraints.append({
                "key": "net_yield", "value": round(pgi_needed / 12.0, 0),
                "label": f"淨租金回報 ≥ {y_req:.2%}",
                "basis": "（稅後 NOI − 維修預備）÷ 買入價"})
        cf = uw_out.get("annual_cash_flow_y1")
        if cf is not None and cf < 0 and aft > 0:
            shortfall = -cf
            extra_pgi = shortfall / aft
            current = rental.get("monthly_rent") or 0
            rent_constraints.append({
                "key": "positive_cash_flow", "value": round(current + extra_pgi / 12.0, 0),
                "label": "年度現金流 ≥ 0",
                "basis": f"補足現時每年短缺 HK${shortfall:,.0f}"})
    if rent_constraints:
        min_rent = max(i["value"] for i in rent_constraints if i.get("value") is not None)
        rent_binding = max((i for i in rent_constraints if i.get("value") is not None),
                           key=lambda i: i["value"])
    else:
        min_rent, rent_binding = None, None
    out["target_rent"] = {
        "value": min_rent,
        "binding_constraint": rent_binding["key"] if rent_binding else None,
        "binding_label": rent_binding["label"] if rent_binding else None,
        "constraints": rent_constraints,
        "current_rent": rental.get("monthly_rent"),
        "gap_pct": (round(min_rent / rental["monthly_rent"] - 1, 6)
                    if (min_rent and rental.get("monthly_rent")) else None),
        "provenance": C.MODEL,
    }

    # ------------------------------------------------------------------ #
    #  3. Maximum comfortable LTV                                         #
    # ------------------------------------------------------------------ #
    ltv_items = []
    if income and price:
        allowed = income * T["max_base_dsr"] - debt
        loan = uw.loan_from_payment(allowed, rate, tenure, amort)
        if loan:
            ltv_items.append({"key": "base_dsr", "value": loan / price,
                              "label": f"基準 DSR ≤ {T['max_base_dsr']:.0%}",
                              "basis": "以現價計的最高貸款額 ÷ 現價"})
        allowed_s = income * T["max_stress_dsr"] - debt
        loan_s = uw.loan_from_payment(allowed_s, rate + 0.02, tenure, amort)
        if loan_s:
            ltv_items.append({"key": "stress_dsr", "value": loan_s / price,
                              "label": f"壓力 DSR ≤ {T['max_stress_dsr']:.0%}",
                              "basis": "利率 +2.00pp 下的最高貸款額 ÷ 現價"})
        ltv_items.append({"key": "policy_cap", "value": T["comfortable_ltv_cap"],
                          "label": f"政策上限 {T['comfortable_ltv_cap']:.0%}",
                          "basis": "本模型設定的槓桿上限（按保／銀行慣例參考）"})
    ltv_val, ltv_binding = _min_constraint(ltv_items)
    if ltv_val is not None:
        ltv_val = round(min(1.0, max(0.0, ltv_val)), 4)
    out["maximum_comfortable_ltv"] = {
        "value": ltv_val,
        "binding_constraint": ltv_binding["key"] if ltv_binding else None,
        "binding_label": ltv_binding["label"] if ltv_binding else None,
        "constraints": ltv_items,
        "current_ltv": ltv,
        "provenance": C.MODEL,
    }

    # ------------------------------------------------------------------ #
    #  4. Interest-rate ceiling                                           #
    # ------------------------------------------------------------------ #
    ceiling = uw.rate_for_dsr_target(price, ltv, tenure, income, debt, C.BANK_DSR_CAP, amort) \
        if (price and ltv and income) else None
    out["interest_rate_ceiling"] = {
        "value": ceiling,
        "target_dsr": C.BANK_DSR_CAP,
        "label": f"DSR 觸及 {C.BANK_DSR_CAP:.0%} 的利率水平",
        "basis": "固定現價、貸款額與入息下的利率上限（deterministic bisection）",
        "current_rate": rate,
        "headroom_pp": (round((ceiling - rate) * 100, 3)
                        if (ceiling is not None and rate is not None) else None),
        "framing": C.RATE_STRESS_FRAMING,
        "provenance": C.MODEL,
    }

    # ------------------------------------------------------------------ #
    #  5. Minimum rental yield                                            #
    # ------------------------------------------------------------------ #
    y_floor = T["min_net_yield_floor"]
    y_spread = (rate or 0) + T["yield_spread_over_rate"]
    out["minimum_rental_yield"] = {
        "required": round(max(y_floor, y_spread), 6),
        "components": {"absolute_floor": y_floor,
                       "mortgage_rate_plus_spread": round(y_spread, 6),
                       "spread": T["yield_spread_over_rate"]},
        "achieved": uw_out.get("net_yield") if purpose == "rental" else None,
        "meets_requirement": ((uw_out.get("net_yield") or 0) >= max(y_floor, y_spread)
                              if purpose == "rental" else None),
        "basis": "淨租金回報須高於按揭利率 + 1.00pp（正利差），且不低於 2.00% 絕對下限",
        "provenance": C.MODEL,
    }

    # ------------------------------------------------------------------ #
    #  6. Downside boundary                                               #
    # ------------------------------------------------------------------ #
    downside = {
        "equity_cushion_pct": round(1 - ltv, 6) if ltv is not None else None,
        "equity_cushion_note": "樓價下跌超過此幅度即進入負資產（購入時點）",
    }
    if purpose == "rental" and rental.get("monthly_rent"):
        def cf_at_vacancy(v):
            r = uw.rental_underwriting(
                price=price, area_sqft=area, monthly_rent=rental["monthly_rent"],
                vacancy=v, rates_pct=rental.get("rates_pct", 0.06),
                opex_pct=rental.get("opex_pct", 0.08),
                annual_capex=rental.get("annual_capex", 0),
                ltv=ltv, annual_rate=rate, tenure_years=tenure, amort=amort,
                rent_growth=rental.get("rent_growth", 0.03),
                hold_years=rental.get("hold_years", 5),
                exit_cap=rental.get("exit_cap", 0.035),
                sell_cost=rental.get("sell_cost", 0.01), fee_rate=fee_rate, disc_rate=disc)
            return r["annual_cash_flow_y1"]
        downside["break_even_vacancy"] = _bisect(cf_at_vacancy, 0.0, 0.95)
        downside["current_vacancy"] = rental.get("vacancy", 0.05)

        def cf_at_rate(r_):
            r = uw.rental_underwriting(
                price=price, area_sqft=area, monthly_rent=rental["monthly_rent"],
                vacancy=rental.get("vacancy", 0.05), rates_pct=rental.get("rates_pct", 0.06),
                opex_pct=rental.get("opex_pct", 0.08),
                annual_capex=rental.get("annual_capex", 0),
                ltv=ltv, annual_rate=r_, tenure_years=tenure, amort=amort,
                rent_growth=rental.get("rent_growth", 0.03),
                hold_years=rental.get("hold_years", 5),
                exit_cap=rental.get("exit_cap", 0.035),
                sell_cost=rental.get("sell_cost", 0.01), fee_rate=fee_rate, disc_rate=disc)
            return r["annual_cash_flow_y1"]
        downside["break_even_rate"] = _bisect(cf_at_rate, 0.001, 0.40)
        downside["note"] = "現金流歸零的臨界空置率與臨界利率（deterministic bisection）"
    elif purpose == "owner_occupier" and income:
        def surplus_at_rate(r_):
            r = uw.owner_underwriting(
                price=price, ltv=ltv, annual_rate=r_, tenure_years=tenure, amort=amort,
                monthly_net_income=pf.get("monthly_net_income"),
                existing_monthly_debt=debt, monthly_mgmt_rates=mgmt,
                fee_rate=fee_rate, area_sqft=area,
                other_recurring_income=pf.get("other_recurring_income", 0.0))
            return r["monthly_surplus"]
        downside["break_even_rate"] = _bisect(surplus_at_rate, 0.001, 0.40)
        downside["note"] = "每月盈餘歸零的臨界利率（deterministic bisection）"
    out["downside_boundary"] = downside

    out["note"] = ("Maximum Buy Price is the tightest of the listed constraints, "
                   "all derived deterministically from the user's own inputs and "
                   "the config policy targets.")
    out["provenance"] = C.MODEL
    return out
