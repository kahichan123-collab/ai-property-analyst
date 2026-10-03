# -*- coding: utf-8 -*-
"""tools/verdict.py — investment verdict engine (spec #18).

Fixed output vocabulary: BUY | NEGOTIATE | WATCH | PASS — plus the conditional
decision the product is really about:

    BUY IF   price <= X  AND rent >= Y AND stress DSR <= Z (AND net yield >= W)
    PASS IF  price > X   OR stress cash flow < 0 OR fair-value premium > P

Everything is decided from deterministic outputs already computed by the engine
(underwriting, valuation, boundary, rate stress, scenarios, risk). The LLM may
explain the verdict; it never computes it.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C


def _f(x, nd=0):
    if x is None:
        return None
    return round(x, nd)


def decide(purpose: str, uw_out: dict | None, valuation: dict | None,
           boundary: dict | None, risk: dict | None, rate_stress: dict | None,
           scenarios_out: dict | None, payload: dict, resolved: dict) -> dict:
    V = C.VERDICT_THRESHOLDS
    uw_out = uw_out or {}
    valuation = valuation or {}
    boundary = boundary or {}
    risk = risk or {}
    rate_stress = rate_stress or {}
    scenarios_out = scenarios_out or {}
    fin = payload.get("financing") or {}
    pf = payload.get("finance") or {}

    price = resolved.get("asking_price")
    max_buy = (boundary.get("maximum_buy_price") or {}).get("value")
    premium = valuation.get("premium_pct")
    valuation_class = valuation.get("classification")
    risk_level = risk.get("level", "medium")
    base = rate_stress.get("base_affordability") or {}
    stress = rate_stress.get("stress_affordability") or {}
    base_dsr = base.get("dsr")
    stress_dsr = stress.get("dsr")
    base_cf = base.get("annual_cash_flow")
    stress_cf = stress.get("annual_cash_flow")
    net_yield = uw_out.get("net_yield")
    min_yield = (boundary.get("minimum_rental_yield") or {}).get("required")
    dscr = uw_out.get("min_dscr")
    scenarios = scenarios_out.get("scenarios") or {}
    bear_irr = (scenarios.get("bear") or {}).get("irr")
    base_irr = (scenarios.get("base") or {}).get("irr")
    stress_irr = (scenarios.get("bear") or {}).get("irr")
    ceiling = (boundary.get("interest_rate_ceiling") or {}).get("value")
    current_rate = fin.get("mortgage_rate")
    target_rent = (boundary.get("target_rent") or {}).get("value")
    max_ltv = (boundary.get("maximum_comfortable_ltv") or {}).get("value")
    current_ltv = fin.get("ltv")

    reasons: list[str] = []
    blockers: list[dict] = []
    missing: list[str] = []      # inputs that make the FINANCIAL case unknown
    caveats: list[dict] = []     # disclosed limits on an otherwise usable verdict

    # ---------------------------------------------------------------- #
    #  PASS conditions                                                  #
    # ---------------------------------------------------------------- #
    if risk_level == "very_high":
        blockers.append({"code": "risk_very_high", "detail": "風險等級 Very High"})
    if premium is not None and premium > V["pass_if_premium_gt"]:
        blockers.append({"code": "premium_too_high",
                         "detail": f"開價高於公允價值 {premium:.1%}"})
    if max_buy and price and price > max_buy * (1 + V["pass_if_price_over_max_buy"]):
        blockers.append({"code": "above_max_buy",
                         "detail": f"叫價 HK${price:,.0f} 高於最大買入價 HK${max_buy:,.0f} 逾 "
                                   f"{V['pass_if_price_over_max_buy']:.0%}"})
    if stress_dsr is not None and stress_dsr > V["pass_if_stress_dsr_gt"]:
        blockers.append({"code": "stress_dsr",
                         "detail": f"利率 +2.00pp 後 DSR {stress_dsr:.1%}"})
    if purpose == "rental" and stress_cf is not None and stress_cf < 0:
        blockers.append({"code": "stress_cash_flow_negative",
                         "detail": f"壓力情境年度現金流 HK${stress_cf:,.0f}"})

    # ---------------------------------------------------------------- #
    #  BUY conditions                                                   #
    # ---------------------------------------------------------------- #
    buy_checks: list[dict] = []

    def chk(name, ok, detail):
        buy_checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    if income_ok := bool((pf.get("monthly_net_income") or 0) > 0):
        chk("income_available", True, "已提供入息")
    else:
        chk("income_available", False, "未提供每月淨收入 → 無法判斷可負擔性")
        missing.append("finance.monthly_net_income")

    chk("price_at_or_below_max_buy", (max_buy is not None and price is not None
                                      and price <= max_buy),
        f"叫價 HK${price:,.0f} vs 最大買入價 "
        f"{'HK$%s' % format(max_buy, ',.0f') if max_buy else '—'}")

    if purpose == "owner_occupier":
        chk("base_dsr_comfortable", (base_dsr is not None and base_dsr <= C.PERSONAL_DSR_COMFORT),
            f"基準 DSR {base_dsr:.1%}" if base_dsr is not None else "基準 DSR 未知")
        chk("stress_dsr_ok", (stress_dsr is not None and stress_dsr <= V["buy_if_stress_dsr_le"]),
            f"壓力 DSR {stress_dsr:.1%}" if stress_dsr is not None else "壓力 DSR 未知")
    else:
        chk("dscr_ok", (dscr is not None and dscr >= V["buy_if_dscr_ge"]),
            f"Min DSCR {dscr:.2f}x" if dscr is not None else "Min DSCR 未知")
        chk("positive_cash_flow", (base_cf is not None and base_cf > 0),
            f"年度現金流 HK${base_cf:,.0f}" if base_cf is not None else "現金流未知")
        chk("yield_meets_requirement", (net_yield is not None and min_yield is not None
                                        and net_yield >= min_yield),
            f"淨租金回報 {net_yield:.2%} vs 要求 {min_yield:.2%}"
            if (net_yield is not None and min_yield is not None) else "回報要求未知")

    if valuation_class == "insufficient_data":
        chk("valuation_available", False,
            "無可比證據 → 未能確認價格合理（不列入 BUY 門檻，見判決限制）")
    else:
        chk("valuation_available", True, valuation.get("classification_label", ""))

    chk("premium_acceptable", (premium is not None and premium <= V["buy_if_premium_le"]),
        f"溢價 {premium:+.1%}" if premium is not None
        else "溢價未知（不列入 BUY 門檻，見判決限制）")
    chk("risk_acceptable", risk_level in ("low", "medium"),
        f"風險等級 {C.RISK_LEVEL_LABELS.get(risk_level, risk_level)}")

    # Missing comparable market evidence is NOT the same as a missing financial
    # input. Affordability, the transaction boundary and the stress tests are all
    # complete without it, so it must not downgrade a verdict to WATCH on its own
    # (a buyer with overwhelming cash would be told "insufficient data" forever).
    # It is disclosed as a mandatory caveat instead, and it still blocks the
    # valuation_available / premium_acceptable BUY checks above.
    valuation_unverified = valuation_class == "insufficient_data"
    if valuation_unverified:
        caveats.append({
            "code": "valuation_unverified",
            "detail": ("未接入可比成交證據：公允價值屬 Insufficient Data，"
                       "本判決未考慮叫價是否高於市場水平。"
                       "接入可比成交（或由使用者提供）後才會反映價格判斷。"),
        })

    # The evidence-dependent checks stay in the published checklist (so the
    # reader can see they were attempted and what was missing) but they must not
    # veto a BUY when the only thing absent is the comparable evidence set —
    # that absence is already disclosed as a caveat above.
    evidence_dependent = {"valuation_available", "premium_acceptable"}
    gating_checks = [c for c in buy_checks
                     if not (valuation_unverified and c["check"] in evidence_dependent)]
    buy_ok = all(c["ok"] for c in gating_checks) and not blockers
    negotiate_ok = (not blockers and premium is not None
                    and premium <= V["negotiate_if_premium_le"]
                    and (not buy_ok))

    # ---------------------------------------------------------------- #
    #  Label                                                            #
    # ---------------------------------------------------------------- #
    if blockers:
        label, code = "PASS", 0
        why = "存在否決條件：" + "；".join(b["detail"] for b in blockers[:3]) + "。"
    elif missing:
        label, code = "WATCH", 1
        why = "資料不足，無法否定亦無法確認：" + "、".join(missing) + "。"
    elif buy_ok:
        label, code = "BUY", 3
        why = ("價格、現金流與壓力測試同時通過所有門檻"
               + ("（惟公允價值未經市場證據驗證）。" if valuation_unverified else "。"))
    elif negotiate_ok:
        label, code = "NEGOTIATE", 2
        why = ("基本財務條件可接受，但價格或回報未達 BUY 門檻，建議議價"
               + ("（且公允價值未經市場證據驗證）。" if valuation_unverified else "。"))
    else:
        label, code = "WATCH", 1
        why = "未觸發否決條件，但亦未通過全部買入門檻，建議觀察。"
        failed = [c for c in buy_checks if not c["ok"]]
        if failed:
            why += " 未通過：" + "；".join(c["detail"] for c in failed[:3]) + "。"

    # ---------------------------------------------------------------- #
    #  What would change the decision                                   #
    # ---------------------------------------------------------------- #
    changes: list[dict] = []
    if max_buy and price and price > max_buy:
        changes.append({"lever": "price", "detail":
                        f"價格下調至 HK${max_buy:,.0f} 或以下"
                        f"（需減 {abs(max_buy / price - 1):.1%}）"})
    if target_rent and purpose == "rental":
        cur = (payload.get("rental") or {}).get("monthly_rent")
        if cur and target_rent > cur:
            changes.append({"lever": "rent", "detail":
                            f"租金提升至 HK${target_rent:,.0f}/月或以上"
                            f"（需增 {target_rent / cur - 1:.1%}）"})
    if ceiling is not None and current_rate is not None and ceiling < current_rate + 0.02:
        changes.append({"lever": "rate", "detail":
                        f"按揭利率需降至 {ceiling:.3%} 以下（現為 {current_rate:.3%}）"})
    if max_ltv is not None and current_ltv is not None and max_ltv < current_ltv:
        changes.append({"lever": "ltv", "detail":
                        f"貸款成數需降至 {max_ltv:.0%} 或以下（現為 {current_ltv:.0%}）"})
    if valuation.get("fair_value") is None:
        changes.append({"lever": "evidence", "detail":
                        "接入可比成交資料以確認公允價值（目前為 Insufficient Data）"})
    if not pf.get("liquid_cash") and not pf.get("available_cash_for_transaction"):
        changes.append({"lever": "cash", "detail": "提供可動用現金以評估流動性與交易上限"})
    if premium is not None and premium > 0:
        changes.append({"lever": "premium", "detail":
                        f"議價至公允價值 HK${valuation.get('fair_value', 0):,.0f} 或以下"})

    # ---------------------------------------------------------------- #
    #  Conditional decision                                             #
    # ---------------------------------------------------------------- #
    conditional = {
        "buy_if": {
            "price_max": _f(max_buy),
            "rent_min": _f(target_rent),
            "stress_dsr_max": _f(V["buy_if_stress_dsr_le"], 4),
            "net_yield_min": _f(min_yield, 6),
            "min_dscr": _f(V["buy_if_dscr_ge"], 2),
            "risk_level_max": "medium",
            "expression_zh": (
                "當 " + " 且 ".join(filter(None, [
                    f"價格 ≤ HK${max_buy:,.0f}" if max_buy else None,
                    f"租金 ≥ HK${target_rent:,.0f}/月" if target_rent else None,
                    f"壓力 DSR ≤ {V['buy_if_stress_dsr_le']:.0%}" if base_dsr is not None else None,
                    f"淨租金回報 ≥ {min_yield:.2%}" if (min_yield and purpose == "rental") else None,
                    "風險等級 ≤ Medium",
                ])) + " 時，可考慮買入。"),
        },
        "pass_if": {
            "price_above": _f(max_buy),
            "stress_cash_flow_below": 0,
            "fair_value_premium_above": _f(V["pass_if_premium_gt"], 4),
            "stress_dsr_above": _f(V["pass_if_stress_dsr_gt"], 4),
            "expression_zh": (
                "當 " + " 或 ".join(filter(None, [
                    f"價格 > HK${max_buy:,.0f}" if max_buy else None,
                    "壓力情境年度現金流 < 0" if purpose == "rental" else None,
                    f"公允價值溢價 > {V['pass_if_premium_gt']:.0%}",
                    f"壓力 DSR > {V['pass_if_stress_dsr_gt']:.0%}" if base_dsr is not None else None,
                    "風險等級 = Very High",
                ])) + " 時，應放棄。"),
        },
    }

    key_numbers = {
        "asking_price": _f(price),
        "fair_value": _f(valuation.get("fair_value")),
        "premium_pct": _f(premium, 4),
        "initial_cash_required": _f((uw_out.get("initial_cash_required")
                                     or uw_out.get("total_initial_cash_required"))),
        "mortgage_amount": _f(uw_out.get("loan_amount")),
        "monthly_payment": _f(uw_out.get("monthly_payment")),
        "net_yield": _f(net_yield, 6),
        "base_irr": _f(base_irr, 6),
        "stress_irr": _f(stress_irr, 6),
        "risk_level": risk_level,
        "dsr": _f(base_dsr, 6),
        "stress_dsr": _f(stress_dsr, 6),
        "min_dscr": _f(dscr, 4),
        "annual_cash_flow": _f(base_cf),
        "maximum_buy_price": _f(max_buy),
    }

    label_en = C.VERDICT_LABELS.get(label.lower(), label)
    return {
        "label": label_en,
        "label_zh": C.VERDICT_LABELS_ZH.get(label_en, ""),
        "code": code,
        "why": why,
        "reasons": ([c["detail"] for c in buy_checks]
                    + [f"[{c['code']}] {c['detail']}" for c in caveats]),
        "buy_checks": buy_checks,
        "blockers": blockers,
        "caveats": caveats,
        "valuation_unverified": valuation_unverified,
        # NOTE: only inputs that make the FINANCIAL case unknown land here (e.g.
        # no income). Missing comparable evidence is a caveat, not a blocker.
        "missing_inputs": missing,
        "key_numbers": key_numbers,
        "major_risks": [
            {"id": r["id"], "label": r["label"], "level": r["level"],
             "impact": r["impact"]} for r in (risk.get("top_risks") or [])],
        "what_would_change": changes,
        "conditional": conditional,
        "decision_basis": "deterministic thresholds (tools/config.py VERDICT_THRESHOLDS)",
        "provenance": C.MODEL,
    }
