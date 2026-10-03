# -*- coding: utf-8 -*-
"""tools/report.py — render the analysis dict into a standalone HTML report (v2.0).

Report structure (spec #19) — on page 1 the reader sees the verdict banner and a
KPI board, then:

    1 Executive Summary            8  Interest Rate Stress Test
    2 Property Snapshot            9  Market Regime
    3 Financing                   10  Risk Analysis
    4 Acquisition Cost            11  Transaction Boundary
    5 Rental Economics            12  Investment Verdict
    6 Valuation                   13  Data Quality / Assumptions
    7 Scenario Analysis           14  Sources

Every material number carries a provenance badge; research sources are shown
separately from model output. Rendering is pure string building — no LLM.
"""
from __future__ import annotations

import sys, os
from html import escape as _x

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import config as C


# --------------------------------------------------------------------------- #
#  Formatting helpers                                                         #
# --------------------------------------------------------------------------- #
def _hk(x, dp=0):
    if x is None:
        return "—"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if v != v:
        return "—"
    return ("-" if v < 0 else "") + "HK$" + f"{abs(v):,.{dp}f}"


def _pct(x, dp=1, signed=False):
    if x is None:
        return "—"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if v != v:
        return "—"
    return f"{v * 100:+.{dp}f}%" if signed else f"{v * 100:.{dp}f}%"


def _caveats_block(verdict: dict) -> str:
    """Disclosed limits on an otherwise usable verdict.

    A missing comparable-evidence set does not make the financial case unknown,
    so the verdict is no longer forced to WATCH for it — but the reader must
    still be told, prominently, that the price judgement is unverified.
    """
    items = (verdict or {}).get("caveats") or []
    if not items:
        return ""
    lis = "".join(f"<li><b>{_x(c.get('code'))}</b>：{_x(c.get('detail'))}</li>"
                  for c in items)
    return (f"<div class='caveat'><b>⚠ 判決限制 VERDICT CAVEATS</b>"
            f"（已計入其他判決條件；不強制降級為 WATCH）<ul>{lis}</ul></div>")


def _num(x, dp=2, suffix=""):
    if x is None:
        return "—"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if v != v:
        return "—"
    return f"{v:,.{dp}f}{suffix}"


def _txt(x):
    return _x(str(x)) if x not in (None, "") else "—"


def _ai_short(reason):
    """One-line header status for the AI narrative, naming the REAL cause."""
    if not reason:
        return "未使用（deterministic 結果不受影響）"
    if reason == "no_api_key":
        return "未附加（沒有 API key）"
    if reason == "disabled_by_caller":
        return "未附加（本次執行停用）"
    return "未附加（AI 呼叫失敗：" + _x(str(reason)) + "）"


def _prov(label):
    if not label:
        return ""
    cls = {"Observed": "p-obs", "User Input": "p-user", "Estimated": "p-est",
           "Assumption": "p-asm", "Research Approximation": "p-res",
           "Model Output": "p-mod"}.get(label, "p-mod")
    return f'<span class="prov {cls}">{_x(label)}</span>'


def _table(headers, rows, cls=""):
    head = "".join(f"<th>{_x(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td class='{c}'>{v}</td>" for v, c in row) + "</tr>"
        for row in rows)
    return f"<table class='{cls}'><tr>{head}</tr>{body}</table>"


def _card(title, body, sub=""):
    sub_html = f"<div class='sub'>{sub}</div>" if sub else ""
    return f'<div class="card"><h3>{title}</h3>{sub_html}{body}</div>'


def _kpi(label, value, sub="", tone=""):
    return (f'<div class="kpi {tone}"><div class="t">{label}</div>'
            f'<div class="v">{value}</div>'
            + (f'<div class="s">{sub}</div>' if sub else "") + "</div>")


def _risk_chip(level):
    cls = "ok" if level == "low" else "warn" if level == "medium" else ""
    return f"<span class='chip {cls}'>{_x(C.RISK_LEVEL_LABELS.get(level, level))}</span>"


# --------------------------------------------------------------------------- #
#  Main renderer                                                              #
# --------------------------------------------------------------------------- #
def render(report: dict) -> str:
    v = report.get("verdict") or {}
    label = v.get("label") or "WATCH"
    label_zh = v.get("label_zh") or C.VERDICT_LABELS_ZH.get(label, "")
    label_full = f"{label} {label_zh}".strip()
    color = {"BUY": "#1E7B34", "NEGOTIATE": "#B7950B",
             "WATCH": "#2E75B6", "PASS": "#C00000"}.get(label, "#444")
    prop = report.get("property") or {}
    fin = report.get("financing") or {}
    pf = report.get("finance") or {}
    rental = report.get("rental_inputs") or {}
    uw = report.get("underwriting") or {}
    acq = report.get("acquisition") or {}
    val = report.get("valuation") or {}
    scen = report.get("scenarios") or {}
    scen_meta = report.get("scenario_meta") or {}
    rs = report.get("rate_stress") or {}
    risk = report.get("risk") or {}
    reg = report.get("market_regime") or {}
    bnd = report.get("transaction_boundary") or {}
    summ = report.get("summary") or {}
    dq = report.get("data_quality") or {}
    meta = report.get("meta") or {}
    purpose = meta.get("purpose", "owner_occupier")
    is_rental = purpose == "rental"
    risk_level = risk.get("level")
    prem = val.get("premium_pct")

    h: list[str] = [f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>物業分析報告 · {_txt(prop.get('estate'))} · {label}</title>
<style>
 body{{font-family:Calibri,'Microsoft JhengHei',sans-serif;margin:0;background:#F3F5F9;color:#1a1a1a;-webkit-text-size-adjust:100%}}
 .wrap{{max-width:1000px;margin:0 auto;padding:18px}}
 header{{background:linear-gradient(135deg,#1B365D,#2E5E9E);color:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(27,54,93,.25)}}
 header h1{{margin:0;font-size:20px}} header p{{margin:5px 0 0;font-size:12px;opacity:.9;overflow-wrap:anywhere}}
 .verdict{{border-radius:10px;color:#fff;padding:16px 20px;margin:12px 0;font-size:22px;font-weight:800}}
 .verdict .vb{{font-size:12px;font-weight:700;opacity:.95;margin-bottom:4px;letter-spacing:.06em}}
 .verdict .why{{font-size:12.5px;font-weight:400;margin-top:6px;line-height:1.5}}
 .card{{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:14px;margin:12px 0}}
 .card h3{{margin:0 0 8px;font-size:13px;color:#1B365D;border-bottom:2px solid #1B365D;padding-bottom:5px}}
 .card .sub{{font-size:11px;color:#6B7280;margin:-4px 0 8px}}
 table{{border-collapse:collapse;width:100%;font-size:12px}}
 td,th{{border:1px solid #d6dee9;padding:4px 7px}}
 th{{background:#1B365D;color:#fff;text-align:left;font-weight:600}}
 td.l{{text-align:left}} td.r{{text-align:right}} td.c{{text-align:center}}
 tr:nth-child(even) td{{background:#FAFBFD}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}}
 .kpi{{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:10px;text-align:center;border-top:3px solid #2E75B6}}
 .kpi .t{{font-size:10px;color:#777;line-height:1.3}} .kpi .v{{font-size:17px;font-weight:800;color:#1B365D;margin-top:2px}}
 .kpi .s{{font-size:10px;color:#888;margin-top:2px}}
 .kpi.good{{border-top-color:#1E7B34}} .kpi.bad{{border-top-color:#C00000}} .kpi.warn{{border-top-color:#B7950B}}
 .chip{{display:inline-block;background:#FBE4E4;color:#9c0000;border-radius:999px;padding:2px 9px;margin:2px;font-size:11px}}
 .chip.warn{{background:#FFF3CD;color:#7a5d00}} .chip.ok{{background:#E7F4E8;color:#1E7B34}}
 .chip.info{{background:#E8F0FB;color:#1B365D}}
 .prov{{display:inline-block;border-radius:4px;padding:0 5px;font-size:9.5px;font-weight:700;vertical-align:middle}}
 .p-obs{{background:#E7F4E8;color:#1E7B34}} .p-user{{background:#E8F0FB;color:#1B365D}}
 .p-est{{background:#FFF3CD;color:#7a5d00}} .p-asm{{background:#FDE8D2;color:#8a4b00}}
 .p-res{{background:#EDE7F6;color:#4A2E85}} .p-mod{{background:#E4E7EB;color:#333}}
 .note{{font-size:10.5px;color:#6B7280;line-height:1.5}}
 .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
 .grid2 .card{{margin:0}}
 .bar-row{{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px}}
 .bar-lbl{{width:150px;flex:none;text-align:right;color:#334}}
 .bar-track{{flex:1;background:#eef1f6;border-radius:6px;height:14px;overflow:hidden}}
 .bar-fill{{height:14px;border-radius:6px;background:#2E75B6}}
 .bar-fill.sub{{background:#B7950B}} .bar-fill.ref{{background:#1E7B34}}
 .bar-val{{width:120px;flex:none;font-weight:700;color:#1B365D}}
 .risk{{border-left:5px solid #B7950B;background:#FFFDF5;border-radius:6px;padding:9px 12px;margin:8px 0}}
 .risk.high{{border-left-color:#D97706}} .risk.very_high{{border-left-color:#C00000;background:#FFF7F7}}
 .risk.low{{border-left-color:#1E7B34;background:#F7FCF8}}
 .risk .rt{{font-weight:800;font-size:13px;color:#1B365D}}
 .risk dl{{margin:6px 0 0;font-size:11.5px}} .risk dt{{font-weight:700;color:#555;margin-top:4px}}
 .risk dd{{margin:1px 0 0 0;color:#333;line-height:1.45}}
 .cond{{background:#F7F9FC;border:1px solid #d6dee9;border-radius:8px;padding:10px;margin-top:8px;font-size:12.5px;line-height:1.6}}
 .caveat{{background:#FFF9E6;border:1px solid #E0B93A;border-left:5px solid #B7950B;border-radius:8px;padding:10px 12px;margin-top:8px;font-size:12.5px;line-height:1.6;color:#4A3B00}}
 .caveat ul{{margin:6px 0 0 18px;padding:0}} .caveat li{{margin:2px 0}}
 .sec-num{{display:inline-block;background:#1B365D;color:#fff;border-radius:4px;padding:0 6px;margin-right:6px;font-size:11px}}
 @media (max-width:720px){{
   .wrap{{padding:10px}} .grid2{{grid-template-columns:1fr}}
   .kpis{{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}}
   table{{font-size:11px}} td,th{{padding:3px 5px}}
   .bar-lbl{{width:96px}} .bar-val{{width:88px;font-size:11px}}
   .verdict{{font-size:18px}} header h1{{font-size:17px}}
 }}
 @media print{{ body{{background:#fff}} .card{{box-shadow:none;border:1px solid #d6dee9}} }}
</style></head><body><div class="wrap">
<header><h1>物業分析報告 / Property Analysis Report</h1>
<p>{_txt(prop.get('estate') or meta.get('url'))} ｜ 目的: {'收租投資 Rental' if is_rental else '自住置業 Owner-Occupier'}
 ｜ engine v{_txt(report.get('engine_version'))} ｜ {_txt(meta.get('generated_note'))}
 ｜ AI 敘述: {'已附加（僅解釋）' if report.get('llm_narrative') else _ai_short(report.get('llm_narrative_error'))}</p></header>

<div class="verdict" style="background:{color}">
  <div class="vb">最終判決 FINAL VERDICT</div>{label_full}
  <div class="why">{_x(v.get('why') or '')}</div>
</div>{_caveats_block(v)}"""]

    # ---------------- page-1 KPI board -------------------------------- #
    kpis = [
        _kpi("判決 Verdict", label_full,
             "risk: " + _txt(C.RISK_LEVEL_LABELS.get(risk_level, risk_level)),
             tone=("good" if label == "BUY" else "bad" if label == "PASS" else "warn")),
        _kpi("叫價 Asking Price", _hk(prop.get("price")),
             (_hk(prop.get("price_psf")) + "/呎") if prop.get("price_psf") else ""),
        _kpi("公允價值 Fair Value", _hk(val.get("fair_value")),
             (_pct(prem, 1, signed=True) + " 溢價") if prem is not None else "Insufficient Data",
             tone=("good" if (prem is not None and prem < 0) else
                   "bad" if (prem is not None and prem > 0.05) else "warn")),
        _kpi("交易所需現金 Cash Required", _hk(acq.get("total_initial_cash_required")),
             "首期 " + _hk(acq.get("down_payment"))),
        _kpi("按揭貸款 Mortgage", _hk(uw.get("loan_amount")), "LTV " + _pct(fin.get("ltv"), 0)),
        _kpi("每月供款 Monthly Payment", _hk(uw.get("monthly_payment")),
             ("DSR " + _pct(uw.get("dsr"))) if uw.get("dsr") is not None
             else ("DSCR " + _num(uw.get("min_dscr"), 2, "x"))),
        _kpi("淨租金回報 Net Yield", _pct(uw.get("net_yield")) if is_rental else "N/A",
             ("毛 " + _pct(uw.get("gross_yield"))) if is_rental else "自住模式"),
        _kpi("基準 IRR Base IRR", _pct(summ.get("base_irr"), 1), "BASE 情境"),
        _kpi("壓力 IRR Stress IRR", _pct(summ.get("stress_irr"), 1), "BEAR 情境"),
        _kpi("風險等級 Risk Level", C.RISK_LEVEL_LABELS.get(risk_level, "—"),
             f"{len(risk.get('risks') or [])} 項已偵測",
             tone=("bad" if risk_level in ("high", "very_high") else "good")),
    ]
    h.append(f'<div class="kpis">{"".join(kpis)}</div>')

    cond = v.get("conditional") or {}
    buy_if = cond.get("buy_if") or {}
    pass_if = cond.get("pass_if") or {}
    changes = v.get("what_would_change") or []

    # ---------------- 1. executive summary ---------------------------- #
    body = [f"<p style='font-size:13px;line-height:1.6'>{_x(v.get('why') or '')}</p>",
            "<div class='cond'><b>條件式決策 CONDITIONAL DECISION</b><br>"
            f"<b>BUY IF</b>：{_x(buy_if.get('expression_zh') or '—')}<br>"
            f"<b>PASS IF</b>：{_x(pass_if.get('expression_zh') or '—')}</div>"]
    if changes:
        body.append("<p style='font-size:12px;margin:8px 0 2px'><b>甚麼會改變判決 What would change the decision</b></p>"
                    "<ul class='note'>"
                    + "".join(f"<li>{_x(c['detail'])}</li>" for c in changes) + "</ul>")
    if v.get("missing_inputs"):
        body.append("<p class='note'>資料缺口 missing inputs：" + _x("、".join(v["missing_inputs"])) + "</p>")
    h.append(_card("<span class='sec-num'>1</span>執行摘要 Executive Summary", "".join(body)))

    # ---------------- 2. property snapshot ---------------------------- #
    ext = report.get("extraction") or {}
    rows = [
        ("屋苑 / 大廈 Estate / Building",
         f"{_txt(prop.get('estate'))} / {_txt(prop.get('building'))}"),
        ("地區 District", _txt(prop.get("district"))),
        ("間隔 Layout", _txt(prop.get("layout"))),
        ("樓層 / 座向 / 景觀",
         f"{_txt(prop.get('floor'))} / {_txt(prop.get('orientation'))} / {_txt(prop.get('view'))}"),
        ("叫價 Asking Price", f"<b>{_hk(prop.get('price'))}</b> {_prov('User Input')}"),
        ("實用面積 Saleable Area", f"{_num(prop.get('usable_area_sqft'), 0)} 呎"),
        ("建築面積 Gross Area", f"{_num(prop.get('gross_area_sqft'), 0)} 呎"),
        ("呎價 Price / sqft", _hk(prop.get("price_psf"))),
        ("樓齡 Building Age",
         f"{_num(prop.get('building_age'), 0)} 年"
         + (" " + _prov("User Input") if prop.get("building_age") is not None else "")),
        ("每月管理費 Management Fee", _hk(prop.get("monthly_mgmt_rates"))),
        ("每月差餉地租 Rates", _hk(prop.get("rates_amount"))),
        ("特殊條件 Special Features", _txt(prop.get("special_features"))),
        ("價格來源 Price Source", _txt(prop.get("price_source"))),
        ("樓盤連結 Source URL",
         f"<span style='font-size:10.5px;overflow-wrap:anywhere'>{_txt(prop.get('source_url'))}</span>"),
    ]
    missing = ext.get("missing_fields") or []
    body = _table(["項目 Item", "數值 Value"], [[(a, "l"), (b, "l")] for a, b in rows])
    body += (f"<p class='note'>未能抽取欄位（系統不猜測 not guessed）："
             f"{_x(', '.join(missing)) if missing else '無'}</p>")
    h.append(_card("<span class='sec-num'>2</span>物業概覽 Property Snapshot", body))

    # ---------------- 3. financing ------------------------------------ #
    rows = [
        ("貸款成數 LTV", _pct(fin.get("ltv"))),
        ("按揭年利率 Mortgage Rate", _pct(fin.get("mortgage_rate"), 3)),
        ("還款年期 Tenure", f"{_num(fin.get('tenure_years'), 0)} 年"),
        ("還款方式 Amortization", _txt(fin.get("amortization"))),
        ("貸款額 Loan Amount", f"<b>{_hk(uw.get('loan_amount'))}</b>"),
        ("首期 Down Payment", _hk(uw.get("down_payment"))),
        ("每月供款 Monthly Payment", f"<b>{_hk(uw.get('monthly_payment'))}</b>"),
        ("每月持樓成本 Housing Cost", _hk(uw.get("monthly_housing_cost"))),
        ("每月淨收入 Net Income", _hk(pf.get("monthly_net_income"))),
        ("其他固定收入 Other Income", _hk(pf.get("other_recurring_income"))),
        ("現有每月債務 Existing Debt", _hk(pf.get("existing_monthly_debt"))),
        ("DSR 供款／入息",
         (f"<b>{_pct(uw.get('dsr'))}</b> {_prov('Model Output')}"
          if uw.get("dsr") is not None else "—")),
        ("每月盈餘 Monthly Surplus",
         _hk(uw.get("monthly_surplus")) if uw.get("monthly_surplus") is not None else "—"),
        ("可負擔分數 Affordability Score",
         f"{uw.get('affordability_score', 0):.1f}/10" if uw.get("affordability_score") is not None else "—"),
    ]
    body = _table(["項目 Item", "數值 Value"], [[(a, "l"), (b, "r")] for a, b in rows])
    base_band = (rs.get("base_affordability") or {}).get("band")
    stress_band = (rs.get("stress_affordability") or {}).get("band")
    body += (f"<p class='note'>個人舒適度 Personal financial comfort（DSR 基準）："
             f"<b>{_txt(base_band)}</b> ｜ 壓力情境：<b>{_txt(stress_band)}</b> {_prov('Model Output')}</p>"
             f"<p class='note'>{_x(C.INDICATIVE_FINANCING_NOTE)}</p>")
    h.append(_card("<span class='sec-num'>3</span>融資 Financing", body,
                   "銀行可負擔能力 ≠ 個人財務舒適度。"))

    # ---------------- 4. acquisition cost ----------------------------- #
    lines = acq.get("lines") or []
    rows = [[(l["label_zh"], "l"), (_hk(l["amount"]), "r"),
             (_pct(l["pct_of_price"]), "r"), (_prov(l["provenance"]), "c")] for l in lines]
    rows.append([("<b>交易所需總現金 Total Initial Cash Required</b>", "l"),
                 (f"<b>{_hk(acq.get('total_initial_cash_required'))}</b>", "r"),
                 (f"<b>{_pct(acq.get('total_pct_of_price'))}</b>", "r"),
                 (_prov("Model Output"), "c")])
    body = _table(["項目 Item", "金額 Amount", "佔樓價 % of price", "來源"], rows)
    body += (f"<p class='note'><b>首期 Down Payment {_hk(acq.get('down_payment'))}</b>"
             f" 與 <b>Total Initial Cash Required {_hk(acq.get('total_initial_cash_required'))}</b>"
             f" 是兩個不同數字。</p>")
    if acq.get("recommended_reserve"):
        body += (f"<p class='note'>建議初始儲備（{acq.get('reserve_months')} 個月持樓成本）"
                 f"{_hk(acq.get('recommended_reserve'))} {_prov('Assumption')}；"
                 f"連同建議儲備的總現金 {_hk(acq.get('total_with_recommended_reserve'))}。</p>")
    if acq.get("mortgage_insurance_estimate"):
        body += (f"<p class='note'>LTV &gt; 70%：按揭保險估算 "
                 f"{_hk(acq.get('mortgage_insurance_estimate'))} {_prov('Assumption')}"
                 f"（未計入上方現金需求，需向銀行核實）。</p>")
    h.append(_card("<span class='sec-num'>4</span>交易成本 Acquisition Cost", body))

    # ---------------- 5. rental economics ----------------------------- #
    if is_rental:
        rows = [
            ("每月租金 Monthly Rent", _hk(rental.get("monthly_rent"))),
            ("毛租金收入 Gross Rental Income (年)", _hk(uw.get("gross_rental_income_y1"))),
            ("有效租金收入 EGI", _hk(uw.get("effective_gross_income_y1"))),
            ("差餉＋地租 Rates", "−" + _hk(uw.get("rates_y1"))),
            ("管理＋維修 Opex", "−" + _hk(uw.get("opex_y1"))),
            ("稅前 NOI Pre-tax NOI", f"<b>{_hk(uw.get('noi_pre_tax_y1'))}</b>"),
            ("物業稅 Property Tax", "−" + _hk(uw.get("property_tax_y1"))),
            ("稅後 NOI After-tax NOI", f"<b>{_hk(uw.get('noi_after_tax_y1'))}</b>"),
            ("年度供款 Debt Service", "−" + _hk(uw.get("annual_debt_service"))),
            ("年度現金流 Annual Cash Flow", f"<b>{_hk(uw.get('annual_cash_flow_y1'))}</b>"),
            ("每月現金流 Monthly Cash Flow", _hk(uw.get("monthly_cash_flow_y1"))),
            ("毛租金回報 Gross Yield", _pct(uw.get("gross_yield"))),
            ("淨租金回報 Net Yield", f"<b>{_pct(uw.get('net_yield'))}</b>"),
            ("入市資本化率 Going-in Cap Rate", _pct(uw.get("cap_rate_going_in"))),
            ("現金回報 Cash-on-Cash", _pct(uw.get("cash_on_cash"))),
            ("最低 DSCR Min DSCR", _num(uw.get("min_dscr"), 2, "x")),
            ("槓桿 IRR Levered IRR", _pct(uw.get("levered_irr"), 1)),
            ("無槓桿 IRR Unlevered IRR", _pct(uw.get("unlevered_irr"), 1)),
            ("權益倍數 Equity Multiple", _num(uw.get("equity_multiple"), 2, "x")),
            ("退出價值 Exit Value", _hk(uw.get("exit_value"))),
            ("退出淨收益 Net Exit Proceeds", _hk(uw.get("exit_proceeds_net"))),
        ]
        body = _table(["項目 Item", "數值 Value"], [[(a, "l"), (b, "r")] for a, b in rows])
        be = uw.get("break_even_rent") or {}
        be2 = uw.get("break_even_rent_dscr_target") or {}
        body += (f"<p class='note'>損益兩平租金 Break-even rent（DSCR 1.00x）："
                 f"<b>{_hk(be.get('monthly_rent'))}/月</b>；"
                 f"DSCR 1.25x：<b>{_hk(be2.get('monthly_rent'))}/月</b>；"
                 f"損益兩平樓價 Break-even price：<b>{_hk(uw.get('break_even_price'))}</b>（1.00x）／"
                 f"<b>{_hk(uw.get('break_even_price_dscr_target'))}</b>（1.25x）。"
                 f" {_prov('Model Output')}</p>")
        rows2 = [[(f"{i+1}", "c"), (_hk(x), "r")]
                 for i, x in enumerate(uw.get("cash_flow_by_year") or [])]
        if rows2:
            body += ("<p style='font-size:12px;margin:10px 0 2px'><b>年度現金流（槓桿，含退出）</b></p>"
                     + _table(["年度 Year", "現金流 Cash flow"], rows2))
        h.append(_card("<span class='sec-num'>5</span>收租經濟 Rental Economics", body))
    else:
        h.append(_card("<span class='sec-num'>5</span>收租經濟 Rental Economics",
                       "<p class='note'>自住模式不適用（N/A）。若要分析租金回報、DSCR、"
                       "現金流與 IRR，請以「收租投資 Rental」模式重新分析。</p>"))

    # ---------------- 6. valuation ------------------------------------ #
    cls_label = val.get("classification_label") or "—"
    tone = {"undervalued": "ok", "very_undervalued": "ok", "fair": "info",
            "expensive": "warn", "very_expensive": ""}.get(val.get("classification"), "warn")
    body = [f"<p class='chip {tone}'>{_x(cls_label)}</p>",
            _table(["項目 Item", "數值 Value"], [[(a, "l"), (b, "r")] for a, b in [
                ("叫價 Asking Price", f"<b>{_hk(val.get('asking_price'))}</b>"),
                ("叫價呎價 Asking PPSF", _hk(val.get("asking_psf"))),
                ("估算公允價值 Fair Value",
                 f"<b>{_hk(val.get('fair_value'))}</b> "
                 f"{_prov('Model Output' if val.get('fair_value') else None)}"),
                ("公允價值呎價 Fair Value PPSF", _hk(val.get("fair_value_psf"))),
                ("公允價值範圍 Fair Value Range",
                 (f"{_hk(val['fair_value_range'][0])} – {_hk(val['fair_value_range'][1])}"
                  if val.get("fair_value_range") else "—")),
                ("溢價／折讓 Premium / Discount",
                 f"<b>{_pct(val.get('premium_pct'), 1, signed=True)}</b>"),
                ("溢價金額 Premium (HK$)", _hk(val.get("premium_hk"))),
                ("建議目標買入價 Buy Target", _hk(val.get("buy_target_price"))),
                ("可比個數 Comparable Count", _num(val.get("comparable_count"), 0)),
                ("證據信心 Evidence Confidence", _txt(val.get("confidence"))),
                ("證據來源 Evidence Source", _txt(val.get("evidence_source"))),
            ]])]
    breakdown = val.get("evidence_breakdown") or []
    if breakdown:
        bl_rows = []
        for e in breakdown:
            dev = e.get("vs_benchmark_pct")
            is_user = str(e.get("source")) == "user-input"
            bl_rows.append([(_x(e.get("title") or "—"), "l"),
                            (_hk(e.get("price")), "r"),
                            (_num(e.get("usable_area_sqft"), 0) + " 呎", "r"),
                            (_hk(e.get("psf")) + "/呎", "r"),
                            (f"<b>{_pct(dev, 1, signed=True)}</b>" if dev is not None else "—",
                             "r"),
                            (_x(e.get("date") or "—"), "c"),
                            (_prov(C.USER if is_user else C.RESEARCH), "c")])
        body.append("<p style='font-size:12px;margin:10px 0 0'>"
                    "<b>逐筆可比成交 Comparable evidence（呎價 vs 基準）</b></p>")
        body.append(_table(["項目", "價格", "面積", "呎價", "相對基準", "日期", "來源"],
                           bl_rows))
        body.append("<p class='note'>基準為上列呎價的中位數（經同屋苑／同間隔調整）。"
                    "某一筆偏離基準超過 ±15% 通常代表特殊成交、不同樓層或輸入錯誤，"
                    "值得先核實再採用。</p>")
        outliers = [e for e in breakdown
                    if e.get("vs_benchmark_pct") is not None
                    and abs(e["vs_benchmark_pct"]) > 0.15]
        if outliers:
            body.append("<p class='note' style='color:#8a6d00'>⚠ 可疑可比成交（偏離基準 "
                        "±15% 以上）："
                        + "；".join(f"{_x(e.get('title') or '—')}"
                                    f"（{_pct(e.get('vs_benchmark_pct'), 1, signed=True)}）"
                                    for e in outliers)
                        + "。請先核實，否則公允價值會被拉偏。</p>")
    if val.get("insufficient_reason"):
        body.append(f"<p class='note'><b>Insufficient Data</b>：{_x(val['insufficient_reason'])}"
                    f" 系統嚴禁自行捏造公允價值。</p>")
    ia = val.get("income_approach_crosscheck")
    if ia:
        body.append(f"<p class='note'>收益法交叉檢查 Income approach cross-check（僅供參考）："
                    f"{_hk(ia.get('value'))}，使用 cap rate {_pct(ia.get('cap_rate_used'), 2)}。"
                    f" {_prov('Assumption')} {_x(ia.get('note') or '')}</p>")
    if val.get("subject_asking_psf") and val.get("adjusted_benchmark_psf"):
        mx = max(val["subject_asking_psf"], val["adjusted_benchmark_psf"],
                 val.get("median_psf") or 0) * 1.05
        for lbl, v_, cls in (("標的 Asking", val["subject_asking_psf"], "sub"),
                             ("調整後基準 Adjusted", val["adjusted_benchmark_psf"], "ref"),
                             ("中位 Median", val.get("median_psf"), "")):
            if not v_:
                continue
            w = max(2.0, v_ / mx * 100)
            body.append(f"<div class='bar-row'><div class='bar-lbl'>{lbl}</div>"
                        f"<div class='bar-track'><div class='bar-fill {cls}' style='width:{w:.1f}%'></div></div>"
                        f"<div class='bar-val'>HK${v_:,.0f}/呎</div></div>")
    h.append(_card("<span class='sec-num'>6</span>估值 Valuation", "".join(body)))

    # ---------------- 7. scenario analysis ---------------------------- #
    if scen:
        hdr = ["情境", "租金/月", "利率", "退出 cap", "物業價值", "月供",
               "年度現金流", "淨回報", "IRR", "權益倍數"]
        rows = []
        for name in (scen_meta.get("order") or C.SCENARIO_ORDER):
            s = scen.get(name)
            if not s:
                continue
            rows.append([
                (f"<b>{_x(s['label'])}</b><br><span class='note'>{_x(s['label_zh'])}</span>", "l"),
                (_hk(s.get("rent_monthly")), "r"),
                (_pct(s.get("interest_rate"), 3), "r"),
                (_pct(s.get("exit_cap"), 2), "r"),
                (_hk(s.get("property_value")), "r"),
                (_hk(s.get("mortgage_payment")), "r"),
                (_hk(s.get("annual_cash_flow")), "r"),
                (_pct(s.get("net_yield")), "r"),
                (_pct(s.get("irr"), 1), "r"),
                (_num(s.get("equity_multiple"), 2, "x"), "r"),
            ])
        body = _table(hdr, rows)
        notes = "".join(f"<li>{_x(a)}</li>" for a in (scen_meta.get("assumptions") or []))
        body += f"<p class='note'>情境假設（系統設定，非前端可改）：</p><ul class='note'>{notes}</ul>"
        for note in (scen_meta.get("notes") or []):
            body += f"<p class='note'>{_x(note)}</p>"
        body += (f"<p class='note'>配置來源 {_x(scen_meta.get('config_source') or '')} "
                 f"{_prov('Assumption')}</p>")
        h.append(_card("<span class='sec-num'>7</span>情境分析 Scenario Analysis", body))

    # ---------------- 8. interest-rate stress ------------------------- #
    if rs.get("rows"):
        base_a = rs.get("base_affordability") or {}
        stress_a = rs.get("stress_affordability") or {}
        pair = [("利率 Rate", _pct(base_a.get("rate"), 3), _pct(stress_a.get("rate"), 3)),
                ("每月供款 Monthly payment", _hk(base_a.get("monthly_payment")),
                 _hk(stress_a.get("monthly_payment"))),
                ("DSR", _pct(base_a.get("dsr")), _pct(stress_a.get("dsr"))),
                ("每月盈餘 Monthly surplus", _hk(base_a.get("monthly_surplus")),
                 _hk(stress_a.get("monthly_surplus"))),
                ("年度現金流 Annual cash flow", _hk(base_a.get("annual_cash_flow")),
                 _hk(stress_a.get("annual_cash_flow"))),
                ("Min DSCR", _num(base_a.get("min_dscr"), 2, "x"),
                 _num(stress_a.get("min_dscr"), 2, "x")),
                ("舒適度 Band", _txt(base_a.get("band")), _txt(stress_a.get("band")))]
        body = [f"<p class='note'>{_x(rs.get('framing') or '')}</p>",
                _table(["項目 Item", "基準 Base", "壓力 Stress（+2.00pp）"],
                       [[(a, "l"), (b, "r"), (c, "r")] for a, b, c in pair])]
        rows = []
        for r_ in rs["rows"]:
            rows.append([
                (f"<b>{_x(r_['scenario'])}</b>", "l"),
                (_pct(r_["rate"], 3), "r"),
                (_hk(r_.get("monthly_payment")), "r"),
                (_pct(r_.get("dsr")), "r"),
                (_hk(r_.get("annual_cash_flow")), "r"),
                (_hk(r_.get("annual_debt_service")), "r"),
                (_num(r_.get("min_dscr"), 2, "x"), "r"),
                (_pct(r_.get("payment_increase_pct"), 1, signed=True), "r"),
                (_hk(r_.get("cash_flow_deterioration"))
                 if r_.get("cash_flow_deterioration") is not None
                 else _hk(r_.get("surplus_deterioration")), "r"),
            ])
        body.append(_table(["情境", "利率", "月供", "DSR", "年度現金流", "年度供款",
                            "Min DSCR", "月供增幅", "現金流惡化"], rows))
        h.append(_card("<span class='sec-num'>8</span>利率壓力測試 Interest Rate Stress Test",
                       "".join(body),
                       "Mortgage Affordability Stress Test / Interest Rate Sensitivity — "
                       "並非銀行批核規則"))

    # ---------------- 9. market regime -------------------------------- #
    body = [f"<p><b>{_txt(reg.get('label'))}</b> "
            f"<span class='chip info'>score {_num(reg.get('score'), 0)}</span>"
            f" <span class='chip info'>confidence {_txt(reg.get('confidence'))}</span>"
            f" <span class='chip info'>{_num(reg.get('topics_assessed'), 0)}/"
            f"{_num(reg.get('topics_total'), 0)} 議題有訊號</span></p>",
            _table(["面向", "分析"], [[(a, "l"), (b, "l")] for a, b in [
                ("目前情境 Current regime", _txt(reg.get("current_regime"))),
                ("物業影響 Property impact", _txt(reg.get("property_impact"))),
                ("下行含意 Downside implication", _txt(reg.get("downside_implication"))),
                ("試算含意 Underwriting implication", _txt(reg.get("underwriting_implication"))),
            ]])]
    if reg.get("signals"):
        rows = []
        for s in reg["signals"]:
            ev = "；".join(str(e.get("title")) for e in (s.get("evidence") or [])[:2])
            chip = "ok" if s["direction"] == "positive" else ("warn" if s["direction"] == "mixed" else "")
            rows.append([(_txt(s["topic"]), "l"),
                         (f"<span class='chip {chip}'>{_x(s['direction'])}</span>", "c"),
                         (_num(s["weight"], 0), "c"),
                         (f"<span class='note'>{_x(ev)}</span>", "l")])
        body.append(_table(["議題 Topic", "方向", "權重", "研究來源（與模型輸出分開）"], rows))
    body.append(f"<p class='note'>{_x(reg.get('note') or '')} 研究來源 {_prov('Research Approximation')}；"
                f"分類結果 {_prov('Model Output')}；playbook 文字 {_prov('Assumption')}</p>")
    h.append(_card("<span class='sec-num'>9</span>市場週期 Market Regime", "".join(body)))

    # ---------------- 10. risk analysis ------------------------------- #
    body = [f"<p><b>風險等級 Risk Level：</b>{_risk_chip(risk_level)} "
            f"<span class='note'>已執行 {_num(risk.get('checks_run'), 0)} 項檢查，"
            f"觸發 {_num(len(risk.get('risks') or []), 0)} 項</span></p>"]
    top = risk.get("top_risks") or []
    if top:
        body.append("<p style='font-size:12.5px;margin:8px 0 0'><b>三大風險 Top 3 Risks</b></p>")
        for r_ in top:
            body.append(
                f"<div class='risk {r_['level']}'><div class='rt'>{_x(r_['label'])} "
                f"{_risk_chip(r_['level'])}</div>"
                f"<dl><dt>風險 Risk</dt><dd>{_x(r_['evidence'])}</dd>"
                f"<dt>機制 Mechanism</dt><dd>{_x(r_['mechanism'])}</dd>"
                f"<dt>財務影響 Financial impact</dt><dd>{_x(r_['impact'])}</dd>"
                f"<dt>緩解 Mitigation</dt><dd>{_x(r_['mitigation'])}</dd></dl></div>")
    others = [r_ for r_ in (risk.get("risks") or []) if r_ not in top]
    if others:
        body.append("<p style='font-size:12px;margin:10px 0 0'><b>其他已偵測風險</b></p>")
        body.append(_table(["風險", "等級", "證據", "緩解"],
                           [[(_x(r_["label"]), "l"), (_risk_chip(r_["level"]), "c"),
                             (_x(r_["evidence"]), "l"), (_x(r_["mitigation"]), "l")]
                            for r_ in others]))
    if not risk.get("risks"):
        body.append("<p class='note'>未偵測到中等或以上風險。</p>")
    body.append(f"<p class='note'>{_x(risk.get('note') or '')} 門檻來源 "
                f"{_x(risk.get('thresholds_source') or '')}</p>")
    h.append(_card("<span class='sec-num'>10</span>風險分析 Risk Analysis", "".join(body)))

    # ---------------- 11. transaction boundary ------------------------ #
    mbp = bnd.get("maximum_buy_price") or {}
    tr = bnd.get("target_rent") or {}
    ml = bnd.get("maximum_comfortable_ltv") or {}
    ice = bnd.get("interest_rate_ceiling") or {}
    my = bnd.get("minimum_rental_yield") or {}
    dbt = bnd.get("downside_boundary") or {}
    body = ['<div class="kpis">' + "".join([
        _kpi("最大買入價 Maximum Buy Price", _hk(mbp.get("value")),
             ("較叫價 " + _pct(mbp.get("discount_to_asking_pct"), 1, signed=True))
             if mbp.get("discount_to_asking_pct") is not None else "",
             tone=("bad" if (mbp.get("value") and prop.get("price")
                             and prop["price"] > mbp["value"]) else "good")),
        _kpi("目標租金 Target Rent",
             (_hk(tr.get("value")) + "/月") if tr.get("value") else "N/A",
             ("較現時 " + _pct(tr.get("gap_pct"), 1, signed=True))
             if tr.get("gap_pct") is not None else "自住模式"),
        _kpi("最高舒適 LTV Max Comfortable LTV", _pct(ml.get("value"), 0),
             "現時 " + _pct(ml.get("current_ltv"), 0)),
        _kpi("利率上限 Interest-rate Ceiling", _pct(ice.get("value"), 3),
             ("距現時 +" + _num(ice.get("headroom_pp"), 2, "pp"))
             if ice.get("headroom_pp") is not None else ""),
        _kpi("最低租金回報 Min Rental Yield", _pct(my.get("required"), 2),
             ("現時 " + _pct(my.get("achieved"), 2)
              + ("（達標）" if my.get("meets_requirement") else "（未達標）"))
             if my.get("achieved") is not None else "自住模式"),
    ]) + "</div>"]

    for title, blk in (("最大買入價推導 Maximum Buy Price derivation", mbp),
                       ("目標租金推導 Target Rent derivation", tr),
                       ("最高舒適 LTV 推導 Maximum Comfortable LTV", ml)):
        cons = blk.get("constraints") or []
        if not cons:
            continue
        rows = [[(_x(c["label"]), "l"), (_hk(c["value"]), "r"), (_x(c["basis"]), "l"),
                 ("<b>binding</b>" if c["key"] == blk.get("binding_constraint") else "", "c")]
                for c in cons]
        body.append(f"<p style='font-size:12px;margin:10px 0 0'><b>{_x(title)}</b></p>")
        body.append(_table(["約束 Constraint", "數值", "依據", "綁定"], rows))
    body.append(f"<p class='note'>利率上限定義：{_x(ice.get('label') or '')}"
                f"（{_x(ice.get('basis') or '')}）。{_x(ice.get('framing') or '')}</p>")
    body.append(f"<p class='note'>最低租金回報依據：{_x(my.get('basis') or '')}</p>")
    body.append(_table(["下行界線 Downside boundary", "數值", "說明"],
                       [[(a, "l"), (b, "r"), (_x(c or ""), "l")] for a, b, c in [
                           ("權益緩衝 Equity cushion", _pct(dbt.get("equity_cushion_pct")),
                            dbt.get("equity_cushion_note")),
                           ("損益兩平空置率 Break-even vacancy",
                            _pct(dbt.get("break_even_vacancy")),
                            (f"現時空置率 {_pct(dbt.get('current_vacancy'))}"
                             if dbt.get("current_vacancy") is not None else "")),
                           ("損益兩平利率 Break-even rate",
                            _pct(dbt.get("break_even_rate"), 3), dbt.get("note")),
                       ]]))
    body.append(f"<p class='note'>{_x(bnd.get('note') or '')} {_prov('Model Output')}</p>")
    h.append(_card("<span class='sec-num'>11</span>交易界線 Transaction Boundary", "".join(body),
                   "本節回答的不是「值不值得買」，而是「在甚麼條件下值得買」。"))

    # ---------------- 12. investment verdict -------------------------- #
    body = [f"<div class='verdict' style='background:{color};margin-top:0'>"
            f"<div class='vb'>投資判決 INVESTMENT VERDICT</div>{label_full}"
            f"<div class='why'>{_x(v.get('why') or '')}</div></div>",
            _caveats_block(v)]
    kn = v.get("key_numbers") or {}
    body.append(_table(["關鍵數字 Key numbers", "數值"], [[(a, "l"), (b, "r")] for a, b in [
        ("叫價 Asking Price", _hk(kn.get("asking_price"))),
        ("公允價值 Fair Value", _hk(kn.get("fair_value"))),
        ("溢價 Premium", _pct(kn.get("premium_pct"), 1, signed=True)),
        ("交易所需現金 Cash Required", _hk(kn.get("initial_cash_required"))),
        ("按揭貸款 Mortgage", _hk(kn.get("mortgage_amount"))),
        ("每月供款 Monthly Payment", _hk(kn.get("monthly_payment"))),
        ("淨租金回報 Net Yield", _pct(kn.get("net_yield"))),
        ("基準 IRR Base IRR", _pct(kn.get("base_irr"), 1)),
        ("壓力 IRR Stress IRR", _pct(kn.get("stress_irr"), 1)),
        ("風險等級 Risk Level",
         _txt(C.RISK_LEVEL_LABELS.get(kn.get("risk_level"), kn.get("risk_level")))),
    ]]))
    checks = v.get("buy_checks") or []
    if checks:
        body.append("<p style='font-size:12px;margin:10px 0 0'><b>買入條件檢查 BUY checklist</b></p>")
        body.append(_table(["條件", "結果", "說明"],
                           [[(_x(c["check"]), "l"),
                             (f"<span class='chip {'ok' if c['ok'] else ''}'>"
                              f"{'PASS' if c['ok'] else 'FAIL'}</span>", "c"),
                             (_x(c["detail"]), "l")] for c in checks]))
    if v.get("blockers"):
        body.append("<p style='font-size:12px;margin:10px 0 0'><b>否決條件 PASS blockers</b></p>"
                    "<ul class='note'>"
                    + "".join(f"<li><b>{_x(b['code'])}</b>：{_x(b['detail'])}</li>"
                              for b in v["blockers"]) + "</ul>")
    body.append(f"<div class='cond'><b>BUY IF</b>：{_x(buy_if.get('expression_zh') or '—')}<br>"
                f"<b>PASS IF</b>：{_x(pass_if.get('expression_zh') or '—')}</div>")
    if changes:
        body.append("<p style='font-size:12px;margin:10px 0 0'>"
                    "<b>甚麼會改變判決 What would change the decision</b></p>"
                    "<ul class='note'>"
                    + "".join(f"<li>{_x(c['detail'])}</li>" for c in changes) + "</ul>")
    body.append(f"<p class='note'>判決依據 {_x(v.get('decision_basis') or '')} "
                f"{_prov('Model Output')}</p>")
    h.append(_card("<span class='sec-num'>12</span>投資判決 Investment Verdict", "".join(body)))

    # ---------------- 13. data quality -------------------------------- #
    counts = dq.get("provenance_counts") or {}
    body = [_table(["資料性質 Provenance", "項目數"],
                   [[(_prov(k), "c"), (_num(v_, 0), "r")] for k, v_ in counts.items()]),
            f"<p class='note'>硬證據（Observed／User Input／Model Output）"
            f"{_num(dq.get('hard_evidence_items'), 0)} 項；軟資料（Estimated／Assumption／"
            f"Research Approximation）{_num(dq.get('soft_or_assumed_items'), 0)} 項；"
            f"證據比率 {_pct(dq.get('evidence_ratio'))}。{_x(dq.get('note') or '')}</p>"]
    if dq.get("unresolved_inputs"):
        body.append(f"<p class='note'>未取得欄位：{_x(', '.join(dq['unresolved_inputs']))}</p>")
    if dq.get("warnings"):
        body.append("<ul class='note'>"
                    + "".join(f"<li>{_x(w)}</li>" for w in dq["warnings"]) + "</ul>")
    grp = report.get("provenance_grouped") or {}
    for pname in C.PROVENANCE_ORDER:
        items = grp.get(pname) or []
        if not items:
            continue
        body.append(f"<p style='font-size:12px;margin:10px 0 2px'>{_prov(pname)} "
                    f"<span class='note'>{len(items)} 項</span></p>")
        body.append(_table(["項目", "數值", "依據", "信心"],
                           [[(_x(i["label"]), "l"),
                             (_hk(i["value"]) if isinstance(i["value"], (int, float))
                              else _txt(i["value"]), "r"),
                             (_x(i.get("basis") or ""), "l"),
                             (_txt(i.get("confidence")), "c")] for i in items]))
    body.append("<p style='font-size:12px;margin:10px 0 2px'><b>模型假設 Assumptions</b></p>"
                "<ul class='note'>"
                + "".join(f"<li>{_x(a)}</li>" for a in (report.get("assumptions") or []))
                + f"<li>{_x(report.get('disclaimer') or '')}</li></ul>")
    h.append(_card("<span class='sec-num'>13</span>資料品質與假設 Data Quality &amp; Assumptions",
                   "".join(body)))

    # ---------------- 14. sources ------------------------------------- #
    def _src_table(entries, title):
        if not entries:
            return ""
        rows = []
        for e in entries:
            d = e.get("data") or {}
            val_txt = " / ".join(f"{k}={v}" for k, v in d.items()
                                 if not str(k).startswith("subject") and v not in (None, ""))
            rows.append([(_x(e.get("title") or ""), "l"), (_x(e.get("source") or ""), "l"),
                         (_x(val_txt or "—"), "l"), (_x(e.get("date") or "—"), "c"),
                         (_x(e.get("confidence") or ""), "c")])
        return (f"<p style='font-size:12px;margin:8px 0 2px'><b>{_x(title)}</b></p>"
                + _table(["項目", "來源 Source", "資料 Data", "日期", "信心"], rows))

    src = (_src_table(report.get("official_context"),
                      "官方數據 Official (Observed) — HKMA / 土地註冊處")
           + _src_table((report.get("comparables") or {}).get("entries"), "可比成交 Comparables")
           + _src_table(report.get("rental_evidence"), "租金證據 Rental evidence")
           + _src_table(report.get("market_context"), "市場 Market")
           + _src_table(report.get("mortgage_context"), "按揭 Mortgage")
           + _src_table(report.get("policy_context"), "政策 Policy"))
    if not src:
        src = "<p class='note'>未接上任何即時資料來源。</p>"
    src += ("<p class='note'><b>官方數據（Observed）</b>來自 HKMA 住宅按揭統計調查與土地註冊處，"
            "經公開 API 取得，非估算、非新聞推測。其餘研究資料屬近似值（Research Approximation），"
            "與模型輸出（Model Output）分開列示；示範資料集以 source=demo-dataset 標示。</p>")
    h.append(_card("<span class='sec-num'>14</span>資料來源 Sources", src))

    # ---------------- pipeline + narrative ---------------------------- #
    steps = "".join(
        f"<span class='chip {'ok' if s['status'] == 'ok' else 'warn'}'>{_x(s['status'])}</span> "
        f"{_x(s['step'])} — <span class='note'>{_x(s.get('note') or '')}</span><br>"
        for s in (report.get("pipeline_log") or []))
    h.append(_card("執行流程 Pipeline（deterministic 優先，AI 其次）", steps or "—"))

    def blk(title, value):
        if isinstance(value, list):
            inner = "<ul>" + "".join(f"<li>{_x(x)}</li>" for x in value) + "</ul>"
        else:
            inner = _x(value or "—")
        return f"<p><b>{_x(title)}</b><br>{inner}</p>"

    narr = report.get("llm_narrative")
    if narr and isinstance(narr, dict):
        h.append(_card("AI 敘述（輔助解釋，不取代任何數字） / AI-assisted interpretation",
                       blk("執行摘要 Executive summary", narr.get("executive_summary"))
                       + blk("可負擔性 Affordability", narr.get("affordability_plain"))
                       + blk("市場週期觀點 Market regime view", narr.get("market_regime_view"))
                       + blk("估值觀點 Valuation view", narr.get("valuation_view"))
                       + blk("主要風險 Key risks", narr.get("key_risks"))
                       + blk("投資論點 Investment thesis", narr.get("investment_thesis"))
                       + blk("價格觀點 Price view", narr.get("price_view"))
                       + blk("建議下一步 Next steps", narr.get("next_steps"))
                       + blk("不確定性 Uncertainty", narr.get("uncertainty_note"))
                       + f"<p class='note'>{_x(narr.get('note') or '')}</p>"))
    else:
        reason = report.get("llm_narrative_error") or "unknown"
        if reason == "no_api_key":
            why = "未附加：這台機器沒有設定 DeepSeek API key（設定後即可附加）。"
        elif reason == "disabled_by_caller":
            why = "未附加：這次執行由呼叫方停用了 AI 敘述。"
        else:
            why = ("未附加：API key 已設定，但 AI 呼叫失敗（" + _x(str(reason)) + "）。"
                   "這屬外部服務問題，稍後重跑即可補上。")
        h.append(_card("AI 敘述 / AI-assisted interpretation",
                       f"<p class='note'>{why}"
                       "deterministic underwriting、估值、情境、風險與判決均已完整計算，不受影響。</p>"))

    h.append("<p class='note' style='margin:16px 4px 30px'>"
             f"AI Property Analyst · engine v{_txt(report.get('engine_version'))} · "
             f"schema v{_txt(report.get('schema_version'))} · "
             f"{_txt(report.get('disclaimer'))}</p>")
    h.append("</div></body></html>")
    return "".join(h)


def write_report(report: dict, path: str) -> str:
    html = render(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
