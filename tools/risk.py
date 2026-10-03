# -*- coding: utf-8 -*-
"""tools/risk.py — risk engine (spec #16).

Automatically detects the risk list required by the spec, scores each item, and
emits an overall Risk Level (Low / Medium / High / Very High) plus the Top 3
risks in the required Risk -> Mechanism -> Financial Impact -> Mitigation form.

Every detection is a deterministic threshold test from tools/config.py
RISK_THRESHOLDS. Nothing is LLM-generated; the narrative layer may only explain
what this module found.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C

SEVERITY = {"low": 0, "medium": 1, "high": 2, "very_high": 3}
SEVERITY_INV = {v: k for k, v in SEVERITY.items()}

# deterministic ordering used to break ties when selecting the Top 3
PRIORITY = ["liquidity_risk", "negative_rental_cash_flow", "high_dsr_stress",
            "weak_income_coverage", "high_ltv", "high_asking_premium",
            "low_rental_yield", "high_interest_rate_sensitivity",
            "exit_assumption_risk", "old_building_risk", "maintenance_exposure",
            "concentration_risk"]


def _item(key: str, level: str, evidence: str) -> dict:
    label, mechanism, impact, mitigation = C.RISK_CATALOG[key]
    return {"id": key, "label": label, "level": level,
            "severity": SEVERITY[level], "mechanism": mechanism,
            "impact": impact, "mitigation": mitigation, "evidence": evidence,
            "provenance": C.MODEL}


def assess(purpose: str, resolved: dict, payload: dict, uw_out: dict | None,
           valuation: dict | None, rate_stress: dict | None,
           acquisition: dict | None, scenarios_out: dict | None) -> dict:
    T = C.RISK_THRESHOLDS
    fin = payload.get("financing") or {}
    pf = payload.get("finance") or {}
    rows: list[dict] = []
    checks: list[dict] = []

    def record(key, level, evidence, triggered=True):
        checks.append({"id": key, "level": level, "evidence": evidence,
                       "triggered": triggered})
        if triggered and SEVERITY[level] > 0:
            rows.append(_item(key, level, evidence))

    ltv = fin.get("ltv")
    price = resolved.get("asking_price") or 0
    area = resolved.get("usable_area_sqft")
    uw_out = uw_out or {}
    valuation = valuation or {}

    # ---- 1. leverage ----------------------------------------------------- #
    if ltv is not None:
        if ltv > T["ltv_very_high"]:
            record("high_ltv", "very_high", f"LTV {ltv:.2%} > {T['ltv_very_high']:.0%}")
        elif ltv > T["ltv_high"]:
            record("high_ltv", "high", f"LTV {ltv:.2%} > {T['ltv_high']:.0%}")
        else:
            record("high_ltv", "low", f"LTV {ltv:.2%}", triggered=False)

    # ---- 2. income coverage / DSR --------------------------------------- #
    if purpose == "owner_occupier":
        dsr = uw_out.get("dsr")
        if dsr is None:
            record("weak_income_coverage", "medium",
                   "入息未提供，無法評估供款佔比（DSR）")
        elif dsr > T["dsr_very_high"]:
            record("weak_income_coverage", "very_high", f"DSR {dsr:.1%}")
        elif dsr > T["dsr_high"]:
            record("weak_income_coverage", "high", f"DSR {dsr:.1%}")
        elif dsr > T["dsr_elevated"]:
            record("weak_income_coverage", "medium", f"DSR {dsr:.1%}")
        else:
            record("weak_income_coverage", "low", f"DSR {dsr:.1%}", triggered=False)
    else:
        dscr = uw_out.get("min_dscr")
        if dscr is not None:
            if dscr < 0.9:
                record("weak_income_coverage", "very_high", f"Min DSCR {dscr:.2f}x")
            elif dscr < T["dscr_weak"]:
                record("weak_income_coverage", "high", f"Min DSCR {dscr:.2f}x")
            elif dscr < T["dscr_target"]:
                record("weak_income_coverage", "medium", f"Min DSCR {dscr:.2f}x")
            else:
                record("weak_income_coverage", "low", f"Min DSCR {dscr:.2f}x", triggered=False)

    # ---- 3. stress DSR --------------------------------------------------- #
    if rate_stress and rate_stress.get("stress_affordability"):
        sdsr = rate_stress["stress_affordability"].get("dsr")
        if sdsr is not None:
            if sdsr > T["stress_dsr_very_high"]:
                record("high_dsr_stress", "very_high", f"利率 +2pp 後 DSR {sdsr:.1%}")
            elif sdsr > T["stress_dsr_high"]:
                record("high_dsr_stress", "high", f"利率 +2pp 後 DSR {sdsr:.1%}")
            elif sdsr > T["dsr_elevated"]:
                record("high_dsr_stress", "medium", f"利率 +2pp 後 DSR {sdsr:.1%}")
            else:
                record("high_dsr_stress", "low", f"利率 +2pp 後 DSR {sdsr:.1%}",
                       triggered=False)

    # ---- 4. rental cash flow -------------------------------------------- #
    if purpose == "rental":
        cf = uw_out.get("annual_cash_flow_y1")
        if cf is not None:
            if cf < 0:
                level = "very_high" if (uw_out.get("monthly_cash_flow_y1") or 0) < -3000 else "high"
                record("negative_rental_cash_flow", level, f"年度現金流 HK${cf:,.0f}")
            else:
                record("negative_rental_cash_flow", "low", f"年度現金流 HK${cf:,.0f}",
                       triggered=False)

    # ---- 5. interest-rate sensitivity ----------------------------------- #
    # A large percentage move in the payment is only a *risk* if the borrower
    # cannot absorb it: severity therefore combines the payment shock with the
    # resulting stress DSR (owner) or the stressed cash flow (rental).
    if rate_stress and rate_stress.get("rows"):
        last = rate_stress["rows"][-1]
        inc = last.get("payment_increase_pct")
        stress_dsr = (rate_stress.get("stress_affordability") or {}).get("dsr")
        stress_cf = last.get("annual_cash_flow")          # rental rows only
        stress_dscr = last.get("min_dscr")
        if inc is not None:
            if purpose == "owner_occupier":
                if inc > T["payment_increase_very_high"] and (stress_dsr or 0) > T["dsr_high"]:
                    record("high_interest_rate_sensitivity", "very_high",
                           f"利率 +2pp 月供 +{inc:.1%} 且壓力 DSR {(stress_dsr or 0):.1%}")
                elif inc > T["payment_increase_high"] and (stress_dsr or 0) > T["dsr_elevated"]:
                    record("high_interest_rate_sensitivity", "high",
                           f"利率 +2pp 月供 +{inc:.1%}，壓力 DSR {(stress_dsr or 0):.1%}")
                elif (stress_dsr or 0) > T["dsr_high"] or inc > 0.20:
                    record("high_interest_rate_sensitivity", "medium",
                           f"利率 +2pp 月供 +{inc:.1%}，壓力 DSR "
                           f"{('%.1f%%' % (stress_dsr * 100)) if stress_dsr is not None else '—'}")
                else:
                    record("high_interest_rate_sensitivity", "low",
                           f"利率 +2pp 月供 +{inc:.1%}，但壓力 DSR 仍偏低"
                           f"（{(stress_dsr or 0):.1%}）", triggered=False)
            else:
                if stress_cf is not None and stress_cf < 0 and inc > T["payment_increase_high"]:
                    record("high_interest_rate_sensitivity", "very_high",
                           f"利率 +2pp 月供 +{inc:.1%}，壓力現金流 HK${stress_cf:,.0f}")
                elif stress_cf is not None and stress_cf < 0:
                    record("high_interest_rate_sensitivity", "high",
                           f"利率 +2pp 後年度現金流 HK${stress_cf:,.0f}")
                elif (stress_dscr is not None and stress_dscr < T["dscr_target"]) \
                        or inc > T["payment_increase_high"]:
                    record("high_interest_rate_sensitivity", "medium",
                           f"利率 +2pp 月供 +{inc:.1%}，DSCR "
                           f"{('%.2fx' % stress_dscr) if stress_dscr is not None else '—'}")
                else:
                    record("high_interest_rate_sensitivity", "low",
                           f"利率 +2pp 月供 +{inc:.1%}", triggered=False)

    # ---- 6. rental yield ------------------------------------------------- #
    if purpose == "rental":
        ny = uw_out.get("net_yield")
        if ny is not None:
            if ny < T["net_yield_very_low"]:
                record("low_rental_yield", "very_high", f"淨租金回報 {ny:.2%}")
            elif ny < T["net_yield_low"]:
                record("low_rental_yield", "high", f"淨租金回報 {ny:.2%}")
            elif ny < (fin.get("mortgage_rate") or 0):
                record("low_rental_yield", "medium",
                       f"淨租金回報 {ny:.2%} 低於按揭利率 {fin.get('mortgage_rate', 0):.3%}")
            else:
                record("low_rental_yield", "low", f"淨租金回報 {ny:.2%}", triggered=False)

    # ---- 7. asking premium ---------------------------------------------- #
    prem = valuation.get("premium_pct")
    if prem is None:
        record("high_asking_premium", "medium",
               "無可比證據，無法判斷開價溢價（未捏造估值）")
    elif prem > T["premium_very_high"]:
        record("high_asking_premium", "very_high", f"開價高於公允價值 {prem:.1%}")
    elif prem > T["premium_high"]:
        record("high_asking_premium", "high", f"開價高於公允價值 {prem:.1%}")
    elif prem > 0:
        record("high_asking_premium", "medium", f"開價高於公允價值 {prem:.1%}")
    else:
        record("high_asking_premium", "low", f"開價相對公允價值 {prem:+.1%}", triggered=False)

    # ---- 8. building age ------------------------------------------------- #
    age = resolved.get("building_age")
    if age is not None:
        if age >= T["building_age_very_old"]:
            record("old_building_risk", "high", f"樓齡 {age:.0f} 年")
        elif age >= T["building_age_old"]:
            record("old_building_risk", "medium", f"樓齡 {age:.0f} 年")
        else:
            record("old_building_risk", "low", f"樓齡 {age:.0f} 年", triggered=False)
    else:
        record("old_building_risk", "medium", "樓齡未提供（unknown）", triggered=False)

    # ---- 9. maintenance exposure ---------------------------------------- #
    mgmt = (payload.get("property") or {}).get("monthly_mgmt_rates")
    rates_amt = (payload.get("property") or {}).get("rates_amount")
    monthly_out = (mgmt or 0) + (rates_amt or 0)
    if area and monthly_out:
        psf = monthly_out / area
        if psf > 2 * T["maintenance_psf_high"]:
            record("maintenance_exposure", "high", f"每月管理費＋差餉 HK${psf:.1f}/呎")
        elif psf > T["maintenance_psf_high"]:
            record("maintenance_exposure", "medium", f"每月管理費＋差餉 HK${psf:.1f}/呎")
        else:
            record("maintenance_exposure", "low", f"每月管理費＋差餉 HK${psf:.1f}/呎",
                   triggered=False)
    else:
        record("maintenance_exposure", "low", "未有足夠資料評估", triggered=False)

    # ---- 10. liquidity --------------------------------------------------- #
    cash_required = (acquisition or {}).get("total_initial_cash_required")
    available = pf.get("available_cash_for_transaction")
    liquid = pf.get("liquid_cash")
    spendable = available if available is not None else liquid
    if cash_required and spendable is not None:
        reserve = pf.get("emergency_reserve") or 0
        leftover = spendable - cash_required
        if leftover < 0:
            record("liquidity_risk", "very_high",
                   f"所需現金 HK${cash_required:,.0f} 超過可動用資金 HK${spendable:,.0f}")
        elif leftover < reserve:
            record("liquidity_risk", "high",
                   f"交易後剩餘 HK${leftover:,.0f} 低於應急儲備 HK${reserve:,.0f}")
        elif leftover < cash_required * 0.15:
            record("liquidity_risk", "medium",
                   f"交易後剩餘 HK${leftover:,.0f}（少於所需現金 15%）")
        else:
            record("liquidity_risk", "low", f"交易後剩餘 HK${leftover:,.0f}", triggered=False)
    else:
        record("liquidity_risk", "medium", "未提供可動用現金，無法評估流動性",
               triggered=False)

    # ---- 11. concentration ----------------------------------------------- #
    # proxy: the property's share of (property + spendable financial assets).
    # available-for-transaction cash is preferred over total liquid cash because
    # it is the amount actually earmarked for this purchase.
    if price and spendable:
        ratio = price / (price + spendable) if (price + spendable) > 0 else 1.0
        if ratio > T["liquidity_cash_ratio_very_high"]:
            record("concentration_risk", "high", f"物業佔流動資產 {ratio:.0%}")
        elif ratio > T["liquidity_cash_ratio_high"]:
            record("concentration_risk", "medium", f"物業佔流動資產 {ratio:.0%}")
        else:
            record("concentration_risk", "low", f"物業佔流動資產 {ratio:.0%}", triggered=False)
    else:
        record("concentration_risk", "medium", "未提供現金資料，無法評估集中度",
               triggered=False)

    # ---- 12. exit assumption --------------------------------------------- #
    if purpose == "rental":
        exit_cap = (payload.get("rental") or {}).get("exit_cap")
        going_in = uw_out.get("cap_rate_going_in")
        if exit_cap and going_in:
            if going_in - exit_cap > T["exit_cap_compression"]:
                record("exit_assumption_risk", "high",
                       f"退出 cap rate {exit_cap:.2%} 低於入市 cap rate {going_in:.2%}"
                       f"（隱含估值擴張 {(going_in - exit_cap) * 100:.2f}pp）")
            else:
                record("exit_assumption_risk", "low",
                       f"退出 cap rate {exit_cap:.2%} vs 入市 {going_in:.2%}", triggered=False)

    # ---- overall ---------------------------------------------------------- #
    overall_sev = max([SEVERITY[c["level"]] for c in checks] or [0])
    detected = sorted(rows, key=lambda r: (-r["severity"], PRIORITY.index(r["id"])
                                           if r["id"] in PRIORITY else 99))
    top3 = detected[:3]
    return {
        "level": SEVERITY_INV[overall_sev],
        "level_label": C.RISK_LEVEL_LABELS[SEVERITY_INV[overall_sev]],
        "score": overall_sev,
        "risks": detected,
        "top_risks": top3,
        "counts": {lvl: sum(1 for r in detected if r["level"] == lvl)
                   for lvl in C.RISK_LEVELS},
        "checks": checks,
        "checks_run": len(checks),
        "thresholds_source": "tools/config.py RISK_THRESHOLDS",
        "note": ("Risk detection is deterministic. Levels are threshold-based policy "
                 "settings, not statistical probabilities."),
    }
