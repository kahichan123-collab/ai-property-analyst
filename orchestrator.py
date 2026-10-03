# -*- coding: utf-8 -*-
"""orchestrator.py — AI Property Analyst pipeline (v2.0)

    Request (validated schema)
      -> Property Data Extraction
      -> Deterministic Underwriting
      -> Acquisition Cost Model
      -> Valuation Engine
      -> Market Research
      -> Scenario Engine (BEAR / BASE / BULL)
      -> Interest-Rate Stress
      -> Risk Engine
      -> Transaction Boundary
      -> Investment Verdict
      -> DeepSeek narrative (optional, auxiliary, never numeric)
      -> Report dict / HTML

Deterministic first, AI second: every financial figure is produced by
tools/*.py. The LLM receives the finished numbers and may only explain them —
it can never compute, replace or override one. If no API key is configured the
whole analysis still completes.
"""
from __future__ import annotations

import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import config as C
from tools import schema as sc
from tools import underwriting as uw
from tools import research as rs
from tools import official as official_mod
from tools import stress as st
from tools import acquisition as acq_mod
from tools import valuation as val_mod
from tools import scenarios as scen_mod
from tools import regime as regime_mod
from tools import risk as risk_mod
from tools import boundary as boundary_mod
from tools import verdict as verdict_mod
from tools.provenance import Ledger, data_quality
from tools.extract import extract_property, extract_from_html
from tools.llm import llm_available, chat_json_ex


# --------------------------------------------------------------------------- #
#  LLM narrative (auxiliary only)                                             #
# --------------------------------------------------------------------------- #
NARRATIVE_SYSTEM = (
    "你是一名香港住宅物業分析助手。以下所有數字均由 deterministic 財務引擎計算完成，"
    "你的角色只是「解釋」，不可計算、不可修改、不可新增任何數字。\n"
    "規則：\n"
    "1. 只能引用輸入 JSON 中已存在的數字；不得自行推算新數字。\n"
    "2. 不確定或資料不足時，必須寫「需進一步核實」或「資料不足」，不可猜測。\n"
    "3. 必須尊重 provenance：Observed / User Input 屬事實；Estimated / Assumption / "
    "Research Approximation 必須明確標示為不確定。\n"
    "4. 若 verdict 為 PASS/WATCH，不可寫成建議買入。\n"
    '回傳 strict JSON：{"executive_summary":"...","affordability_plain":"...",'
    '"market_regime_view":"...","valuation_view":"...","key_risks":["..."],'
    '"investment_thesis":"...","price_view":"...","next_steps":["..."],'
    '"uncertainty_note":"..."}'
)


def _pack_json(pack: dict, limit: int = 9000) -> str:
    """Serialise the pack for the model, staying VALID JSON.

    The old code did `body[:9000]`, slicing a 15.8k-char pack mid-string: the
    model received a malformed document whose tail was an arbitrary fragment.
    Drop the bulkiest optional sections instead, then fall back to a core subset,
    so what is sent can always be parsed.
    """
    droppable = ("scenarios", "rate_stress", "market_regime", "acquisition",
                 "data_quality", "boundary")
    for drop in range(len(droppable) + 1):
        trimmed = {k: v for k, v in pack.items() if k not in droppable[:drop]}
        if drop:
            trimmed["instructions"] = (str(trimmed.get("instructions", ""))
                                       + f"（因長度限制未附上：{', '.join(droppable[:drop])}）")
        text = json.dumps(trimmed, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return text
    core = ("purpose", "property", "user_inputs", "underwriting", "valuation",
            "verdict", "instructions")            # last resort: essentials only
    essentials = {k: pack[k] for k in core if k in pack}
    return json.dumps(essentials, ensure_ascii=False, default=str)


def _llm_narrative(pack: dict) -> tuple[dict | None, str | None]:
    """(narrative, failure_reason)."""
    if not llm_available():
        return None, "no_api_key"
    try:
        return chat_json_ex(NARRATIVE_SYSTEM, _pack_json(pack), max_tokens=1600)
    except Exception as e:                                  # pragma: no cover - defensive
        return None, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #
def _max_affordable_price(income, debt, ltv, rate, tenure):
    """Legacy helper kept for report/back-compat: largest price with DSR <= 50%.

    Returns None when there is no income to underwrite against (e.g. a pure
    rental case with no personal income declared) — the report then shows "—"
    instead of raising and aborting an otherwise complete analysis.
    """
    if not income:
        return None
    return uw.price_for_dsr(income, debt or 0.0, C.BANK_DSR_CAP, ltv, rate, tenure)


def _llm_pack(report: dict) -> dict:
    return {
        "purpose": report["meta"]["purpose"],
        "property": report["property"],
        "user_inputs": {"finance": report["finance"], "financing": report["financing"],
                        "rental": report.get("rental_inputs") or {},
                        "acquisition": report.get("acquisition_inputs") or {}},
        "underwriting": report.get("underwriting"),
        "acquisition": report.get("acquisition"),
        "valuation": {k: v for k, v in (report.get("valuation") or {}).items()
                      if k != "evidence_entries"},
        "scenarios": report.get("scenarios"),
        "rate_stress": report.get("rate_stress"),
        "market_regime": {k: v for k, v in (report.get("market_regime") or {}).items()
                          if k != "signals"},
        "risk": {"level": (report.get("risk") or {}).get("level"),
                 "top_risks": (report.get("risk") or {}).get("top_risks")},
        "boundary": report.get("transaction_boundary"),
        "verdict": report.get("verdict"),
        "data_quality": (report.get("data_quality") or {}).get("provenance_counts"),
        "instructions": ("只可解釋以上已計算的結果。不得計算或修改任何數字。"
                         "資料不足時必須明說。"),
    }


def _error_report(errors, warnings, meta=None, pipeline=None) -> dict:
    return {
        "ok": False, "errors": errors, "warnings": warnings or [],
        "verdict": {"label": None, "code": None, "why": "輸入未通過驗證，未進行分析。"},
        "summary": None, "metrics": {}, "risks": [], "scenarios": {},
        "official_context": [], "official_data_status": {}, "comparables": [],
        "html": None, "meta": meta or {}, "pipeline_log": pipeline or [],
    }


# --------------------------------------------------------------------------- #
#  Main entry point                                                           #
# --------------------------------------------------------------------------- #
def analyse_request(request: dict, ext_html: str | None = None,
                    allow_llm: bool = True) -> dict:
    """Run the whole pipeline from a raw request body (2.0 nested schema)."""
    log: list[dict] = []

    def step(name, ok, note=""):
        log.append({"step": name, "status": "ok" if ok else "skipped", "note": note})

    norm = sc.normalize_request(request)
    errors, warnings = norm["errors"], norm["warnings"]
    if not norm["ok"]:
        step("validate_request", False, f"{len(errors)} validation error(s)")
        return _error_report(errors, warnings, {"stage": "validate"}, log)
    payload = norm["payload"]
    purpose = payload["purpose"]
    mode = payload["mode"]
    step("validate_request", True,
         f"purpose={purpose} mode={mode}"
         + (f"; ignored: {payload['meta'].get('ignored_rental_fields')}"
            if payload["meta"].get("ignored_rental_fields") else ""))

    ledger = Ledger()
    url = (payload["property"].get("url") or "").strip()

    # ---------------- 1. extraction ------------------------------------- #
    ext: dict = {}
    extraction_ok = False
    if ext_html is not None:
        ext = extract_from_html(ext_html, source_url=url, allow_llm=allow_llm)
        extraction_ok = ext.get("price") is not None
        step("extract", extraction_ok, "provided HTML (fixture/manual)"
             + ("" if extraction_ok else "; no price found"))
    elif mode == "auto" and url:
        ext = extract_property(url, allow_llm=allow_llm)
        # "ok" means a PRICE came out, not merely that the page was fetched:
        # a JS-rendered listing returns HTTP 200 with no price in the markup.
        extraction_ok = not ext.get("error") and ext.get("price") is not None
        step("extract", extraction_ok,
             f"missing={ext.get('missing_fields')}" if extraction_ok
             else (ext.get("error") or "page fetched but no price found"))
        if not extraction_ok:
            warnings.append({"field": "property.url", "code": "extraction_failed",
                             "message": "無法從樓盤網址自動讀取叫價（頁面可能由 JavaScript 產生或"
                                        "阻擋自動讀取）；系統不會猜測價格，請使用價格覆寫或手動輸入。"})
    else:
        step("extract", True, "manual input mode (no URL fetch)")

    resolved = sc.resolve_inputs(payload, ext)
    resolved["monthly_rent"] = (payload.get("rental") or {}).get("monthly_rent")

    cross_errors, cross_warnings = sc.cross_field_checks(payload, resolved)
    warnings.extend(cross_warnings)
    errors2 = sc.validate_resolved(payload, resolved, mode, extraction_ok,
                                   ext.get("error")) + cross_errors
    if errors2:
        step("validate_inputs", False, f"{len(errors2)} blocking issue(s)")
        return _error_report(errors + errors2, warnings, {"stage": "resolve"}, log)

    price = resolved["asking_price"]
    area = resolved.get("usable_area_sqft")
    step("validate_inputs", True,
         f"price={price:,.0f}" + (f" area={area:,.0f}" if area else ""))

    # ---------------- 2. research --------------------------------------- #
    prop_for_research = {"price": price, "usable_area_sqft": area,
                         "estate": resolved.get("estate"), "layout": resolved.get("layout")}
    comps = rs.search_comparables(prop_for_research)
    # User-supplied comparable evidence wins over anything a connected source
    # returns: it is the human's own reading of the market and the only path to
    # real evidence when no feed is wired. Tagged user-input so the report shows
    # it as User Input, never as an observed market feed.
    user_comps = payload.get("comparables") or []
    if user_comps:
        comps = [e for e in comps if str(e.get("source")) != "unavailable"] + list(user_comps)
    for c in comps:
        c.setdefault("data", {})
        c["data"]["subject_estate"] = resolved.get("estate")
        c["data"]["subject_layout"] = resolved.get("layout")
    comp_entries = []
    for e in comps:
        if str(e.get("source")) == "unavailable":
            # research.py's "no connected source" placeholder is availability
            # metadata, not evidence. Counting it made comparable_count show 1
            # while the benchmark was None — and the report listed it as a
            # comparable. Same rule tools/regime.py already applies.
            continue
        d = dict(e.get("data") or {})
        # User-supplied entries carry price/area at the TOP level (that is the
        # shape tools/schema.py validates); research entries carry them inside
        # `data`. Reading only `data` silently produced a benchmark of None with
        # a non-zero comparable_count.
        for k in ("price", "usable_area_sqft", "estate", "layout", "block"):
            if d.get(k) in (None, "") and e.get(k) not in (None, ""):
                d[k] = e[k]
        comp_entries.append({**d, "title": e.get("title"), "source": e.get("source"),
                             "date": e.get("date"), "confidence": e.get("confidence")})
    comp_metrics = (rs.compute_comparable_metrics(price, area, comp_entries)
                    if (price and area) else None)
    rents = rs.search_rental_comparables(prop_for_research)
    mkt = rs.search_market_context(prop_for_research)
    mrtg = rs.search_mortgage_context(prop_for_research)
    pol = rs.search_policy_context(prop_for_research)
    # Official, free, documented sources (HKMA mortgage survey + Land Registry
    # registrations). Real Observed facts, so the market-regime engine has
    # something to classify instead of permanently "no data". Never blocks:
    # each source degrades to nothing on failure.
    step("research", True,
         f"comparables={comp_metrics.get('comparable_count') if comp_metrics else 0}")
    official_entries, official_status = official_mod.fetch_all()
    step("official_data", bool(official_entries) or not official_status.get("enabled"),
         f"{len(official_entries)} official entries ({official_status.get('hkma_mortgage')}"
         f"/{official_status.get('landreg')})")

    # ---------------- 3. deterministic underwriting ---------------------- #
    fin = payload["financing"]
    pf = payload["finance"]
    prop_in = payload["property"]
    aq = payload["acquisition"]
    mgmt = prop_in.get("monthly_mgmt_rates")
    if mgmt is None:
        mgmt = C.DEFAULT_MONTHLY_MGMT_RATES
        warnings.append({"field": "property.monthly_mgmt_rates", "code": "assumed",
                         "message": f"未提供管理費，已假設每月 HK${mgmt:,.0f}（可修改）。"})
    extra_upfront = {
        "mortgage_fees": aq.get("mortgage_fees", 0.0),
        "valuation_fee": aq.get("valuation_fee", 0.0),
        "renovation_furnishing": aq.get("renovation_furnishing", 0.0),
        "initial_reserve": aq.get("initial_reserve", 0.0),
    }
    fee_rate = fin.get("fee_rate", C.DEFAULT_FEE_RATE)
    uw_out: dict | None = None
    try:
        if purpose == "owner_occupier":
            uw_out = uw.owner_underwriting(
                price=price, ltv=fin["ltv"], annual_rate=fin["mortgage_rate"],
                tenure_years=fin["tenure_years"], amort=fin.get("amortization", "PI"),
                monthly_net_income=pf.get("monthly_net_income"),
                existing_monthly_debt=pf.get("existing_monthly_debt", 0.0),
                monthly_mgmt_rates=mgmt, fee_rate=fee_rate, area_sqft=area,
                other_recurring_income=pf.get("other_recurring_income", 0.0),
                **extra_upfront)
        else:
            r = payload["rental"]
            uw_out = uw.rental_underwriting(
                price=price, area_sqft=area, monthly_rent=r["monthly_rent"],
                vacancy=r["vacancy"], rates_pct=r["rates_pct"], opex_pct=r["opex_pct"],
                annual_capex=r["annual_capex"], ltv=fin["ltv"],
                annual_rate=fin["mortgage_rate"], tenure_years=fin["tenure_years"],
                amort=fin.get("amortization", C.DEFAULT_AMORTIZATION), rent_growth=r["rent_growth"],
                hold_years=r["hold_years"], exit_cap=r["exit_cap"],
                sell_cost=r["sell_cost"], fee_rate=fee_rate,
                disc_rate=fin.get("discount_rate", C.DEFAULT_DISCOUNT_RATE), **extra_upfront)
        uw_out["purpose"] = purpose
        step("underwrite", True, "deterministic engine")
    except Exception as e:                                  # never crash the pipeline
        step("underwrite", False, str(e))
        errors.append({"field": "underwriting", "code": "engine_error",
                       "message": f"試算引擎錯誤：{e}", "severity": "error"})
        return _error_report(errors, warnings, {"stage": "underwrite"}, log)

    # ---------------- 4. acquisition cost model -------------------------- #
    acquisition = acq_mod.acquisition_costs(
        price=price, ltv=fin["ltv"], fee_rate=fee_rate,
        agency_fee_pct=aq.get("agency_fee_pct"), legal_fee_pct=aq.get("legal_fee_pct"),
        monthly_housing_cost=uw_out.get("monthly_housing_cost") or uw_out.get("monthly_payment"),
        area_sqft=area, **extra_upfront)
    step("acquisition", True,
         f"total cash {acquisition['total_initial_cash_required']:,.0f}")

    # ---------------- 5. valuation --------------------------------------- #
    valuation = val_mod.valuate(price, area, comp_metrics, comps,
                                rental_uw=uw_out if purpose == "rental" else None,
                                purpose=purpose)
    step("valuation", bool(valuation.get("fair_value")), valuation.get("classification", ""))

    # ---------------- 6. scenarios + rate stress ------------------------- #
    scenario_inputs = {
        "price": price, "area": area, "ltv": fin["ltv"], "rate": fin["mortgage_rate"],
        "tenure": fin["tenure_years"], "amort": fin.get("amortization", C.DEFAULT_AMORTIZATION),
        "income": pf.get("monthly_net_income"),
        "other_income": pf.get("other_recurring_income", 0.0),
        "debt": pf.get("existing_monthly_debt", 0.0), "mgmt": mgmt,
        "fee_rate": fee_rate, "disc_rate": fin.get("discount_rate", C.DEFAULT_DISCOUNT_RATE),
        "rental": dict(payload["rental"]), "owner_hold_years": C.OWNER_HOLD_YEARS_DEFAULT,
        **extra_upfront,
    }
    scenarios_out = scen_mod.run_scenarios(purpose, scenario_inputs)
    rate_stress = scen_mod.run_rate_stress(purpose, scenario_inputs)
    step("scenarios", True, "bear/base/bull + rate stress")

    # ---------------- 7. market regime ----------------------------------- #
    market_regime = regime_mod.classify(
        mkt + [e for e in official_entries if e["relevance"] == "transaction_volume"],
        mrtg + [e for e in official_entries if e["relevance"] == "mortgage-context"],
        pol)
    step("market_regime", market_regime["data_available"],
         f"{market_regime['regime']} (confidence {market_regime['confidence']})")

    # ---------------- 8. risk engine ------------------------------------- #
    risk = risk_mod.assess(purpose, resolved, payload, uw_out, valuation, rate_stress,
                           acquisition, scenarios_out)
    step("risk", True, f"level={risk['level']}")

    # ---------------- 9. transaction boundary ---------------------------- #
    boundary = boundary_mod.compute(purpose, resolved, payload, uw_out, valuation, acquisition)
    step("transaction_boundary",
         boundary.get("maximum_buy_price", {}).get("value") is not None,
         f"max buy price = {boundary.get('maximum_buy_price', {}).get('value')}")

    # ---------------- 10. verdict ---------------------------------------- #
    verdict = verdict_mod.decide(purpose, uw_out, valuation, boundary, risk, rate_stress,
                                 scenarios_out, payload, resolved)
    step("verdict", True, verdict["label"])

    # ---------------- 11. stress table (owner) --------------------------- #
    stress_rows = None
    if purpose == "owner_occupier":
        stress_rows = st.summarize_owner_stress(st.stress_owner(
            price=price, ltv=fin["ltv"], annual_rate=fin["mortgage_rate"],
            tenure_years=fin["tenure_years"], amort=fin.get("amortization", "PI"),
            monthly_net_income=pf.get("monthly_net_income"),
            existing_monthly_debt=pf.get("existing_monthly_debt", 0.0),
            monthly_mgmt_rates=mgmt, fee_rate=fee_rate))

    # ---------------- 12. provenance ledger ------------------------------ #
    _fill_ledger(ledger, payload, resolved, ext, uw_out, valuation, comp_metrics, acquisition)
    warnings_all = [w.get("message", str(w)) if isinstance(w, dict) else str(w)
                    for w in warnings]
    dq = data_quality(ledger, missing_fields=ext.get("missing_fields"),
                      warnings=warnings_all,
                      extra={"extraction_error": ext.get("error")})

    # ---------------- 13. report dict ------------------------------------ #
    report = {
        "ok": True,
        "engine_version": C.ENGINE_VERSION,
        "schema_version": C.SCHEMA_VERSION,
        "errors": [],
        "warnings": warnings,
        "meta": {"url": url, "mode": mode, "purpose": purpose,
                 "generated_note": ("deterministic engine + rule-based extraction; "
                                    "AI narrative is auxiliary"),
                 "llm_available": llm_available()},
        "pipeline_log": log,
        "property": {
            "price": price, "asking_price": price,
            "usable_area_sqft": area, "gross_area_sqft": resolved.get("gross_area_sqft"),
            "estate": resolved.get("estate"), "district": resolved.get("district"),
            "building": resolved.get("building"), "layout": resolved.get("layout"),
            "floor": resolved.get("floor"), "orientation": resolved.get("orientation"),
            "view": resolved.get("view"),
            "special_features": resolved.get("special_features"),
            "building_age": resolved.get("building_age"),
            "monthly_mgmt_rates": mgmt,
            "rates_amount": prop_in.get("rates_amount"),
            "price_psf": round(price / area, 0) if area else None,
            "price_source": resolved.get("_price_source"),
            "source_url": url,
        },
        "extraction": ext,
        "finance": pf,
        "financing": fin,
        "rental_inputs": payload["rental"],
        "acquisition_inputs": aq,
        "underwriting": uw_out,
        "acquisition": acquisition,
        "valuation": valuation,
        "scenarios": scenarios_out["scenarios"],
        "scenario_meta": {k: v for k, v in scenarios_out.items() if k != "scenarios"},
        "rate_stress": rate_stress,
        "stress": stress_rows,
        "market_regime": market_regime,
        "risk": risk,
        "transaction_boundary": boundary,
        "verdict": verdict,
        "price_analysis": {
            "max_affordable_price": _max_affordable_price(
                pf.get("monthly_net_income"), pf.get("existing_monthly_debt", 0.0),
                fin["ltv"], fin["mortgage_rate"], fin["tenure_years"]),
            "target_purchase_price": valuation.get("buy_target_price"),
            "maximum_buy_price": boundary.get("maximum_buy_price", {}).get("value"),
            "note": ("max affordable = largest price keeping DSR <= 50%; "
                     "target = fair value less the safety-margin buffer; "
                     "maximum buy price = tightest of all boundary constraints."),
        },
        "comparables": {"entries": comps, "metrics": comp_metrics},
        "rental_evidence": rents,
        "market_context": mkt,
        "mortgage_context": mrtg,
        "official_context": official_entries,
        "official_data_status": official_status,
        "policy_context": pol,
        "provenance": ledger.as_dict(public=False),
        "provenance_grouped": ledger.grouped(),
        "data_quality": dq,
        "assumptions": [
            "AVD scale 2024/26 incl. marginal-relief bands (re-verify if law changes)",
            "property tax = 0.12 x max(0, EGI - rates)",
            "management/maintenance grows +2.5%/yr (matches prototype HTML)",
            "IRR uses annual year-end cash flows (not monthly)",
            "interest rate held constant over the hold period",
            "scenario shocks are policy settings in tools/config.py, not user input",
        ],
        "disclaimer": ("僅供參考，非投資／稅務／按揭建議。銀行批核、估值與法規須另行核實。"
                       "Deterministic 模型輸出取決於使用者輸入與已標示的假設。"),
    }

    # ---------------- 14. response envelope ------------------------------ #
    report["summary"] = _summary(report)
    report["metrics"] = verdict["key_numbers"]
    report["risks"] = risk.get("risks", [])
    report["caveats"] = verdict.get("caveats", [])

    # ---------------- 15. LLM narrative (optional, LAST) ----------------- #
    # Deliberately the FINAL step: nothing that can raise runs after the paid
    # API call, so the server's one-shot retry can never spend credits twice.
    if allow_llm:
        narr, reason = _llm_narrative(_llm_pack(report))
        if narr and isinstance(narr, dict):
            narr["note"] = ("AI-assisted write-up — interpretation only. It cannot "
                            "compute, change or replace any deterministic number.")
            narr["provenance"] = C.ASSUMED
            report["llm_narrative"] = narr
            step("llm_narrative", True, "auxiliary narrative attached")
        else:
            report["llm_narrative_error"] = reason or "unknown"
            step("llm_narrative", False, f"not attached ({report['llm_narrative_error']})")
    else:
        report["llm_narrative_error"] = "disabled_by_caller"
        step("llm_narrative", False, "disabled by caller")

    return report


def _fill_ledger(ledger, payload, resolved, ext, uw_out, valuation, comp_metrics, acquisition):
    prop = payload["property"]
    ledger.add("property.asking_price", resolved.get("asking_price"),
               C.USER if prop.get("asking_price") else
               (C.OBSERVED if resolved.get("asking_price") else None),
               "user input / listing extraction", "high", "叫價 Asking Price")
    ledger.add("property.usable_area_sqft", resolved.get("usable_area_sqft"),
               C.USER if prop.get("usable_area_sqft") else C.OBSERVED,
               "user input / listing extraction", "high", "實用面積")
    ledger.add("property.building_age", resolved.get("building_age"),
               C.USER if prop.get("building_age") is not None else C.OBSERVED,
               "user input / listing", "medium", "樓齡")
    ledger.add("finance.monthly_net_income",
               (payload["finance"] or {}).get("monthly_net_income"),
               C.USER, "user input", "high", "每月淨收入")
    ledger.add("financing.ltv", payload["financing"].get("ltv"), C.USER,
               "user input", "high", "貸款成數")
    ledger.add("financing.mortgage_rate", payload["financing"].get("mortgage_rate"),
               C.USER, "user input", "medium", "按揭利率")
    if payload["purpose"] == "rental":
        ledger.add("rental.monthly_rent", (payload["rental"] or {}).get("monthly_rent"),
                   C.USER, "user input / rental evidence", "medium", "每月租金")
        ledger.add("rental.exit_cap", (payload["rental"] or {}).get("exit_cap"),
                   C.ASSUMED, "user assumption", "low", "退出資本化率")
        ledger.add("rental.rent_growth", (payload["rental"] or {}).get("rent_growth"),
                   C.ASSUMED, "user assumption", "low", "租金年增長")
    ledger.add("valuation.fair_value", valuation.get("fair_value"),
               C.MODEL if valuation.get("fair_value") else None,
               "comparable benchmark x area", "low", "公允價值 Fair value")
    ledger.add("valuation.comparable_count", (comp_metrics or {}).get("comparable_count"),
               C.RESEARCH if comp_metrics else None, "research dataset", "low", "可比個數")
    ledger.add("acquisition.total_initial_cash_required",
               acquisition.get("total_initial_cash_required"), C.MODEL,
               "AVD scale + fee policy", "high", "交易所需總現金")
    ledger.add("acquisition.recommended_reserve", acquisition.get("recommended_reserve"),
               C.ASSUMED, f"{C.RESERVE_MONTHS_RECOMMENDED} months of housing cost",
               "low", "建議初始儲備")
    for key in ("dsr", "monthly_payment", "min_dscr", "net_yield", "levered_irr",
                "annual_cash_flow_y1", "gross_yield", "cash_on_cash"):
        if uw_out.get(key) is not None:
            ledger.add(f"underwriting.{key}", uw_out[key], C.MODEL,
                       "deterministic engine", "high", key)
    if ext.get("source_url"):
        ledger.add("extraction.source", ext.get("source_url"), C.OBSERVED,
                   "listing extraction", "low", "資料來源")


def _summary(report: dict) -> dict:
    v = report["verdict"]
    val = report.get("valuation") or {}
    acq = report.get("acquisition") or {}
    uw_out = report.get("underwriting") or {}
    risk = report.get("risk") or {}
    rs_ = report.get("rate_stress") or {}
    return {
        "verdict": v["label"],
        "headline": f"{v['label']} — {v['why']}",
        "property": {
            "estate": (report.get("property") or {}).get("estate"),
            "asking_price": (report.get("property") or {}).get("price"),
            "area_sqft": (report.get("property") or {}).get("usable_area_sqft"),
        },
        "fair_value": val.get("fair_value"),
        "premium_pct": val.get("premium_pct"),
        "valuation_class": val.get("classification"),
        "total_initial_cash_required": acq.get("total_initial_cash_required"),
        "down_payment": acq.get("down_payment"),
        "mortgage_amount": uw_out.get("loan_amount"),
        "monthly_payment": uw_out.get("monthly_payment"),
        "net_yield": uw_out.get("net_yield"),
        "base_irr": (report.get("scenarios", {}).get("base") or {}).get("irr"),
        "stress_irr": (report.get("scenarios", {}).get("bear") or {}).get("irr"),
        "risk_level": risk.get("level"),
        "top_risks": [r["label"] for r in (risk.get("top_risks") or [])],
        "maximum_buy_price": (report.get("transaction_boundary", {})
                              .get("maximum_buy_price", {}) or {}).get("value"),
        "target_rent": (report.get("transaction_boundary", {})
                        .get("target_rent", {}) or {}).get("value"),
        "stress_summary": rs_.get("summary"),
        "key_numbers": v.get("key_numbers"),
    }


# --------------------------------------------------------------------------- #
#  Legacy CLI entry point (analyst.py)                                        #
# --------------------------------------------------------------------------- #
def analyse(url: str, user: dict, financing: dict, purpose: str,
            price_override: float | None = None, area_override: float | None = None,
            ext: dict | None = None, manual: dict | None = None,
            allow_llm: bool = True) -> dict:
    """Back-compat wrapper around analyse_request() for the CLI."""
    manual = manual or {}
    request = {
        "mode": "manual" if (manual or not url) else "auto",
        "purpose": purpose,
        "property": {
            "url": url,
            "estate": manual.get("estate"),
            "layout": manual.get("layout"),
            "asking_price": manual.get("price") or user.get("price"),
            "usable_area_sqft": manual.get("area"),
            "monthly_mgmt_rates": manual.get("monthly_mgmt_rates"),
            "building_age": manual.get("building_age"),
            "district": manual.get("district"),
            "building": manual.get("building"),
            "floor": manual.get("floor"),
            "special_features": manual.get("special_features"),
        },
        "finance": {"monthly_net_income": user.get("monthly_net_income"),
                    "gross_monthly_income": user.get("gross_monthly_income"),
                    "other_recurring_income": user.get("other_recurring_income"),
                    "existing_monthly_debt": user.get("existing_monthly_debt"),
                    "liquid_cash": user.get("liquid_cash"),
                    "available_cash_for_transaction": user.get("available_cash_for_transaction"),
                    "emergency_reserve": user.get("emergency_reserve"),
                    "max_comfortable_monthly_payment": user.get("max_comfortable_monthly_payment")},
        "financing": dict(financing or {}),
        "rental": ({k: manual.get(k) for k in sc.RENTAL_KEYS if manual.get(k) is not None}
                   if purpose == "rental" else {}),
        "acquisition": manual.get("acquisition") or {},
        "overrides": {"price": price_override, "area": area_override},
    }
    ext_html = None
    if isinstance(ext, str):
        ext_html = ext
    elif isinstance(ext, dict) and ext.get("_html"):
        ext_html = ext["_html"]
    return analyse_request(request, ext_html=ext_html, allow_llm=allow_llm)
