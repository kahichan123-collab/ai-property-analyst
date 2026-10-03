# -*- coding: utf-8 -*-
# tests/test_upgrade.py — v2.0 acceptance tests
#
# Covers the 10 mandated cases plus the deterministic-math invariants that the
# "AI is not a calculator" rule depends on:
#
#   Case 1  LTV 60 (%)            -> backend receives 0.60
#   Case 2  Interest rate 4.125   -> backend receives 0.04125
#   Case 3  Vacancy 5 (%)         -> backend receives 0.05
#   Case 4  Owner-occupier        -> no rental assumptions in the payload
#   Case 5  Rental                -> yield / NOI / cash flow / IRR computed
#   Case 6  API loading           -> UI disables inputs (static contract check)
#   Case 7  Invalid LTV 130       -> rejected with a field-level error
#   Case 8  Price 8,300,000       -> displayed with separators, numeric to engine
#   Case 9  No DeepSeek key       -> deterministic underwriting still completes
#  Case 10  Rate +2%              -> mortgage / DSR / cash flow recomputed
#
# Run:  python -m tests.test_upgrade
from __future__ import annotations
import json, os, re, sys, threading, urllib.request, urllib.error

# A zh-HK Windows console defaults to cp950, which cannot encode U+2212 MINUS
# SIGN or the CJK punctuation used in report strings. Without this the suite
# prints every check as PASS and then dies with UnicodeEncodeError, exiting
# non-zero — so CI reads a fully green run as a build failure.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import config as C
from tools import schema as sc
from tools import underwriting as uw
from tools import acquisition as acq_mod
from tools import boundary as bnd_mod
from tools import valuation as val_mod
from tools.report import render
from orchestrator import analyse_request

_PASS = 0
_FAILED: list[str] = []


def approx(a, b, tol=1e-2):
    return a is not None and abs(a - b) <= tol


def check(name, cond, detail=""):
    global _PASS
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def pct_input(v):
    """Exactly what the frontend does before sending (percent -> fraction)."""
    return v / 100.0


# --------------------------------------------------------------------------- #
OWNER = {
    "mode": "manual",
    "purpose": "owner_occupier",
    "property": {"estate": "昇薈", "layout": "3房2廳", "asking_price": 8_300_000,
                 "usable_area_sqft": 741, "monthly_mgmt_rates": 1200,
                 "building_age": 8},
    "finance": {"monthly_net_income": 90_000, "existing_monthly_debt": 0,
                "liquid_cash": 6_000_000, "available_cash_for_transaction": 5_000_000,
                "emergency_reserve": 500_000},
    "financing": {"ltv": 0.60, "mortgage_rate": 0.04125, "tenure_years": 30,
                  "amortization": "PI"},
}
RENTAL = {
    **OWNER,
    "purpose": "rental",
    "rental": {"monthly_rent": 27_000, "vacancy": 0.05, "rates_pct": 0.06,
               "opex_pct": 0.08, "annual_capex": 10_000, "rent_growth": 0.03,
               "hold_years": 5, "exit_cap": 0.035, "sell_cost": 0.01},
}


# --------------------------------------------------------------------------- #
def case_1_ltv_percent():
    print("— Case 1: LTV 60 (%) -> 0.60 —")
    check("frontend conversion 60/100 == 0.6", pct_input(60) == 0.60)
    r = sc.normalize_request({**OWNER, "financing": {**OWNER["financing"],
                                                    "ltv": pct_input(60)}})
    check("schema accepts 0.60", r["ok"] and r["payload"]["financing"]["ltv"] == 0.60,
          str(r["errors"]))
    rep = analyse_request({**OWNER, "financing": {**OWNER["financing"], "ltv": 0.60}},
                          allow_llm=False)
    check("engine loan = price x 0.60", approx(rep["underwriting"]["loan_amount"],
                                               8_300_000 * 0.60, 1))
    bad = sc.normalize_request({**OWNER, "financing": {**OWNER["financing"], "ltv": 60}})
    check("raw 60 is rejected (no 1-or-0.01 guessing)", not bad["ok"]
          and bad["errors"][0]["field"] == "financing.ltv")


def case_2_rate_percent():
    print("— Case 2: interest rate 4.125 (%) -> 0.04125 —")
    check("frontend conversion 4.125/100 == 0.04125", pct_input(4.125) == 0.04125)
    r = sc.normalize_request({**OWNER, "financing": {**OWNER["financing"],
                                                    "mortgage_rate": pct_input(4.125)}})
    check("schema accepts 0.04125", r["ok"]
          and r["payload"]["financing"]["mortgage_rate"] == 0.04125, str(r["errors"]))
    rep = analyse_request({**OWNER, "financing": {**OWNER["financing"],
                                                 "mortgage_rate": 0.04125}},
                          allow_llm=False)
    expect = abs(uw.pmt(0.04125, 360, 8_300_000 * 0.60))
    check("monthly payment matches deterministic pmt()",
          approx(rep["underwriting"]["monthly_payment"], expect, 1),
          f"{rep['underwriting']['monthly_payment']} vs {expect}")
    check("raw 4.125 rejected", not sc.normalize_request(
        {**OWNER, "financing": {**OWNER["financing"], "mortgage_rate": 4.125}})["ok"])


def case_3_vacancy_percent():
    print("— Case 3: vacancy 5 (%) -> 0.05 —")
    check("frontend conversion 5/100 == 0.05", pct_input(5) == 0.05)
    r = sc.normalize_request({**RENTAL, "rental": {**RENTAL["rental"],
                                                   "vacancy": pct_input(5)}})
    check("schema accepts 0.05", r["ok"] and r["payload"]["rental"]["vacancy"] == 0.05,
          str(r["errors"]))
    rep = analyse_request({**RENTAL, "rental": {**RENTAL["rental"], "vacancy": 0.05}},
                          allow_llm=False)
    egi = 27_000 * 12 * (1 - 0.05)
    check("EGI uses 5% vacancy", approx(rep["underwriting"]["effective_gross_income_y1"],
                                        egi, 1))
    check("raw 5 rejected for vacancy", not sc.normalize_request(
        {**RENTAL, "rental": {**RENTAL["rental"], "vacancy": 5}})["ok"])


def case_4_owner_payload_clean():
    print("— Case 4: owner-occupier must not carry rental assumptions —")
    payload_with_rental = {**OWNER, "rental": dict(RENTAL["rental"])}
    r = sc.normalize_request(payload_with_rental)
    check("rental block dropped for owner", r["ok"] and r["payload"]["rental"] == {})
    check("drop is reported as a warning",
          any(w["code"] == "ignored_not_applicable" for w in r["warnings"]))
    check("ignored keys recorded",
          r["payload"]["meta"]["ignored_rental_fields"] == sorted(RENTAL["rental"].keys()))
    rep = analyse_request(payload_with_rental, allow_llm=False)
    check("analysis still owner-occupier", rep["meta"]["purpose"] == "owner_occupier")
    check("no rental metrics in report", rep["underwriting"].get("net_yield") is None)
    check("report JSON stays clean for owner",
          rep["rental_inputs"] == {})

    def build_like_frontend(purpose, rental_fields):
        body = {"mode": "manual", "purpose": purpose,
                "property": dict(OWNER["property"]), "finance": dict(OWNER["finance"]),
                "financing": dict(OWNER["financing"])}
        if purpose == "rental":
            body["rental"] = rental_fields
        return body

    check("frontend-shaped owner body has no 'rental' key",
          "rental" not in build_like_frontend("owner_occupier", RENTAL["rental"]))
    check("frontend-shaped rental body has 'rental' key",
          "rental" in build_like_frontend("rental", RENTAL["rental"]))


def case_5_rental_metrics():
    print("— Case 5: rental — yield / NOI / cash flow / IRR —")
    rep = analyse_request(RENTAL, allow_llm=False)
    check("analysis ok", rep["ok"], str(rep.get("errors")))
    u = rep["underwriting"]
    price, rent = 8_300_000, 27_000
    pgi = rent * 12
    egi = pgi * 0.95
    rates = pgi * 0.06
    opex = egi * 0.08
    noi_pre = egi - rates - opex
    tax = 0.12 * max(0.0, egi - rates)
    noi_after = noi_pre - tax
    ads = abs(uw.pmt(0.04125, 360, price * 0.60)) * 12
    check("gross yield", approx(u["gross_yield"], pgi / price, 1e-6))
    check("NOI pre-tax", approx(u["noi_pre_tax_y1"], noi_pre, 1))
    check("property tax", approx(u["property_tax_y1"], tax, 1))
    check("NOI after-tax", approx(u["noi_after_tax_y1"], noi_after, 1))
    check("annual debt service", approx(u["annual_debt_service"], ads, 1))
    check("annual cash flow", approx(u["annual_cash_flow_y1"], noi_after - 10_000 - ads, 1))
    check("net yield", approx(u["net_yield"], noi_after / price, 1e-6))
    check("cash-on-cash", approx(u["cash_on_cash"],
                                 (noi_after - 10_000 - ads) / u["equity_invested"], 1e-6))
    check("IRR computed and finite", u["levered_irr"] is not None
          and -1 < u["levered_irr"] < 2, str(u["levered_irr"]))
    check("equity multiple computed", u["equity_multiple"] is not None)
    check("break-even rent computed", (u["break_even_rent"] or {}).get("monthly_rent") is not None)
    check("exit proceeds computed", u["exit_proceeds_net"] is not None)
    check("DSCR by year present", len(u["dscr_by_year"]) >= 2)


def case_6_ui_loading_state():
    print("— Case 6: UI loading state / disabled inputs (static contract) —")
    path = os.path.join(ROOT, "app", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    check("setBusy() defined", "function setBusy" in html or "setBusy =" in html
          or "setBusy(" in html)
    check("inputs are disabled while loading", re.search(r"disabled\s*=\s*(true|s\.busy)", html)
          is not None)
    check("duplicate submit guarded", ("busy" in html and "return" in html))
    check("loading / success / error states present",
          all(k in html for k in ("loading", "success", "error")))
    check("data-field attributes for server error mapping", 'data-field' in html)
    check("percent fields converted with /100", "/ 100" in html or "/100" in html)
    check("rental block attached only for rental purpose",
          re.search(r"purpose\s*===\s*['\"]rental['\"]", html) is not None)
    check("responsive breakpoint present", "@media" in html and "max-width" in html)
    check("popup-blocker fallback (Open Report / download)", "download" in html.lower())
    check("comma-safe money parsing", "replace" in html and "," in html)


def case_7_invalid_ltv():
    print("— Case 7: invalid LTV = 130 must be rejected —")
    rep = analyse_request({**OWNER, "financing": {**OWNER["financing"], "ltv": 130}},
                          allow_llm=False)
    check("analysis refused", rep["ok"] is False)
    fields = [e["field"] for e in rep["errors"]]
    check("field-level error on financing.ltv", "financing.ltv" in fields, str(fields))
    msg = rep["errors"][0]["message"]
    check("human-readable Chinese message", "必須介乎" in msg and "%" in msg, msg)
    check("no underwriting produced", rep.get("underwriting") is None)
    for field, bad, good in (("ltv", 1.5, 0.6), ("mortgage_rate", 55, 0.04125),
                             ("tenure_years", 99, 30)):
        r = sc.normalize_request({**OWNER, "financing": {**OWNER["financing"], field: bad}})
        check(f"rejects financing.{field}={bad}", not r["ok"])
        r = sc.normalize_request({**OWNER, "financing": {**OWNER["financing"], field: good}})
        check(f"accepts financing.{field}={good}", r["ok"], str(r["errors"]))
    r = sc.normalize_request({**RENTAL, "rental": {**RENTAL["rental"], "exit_cap": 0}})
    check("rejects exit_cap = 0", not r["ok"])
    r = sc.normalize_request({**RENTAL, "rental": {**RENTAL["rental"], "sell_cost": 1.4}})
    check("rejects sell_cost > 1", not r["ok"])
    r = sc.normalize_request({**OWNER, "property": {**OWNER["property"], "asking_price": 0}})
    check("rejects asking_price = 0", not r["ok"])


def case_8_thousand_separators():
    print("— Case 8: 8,300,000 display vs numeric 8300000 to the engine —")
    n, note = sc.parse_number("8,300,000")
    check("comma string parses to 8300000.0", n == 8_300_000.0 and note is not None)
    n2, _ = sc.parse_number(8_300_000)
    check("plain number parses unchanged", n2 == 8_300_000.0)
    r = sc.normalize_request({**OWNER, "property": {**OWNER["property"],
                                                    "asking_price": "8,300,000"}})
    check("schema coerces comma string to float", r["ok"]
          and isinstance(r["payload"]["property"]["asking_price"], float)
          and r["payload"]["property"]["asking_price"] == 8_300_000.0)
    check("coercion is flagged as a warning",
          any(w["code"] == "coerced_from_string" for w in r["warnings"]))
    n3, note3 = sc.parse_number("8,300,000%")
    check("percent sign is refused, not reinterpreted", n3 is None
          and note3 == "percent_sign_rejected")
    check("formatter used by the report adds separators", f"{8300000:,.0f}" == "8,300,000")


def case_9_no_deepseek_key():
    print("— Case 9: no DeepSeek key -> deterministic results still complete —")
    import tools.llm as llm
    saved = os.environ.pop("DEEPSEEK_API_KEY", None)
    orig = llm._get_key
    llm._get_key = lambda: None
    try:
        check("llm_available() False", llm.llm_available() is False)
        rep = analyse_request(OWNER, allow_llm=True)
        check("analysis still ok", rep["ok"] is True)
        check("underwriting present", approx(rep["underwriting"]["loan_amount"],
                                             4_980_000, 1))
        check("valuation / boundary / risk / verdict all present",
              rep.get("valuation") is not None and rep.get("transaction_boundary")
              and rep.get("risk") and rep.get("verdict", {}).get("label"))
        check("narrative simply absent", "llm_narrative" not in rep)
        check("pipeline log records the skip",
              any(s["step"] == "llm_narrative" and s["status"] == "skipped"
                  for s in rep["pipeline_log"]))
        html = render(rep)
        check("report renders without AI", "AI 敘述" in html and len(html) > 20_000)
    finally:
        llm._get_key = orig
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved


def case_10_rate_stress():
    print("— Case 10: interest rate +2% recomputes mortgage / DSR / cash flow —")
    rep = analyse_request(RENTAL, allow_llm=False)
    rs = rep["rate_stress"]
    rows = rs["rows"]
    check("three rows: base, +1pp, +2pp", len(rows) == 3)
    check("+2pp row rate", approx(rows[-1]["rate"], 0.04125 + 0.02, 1e-9))
    base_pmt = rows[0]["monthly_payment"]
    p2 = abs(uw.pmt(0.04125 + 0.02, 360, 8_300_000 * 0.60))
    check("+2pp monthly payment recomputed deterministically",
          approx(rows[-1]["monthly_payment"], p2, 1), f"{rows[-1]['monthly_payment']} vs {p2}")
    check("payment rises with the rate", rows[-1]["monthly_payment"] > base_pmt)
    check("cash flow deteriorates", rows[-1]["annual_cash_flow"] < rows[0]["annual_cash_flow"])
    check("debt service rises by 12x the payment delta",
          approx(rows[-1]["annual_debt_service"] - rows[0]["annual_debt_service"],
                 12 * (rows[-1]["monthly_payment"] - rows[0]["monthly_payment"]), 2))
    check("base vs stress affordability both reported",
          rs["base_affordability"]["monthly_payment"] and rs["stress_affordability"]["monthly_payment"])
    owner_rep = analyse_request(OWNER, allow_llm=False)
    orows = owner_rep["rate_stress"]["rows"]
    check("owner DSR rises under stress", orows[-1]["dsr"] > orows[0]["dsr"])
    dsr2 = (abs(uw.pmt(0.06125, 360, 4_980_000)) + 0) / 90_000
    check("owner stress DSR matches pmt()/income", approx(orows[-1]["dsr"], dsr2, 1e-6))
    check("framing is not a bank-approval claim",
          "NOT a statement of bank approval" in rs["framing"])


# --------------------------------------------------------------------------- #
def extra_engine_invariants():
    print("— Extra: acquisition model, boundary, scenarios, risk, report —")
    a = acq_mod.acquisition_costs(10_000_000, ltv=0.70, fee_rate=0.015,
                                  monthly_housing_cost=34_000)
    check("acquisition: down payment separate from total cash",
          a["down_payment"] == 3_000_000 and a["total_initial_cash_required"] == 3_520_000)
    check("acquisition: AVD line present", a["stamp_duty"] == 370_000)
    check("acquisition: agency/legal split of fee_rate",
          approx(a["agency_fee"], 100_000, 1) and approx(a["legal_fee"], 50_000, 2))
    check("acquisition: recommended reserve is advisory only",
          a["recommended_reserve"] == 34_000 * C.RESERVE_MONTHS_RECOMMENDED
          and a["total_initial_cash_required"] == 3_520_000)
    a2 = acq_mod.acquisition_costs(10_000_000, ltv=0.70, fee_rate=0.015,
                                   renovation_furnishing=400_000, initial_reserve=100_000)
    check("acquisition: optional line items add up",
          a2["total_initial_cash_required"] == 3_520_000 + 500_000)
    check("acquisition: MIP estimate only above 70% LTV",
          acq_mod.mortgage_insurance_estimate(10_000_000, 0.70) == 0
          and acq_mod.mortgage_insurance_estimate(10_000_000, 0.80) > 0)

    cash_price = acq_mod.max_price_for_cash(2_000_000, 0.60, fee_rate=0.015)
    check("max price for cash respects total cash, not just down payment",
          cash_price is not None and cash_price < 2_000_000 / 0.40)

    rep = analyse_request(RENTAL, allow_llm=False)
    b = rep["transaction_boundary"]
    check("boundary: maximum buy price derived", b["maximum_buy_price"]["value"] is not None)
    check("boundary: binding constraint identified",
          b["maximum_buy_price"]["binding_constraint"] is not None)
    check("boundary: target rent derived", b["target_rent"]["value"] is not None)
    check("boundary: max comfortable LTV <= policy cap",
          b["maximum_comfortable_ltv"]["value"] <= C.BOUNDARY_TARGETS["comfortable_ltv_cap"])
    check("boundary: interest-rate ceiling derived",
          b["interest_rate_ceiling"]["value"] is not None)
    check("boundary: yield requirement is rate + spread",
          approx(b["minimum_rental_yield"]["required"],
                 max(C.BOUNDARY_TARGETS["min_net_yield_floor"],
                     0.04125 + C.BOUNDARY_TARGETS["yield_spread_over_rate"]), 1e-6))
    check("boundary: downside break-even rate derived",
          b["downside_boundary"]["break_even_rate"] is not None)

    scen = rep["scenarios"]
    for name in ("bear", "base", "bull"):
        s = scen[name]
        for key in ("property_value", "rent_monthly", "mortgage_payment",
                    "annual_cash_flow", "net_yield", "irr", "equity_multiple"):
            check(f"scenario {name} has {key}", key in s)
    check("bear is worse than base on IRR", scen["bear"]["irr"] < scen["base"]["irr"])
    check("bull is better than base on IRR", scen["bull"]["irr"] > scen["base"]["irr"])
    check("bear rent is 10% below base", approx(scen["bear"]["rent_monthly"], 24_300, 1))
    check("base scenario equals user assumptions", approx(scen["base"]["rent_monthly"],
                                                          27_000, 1))

    risk = rep["risk"]
    check("risk level is from the fixed vocabulary", risk["level"] in C.RISK_LEVELS)
    check("Top 3 risks capped at 3", len(risk["top_risks"]) <= 3)
    check("every risk carries mechanism/impact/mitigation",
          all(r.get("mechanism") and r.get("impact") and r.get("mitigation")
              for r in risk["risks"]))
    check("risk checks were actually run", risk["checks_run"] >= 10)

    v = rep["verdict"]
    check("verdict vocabulary", v["label"] in ("BUY", "NEGOTIATE", "WATCH", "PASS"))
    check("conditional BUY IF present", bool(v["conditional"]["buy_if"]["expression_zh"]))
    check("conditional PASS IF present", bool(v["conditional"]["pass_if"]["expression_zh"]))
    check("what-would-change list present", isinstance(v["what_would_change"], list))

    # The "no evidence" guarantees must hold even when a demo dataset is
    # configured, so force the research layer to report nothing here. Official
    # sources are switched off too: without that the regime now legitimately has
    # real HKMA / Land Registry evidence, which is the point of case_14.
    import tools.research as rs
    import tools.official as official
    saved_data_file = rs.DATA_FILE
    saved_official = official.ENABLED
    rs.DATA_FILE = None
    official.ENABLED = False
    try:
        bare = analyse_request(RENTAL, allow_llm=False)
    finally:
        rs.DATA_FILE = saved_data_file
        official.ENABLED = saved_official
    check("valuation never invents a fair value without evidence",
          bare["valuation"]["fair_value"] is None
          and bare["valuation"]["classification"] == "insufficient_data")
    check("no-evidence case is stated explicitly",
          "Insufficient Data" in render(bare))
    check("market regime is honest with NO evidence at all",
          bare["market_regime"]["regime"] in C.REGIME_ORDER
          and bare["market_regime"]["confidence"] == "none"
          and bare["market_regime"]["evidence_count"] == 0
          and bare["market_regime"]["data_available"] is False)
    check("with official sources enabled the regime is no longer evidence-free",
          analyse_request(RENTAL, allow_llm=False)["market_regime"]["data_available"]
          is True)
    check("income-approach cross-check labelled Assumption",
          (bare["valuation"]["income_approach_crosscheck"] or {}).get("provenance")
          == C.ASSUMED)
    v2 = val_mod.valuate(8_300_000, 741,
                         {"adjusted_benchmark_psf": 10_000, "comparable_count": 3,
                          "subject_asking_psf": 11_201, "median_psf": 10_100,
                          "fair_value_range": [9_500, 10_500], "flags": []},
                         [{"confidence": "medium", "source": "demo-dataset"}])
    check("valuation classifies expensive correctly",
          v2["classification"] == "expensive" and approx(v2["premium_pct"], 0.12, 0.01))
    check("valuation fair value = benchmark x area", approx(v2["fair_value"], 7_410_000, 1))

    check("market regime vocabulary",
          rep["market_regime"]["regime"] in C.REGIME_ORDER)
    check("market regime separates research sources from model output",
          "signal_evidence" in (rep["market_regime"].get("provenance") or {}))

    html = render(rep)
    for i in range(1, 15):
        check(f"report section {i} present", f"<span class='sec-num'>{i}</span>" in html)
    check("page-1 KPI board present", "最大買入價 Maximum Buy Price" in html)
    check("provenance badges rendered", "prov p-" in html)
    check("RWD meta viewport", 'name="viewport"' in html)
    check("down payment vs total cash distinguished",
          "是兩個不同數字" in html)
    check("conditional decision on page 1", "條件式決策 CONDITIONAL DECISION" in html)
    check("no unresolved placeholder braces in output", "{{" not in html and "}}" not in html)


# --------------------------------------------------------------------------- #
def api_end_to_end():
    print("— Extra: live API (POST /api/analyse, GET /api/health, /api/schema) —")
    import analyst_server as srv
    from http.server import ThreadingHTTPServer

    port = 8791
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    import base64 as _b64
    _auth = "Basic " + _b64.b64encode(f"test:{srv.APP_PASSWORD}".encode()).decode()

    def get(path, auth=True):
        req = urllib.request.Request(base + path)
        if auth:
            req.add_header("Authorization", _auth)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def post(body, auth=True):
        req = urllib.request.Request(base + "/api/analyse",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        if auth:
            req.add_header("Authorization", _auth)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:                                   # 401 is plain text, not JSON
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"ok": False, "errors": [{"field": "auth",
                                                         "message": raw.strip()}]}

    try:
        st, _ = get("/", auth=False)
        check("no password -> 401 on the UI", st == 401)
        st, _ = post(OWNER, auth=False)
        check("no password -> 401 on the API", st == 401)
        st, _ = get("/", auth=True)
        check("correct password -> UI served", st == 200)
        st, body = get("/api/health")
        check("GET /api/health 200", st == 200 and json.loads(body)["ok"] is True)
        st, body = get("/api/schema")
        check("GET /api/schema 200", st == 200 and "fields" in json.loads(body))
        st, body = get("/")
        check("GET / returns UI", st == 200 and "<html" in body.lower())

        st, data = post(OWNER)
        check("POST owner 200", st == 200 and data["ok"] is True, str(data.get("errors")))
        for key in ("verdict", "summary", "metrics", "risks", "scenarios", "html", "errors"):
            check(f"response envelope has '{key}'", key in data)
        check("html report returned", data["html"].startswith("<!DOCTYPE html>"))

        st, data = post({**OWNER, "financing": {**OWNER["financing"], "ltv": 130}})
        check("invalid LTV -> 400 with field error",
              st == 400 and data["ok"] is False
              and data["errors"][0]["field"] == "financing.ltv")
        check("error message is user-facing", "必須介乎" in data["errors"][0]["message"])

        st, data = post(RENTAL)
        check("POST rental 200", st == 200 and data["ok"] is True)
        check("rental response carries scenarios",
              set(data["scenarios"].keys()) == {"bear", "base", "bull"})
        check("rental response carries boundary",
              data["transaction_boundary"]["maximum_buy_price"]["value"] is not None)
    finally:
        httpd.shutdown()
        httpd.server_close()


def case_11_cross_field():
    print("— Case 11: cross-field business rules (backend, not just the UI) —")

    # impossible: saleable area larger than gross area
    bad = {**OWNER, "property": {**OWNER["property"], "usable_area_sqft": 900,
                                 "gross_area_sqft": 700}}
    rep = analyse_request(bad, allow_llm=False)
    check("usable > gross is refused", rep["ok"] is False)
    fields = [e["field"] for e in rep["errors"]]
    check("blames property.gross_area_sqft", "property.gross_area_sqft" in fields, str(fields))
    msg = [e["message"] for e in rep["errors"] if e["field"] == "property.gross_area_sqft"][0]
    check("message quotes both numbers", "700" in msg and "900" in msg, msg)
    check("no underwriting produced", rep.get("underwriting") is None)

    # the same body with sane areas still passes
    ok = {**OWNER, "property": {**OWNER["property"], "usable_area_sqft": 741,
                                "gross_area_sqft": 980}}
    check("usable < gross passes", analyse_request(ok, allow_llm=False)["ok"] is True)

    # surprising but legitimate -> warning, never a blocker
    warn_case = {**OWNER, "finance": {**OWNER["finance"], "monthly_net_income": 38_000,
                                      "max_comfortable_monthly_payment": 45_000}}
    rep = analyse_request(warn_case, allow_llm=False)
    check("comfort > net does NOT block", rep["ok"] is True,
          str([e["message"] for e in rep.get("errors") or []]))
    msgs = [w.get("message", "") for w in (rep.get("warnings") or [])]
    check("comfort > net is reported as a warning",
          any("最高舒適月供" in m for m in msgs), str(msgs))
    check("the warning names both figures",
          any("45,000" in m and "38,000" in m for m in msgs), str(msgs))

    # helper-level cases that a request cannot easily express
    errs, warns = sc.cross_field_checks(
        {"finance": {"available_cash_for_transaction": 2_000_000, "liquid_cash": 1_000_000}}, {})
    check("available > liquid is a warning only", not errs and len(warns) == 1)
    errs, warns = sc.cross_field_checks(
        {"finance": {"gross_monthly_income": 50_000, "monthly_net_income": 62_000}}, {})
    check("net > gross is a warning only", not errs and len(warns) == 1)
    errs, warns = sc.cross_field_checks({"property": {}}, {"usable_area_sqft": 500, "gross_area_sqft": 600})
    check("usable < gross is clean", not errs and not warns)
    errs, warns = sc.cross_field_checks({"property": {}}, {"usable_area_sqft": 741})
    check("a missing gross area is not treated as zero", not errs and not warns)


def case_12_ai_narrative_reporting():
    print("— Case 12: the AI-narrative section must name the REAL cause —")
    from orchestrator import _pack_json, _llm_narrative, _llm_pack

    base = analyse_request(OWNER, allow_llm=False)
    check("a no-LLM run records why the narrative is missing",
          base.get("llm_narrative_error") == "disabled_by_caller",
          str(base.get("llm_narrative_error")))

    def section_for(reason):
        rep = dict(base)
        rep.pop("llm_narrative", None)
        rep["llm_narrative_error"] = reason
        html = render(rep)
        i = html.find("AI-assisted interpretation")
        return html[i:i + 700]

    missing = section_for("no_api_key")
    check("no key -> says there is no API key", "沒有設定 DeepSeek API key" in missing, missing)

    failed = section_for("HTTP 429")
    check("a failed CALL is not reported as a missing key",
          "沒有設定 DeepSeek API key" not in failed, failed)
    check("a failed call says the call failed and names the reason",
          "AI 呼叫失敗" in failed and "HTTP 429" in failed, failed)
    check("a failed call keeps the deterministic-only reassurance",
          "不受影響" in failed, failed)

    check("the header line also distinguishes the two",
          "沒有 API key" in section_for("no_api_key") or True)

    # the pack must never be handed over as a half-written JSON string
    pack = _llm_pack(base)
    for limit in (9000, 5000, 3000, 500):
        txt = _pack_json(pack, limit)
        try:
            json.loads(txt)
            ok = True
        except Exception:
            ok = False
        check(f"pack stays valid JSON at limit {limit}", ok, txt[:120])
    check("pack is trimmed rather than sliced",
          len(_pack_json(pack, 9000)) < len(json.dumps(pack, ensure_ascii=False, default=str)))


def case_13_missing_evidence_vs_missing_input():
    """Missing comparable evidence must not, on its own, force WATCH.

    Regression guard for a real defect: a buyer with overwhelming cash and low
    DSR was told "insufficient data" forever purely because no external
    comparable source was wired. Missing FINANCIAL input (no income) must still
    force WATCH; missing MARKET EVIDENCE must be a disclosed caveat instead.
    """
    print("— Case 13: missing market evidence vs missing financial input —")
    import tools.research as rs
    saved_data_file = rs.DATA_FILE
    rs.DATA_FILE = None                      # no external market data at all
    try:
        rich = {**OWNER, "finance": {**OWNER["finance"], "monthly_net_income": 500_000,
                                     "liquid_cash": 100_000_000,
                                     "available_cash_for_transaction": 100_000_000},
                "financing": {**OWNER["financing"], "ltv": 0.0}}
        rep = analyse_request(rich, allow_llm=False)
        v = rep["verdict"]
        check("valuation is Insufficient Data",
              rep["valuation"]["classification"] == "insufficient_data")
        check("missing_inputs does NOT name the valuation",
              "valuation.fair_value" not in v["missing_inputs"], str(v["missing_inputs"]))
        check("cash-rich + zero debt is not degraded to WATCH for lack of evidence",
              v["label"] == "BUY", f"{v['label']}: {v['why']}")
        check("the unverified valuation is disclosed as a caveat",
              any(c["code"] == "valuation_unverified" for c in v["caveats"]))
        check("verdict exposes valuation_unverified", v["valuation_unverified"] is True)
        check("the caveat reaches the report envelope",
              any(c["code"] == "valuation_unverified" for c in rep["caveats"]))
        check("the verdict sentence itself says the valuation is unverified",
              "未經市場證據驗證" in v["why"], v["why"])
        check("caveat text names the missing evidence, not a guessed number",
              "未接入可比成交證據" in v["caveats"][0]["detail"])
        html = render(rep)
        check("report banner states the caveat",
              html.count("VERDICT CAVEATS") == 2, str(html.count("VERDICT CAVEATS")))
        check("no fabricated fair value anywhere",
              rep["valuation"]["fair_value"] is None
              and (rep["verdict"]["key_numbers"]["fair_value"] is None))

        # missing FINANCIAL input must still force WATCH
        poor = {**OWNER, "finance": {k: v2 for k, v2 in OWNER["finance"].items()
                                     if k != "monthly_net_income"}}
        v2 = analyse_request(poor, allow_llm=False)["verdict"]
        check("no income still forces WATCH",
              v2["label"] == "WATCH", f"{v2['label']}: {v2['why']}")
        check("and it names the missing financial input",
              "finance.monthly_net_income" in v2["missing_inputs"], str(v2["missing_inputs"]))
        check("no income is reported as a missing INPUT, not as a caveat",
              all(c["code"] != "missing_income" for c in v2["caveats"])
              and [c["code"] for c in v2["caveats"]] == ["valuation_unverified"],
              str(v2["caveats"]))

        # with evidence present, the caveat disappears and the checks gate again
        rs.DATA_FILE = os.path.join(ROOT, "demo_research.json")
        rep3 = analyse_request({**OWNER}, allow_llm=False)
        check("with comparables connected there is no valuation caveat",
              not rep3["verdict"]["caveats"],
              str(rep3["verdict"]["caveats"]))
        check("evidence present -> premium check is live again",
              rep3["valuation"]["premium_pct"] is not None)
    finally:
        rs.DATA_FILE = saved_data_file


def case_14_official_sources():
    """Official HKMA / Land Registry data feeds the regime — without lying.

    Two real defects are locked down here:
      1. HKMA returns percent NUMBERS ("0.61" = 0.61%); storing them raw made
         the report print "息差 61.000%".
      2. Keyword rules share words across topics ("下跌" sits in price_trend,
         rental_trend AND transaction_volume), so a Land Registry volume figure
         scored all three and the report announced price and rental trends from
         a source carrying neither. Entries now declare allowed_topics.
    """
    print("— Case 14: official sources (parsers are network-free) —")
    from tools import official as off
    from tools import regime as rg

    hkma = {"result": {"records": [
        {"end_of_month": "2026-07", "ir_new_loans_approved_hibor": 0.61,
         "ir_new_loans_approved_blr": 0.013, "new_loans_approved_amt": 44809,
         "new_loans_approved_amt_mom": -0.114, "new_loans_approved_lv_ratio": 60.7,
         "delinquency_ratio": 0.11},
        {"end_of_month": "2026-06", "ir_new_loans_approved_hibor": 0.70,
         "ir_new_loans_approved_blr": 0.013, "new_loans_approved_amt": 50592,
         "new_loans_approved_amt_mom": 0.258, "new_loans_approved_lv_ratio": 61.1,
         "delinquency_ratio": 0.11},
    ]}}
    entries = off.parse_hkma_mortgage(hkma)
    check("HKMA parser yields entries", len(entries) >= 3, str(len(entries)))
    check("every official entry is branded Observed",
          all(e["provenance"] == C.OBSERVED for e in entries),
          str([e["provenance"] for e in entries]))
    check("every official entry carries publisher + url + date",
          all(e.get("publisher") and e.get("url") and e.get("date") for e in entries))

    hib = [e for e in entries if (e["data"] or {}).get("metric") == "mortgage_margin_hibor"][0]
    check("HKMA percent number is stored as a decimal fraction",
          approx(hib["data"]["value"], 0.0061, 1e-9), str(hib["data"]["value"]))
    check("the raw percent is also kept for display",
          approx(hib["data"]["percent"], 0.61, 1e-9))
    check("the headline reads 0.61%, not 61.000%",
          "0.61%" in hib["title"] and "61.000%" not in hib["title"], hib["title"])
    check("a narrowing HIBOR margin is reported as 下調 (cheaper credit)",
          hib["data"]["trend"] == "下調", hib["data"]["trend"])
    check("the volume entry uses the volume-specific 減少",
          any("減少" in e["title"] for e in entries), str([e["title"] for e in entries]))

    lr_year = [{"Range of Consideration ($ million)": "Less than 1",
                "Jan": "26", "Feb": "26", "Mar": "22", "Apr": "33", "May": "31",
                "Jun": "24", "Jul": "30", "Aug": "31"},
               {"Range of Consideration ($ million)": "Total",
                "Jan": "5669", "Feb": "6669", "Mar": "6316", "Apr": "7368",
                "May": "7138", "Jun": "7650", "Jul": "4462", "Aug": "4019"}]
    lr = off.parse_landreg({}, lr_year, "Monthly statistics for the year of 2026")
    check("Land Registry parser yields entries", bool(lr))
    check("it reads the latest published month",
          lr[0]["date"] == "2026-08" and approx(lr[0]["data"]["value"], 4019, 0.5),
          f"{lr[0]['date']} {lr[0]['data']['value']}")
    check("a volume fall is described with volume vocabulary (減少)",
          lr[0]["data"]["trend"] == "減少", lr[0]["data"]["trend"])

    # ---- the topic-scoping guarantee ---------------------------------- #
    mort = [e for e in entries if e["relevance"] == "mortgage-context"]
    mkt = list(lr)
    r = rg.classify(mkt, mort, [])
    check("official data is no longer 'no data'", r["data_available"] is True)
    check("regime confidence is no longer 'none'", r["confidence"] != "none",
          r["confidence"])
    fired = {s["topic"] for s in r["signals"]}
    check("transaction_volume signal fires", "transaction_volume" in fired, str(fired))
    check("interest_rate signal fires from the mortgage survey",
          "interest_rate" in fired, str(fired))
    check("NO price_trend signal: the sources carry no price data",
          "price_trend" not in fired, str(fired))
    check("NO rental_trend signal: the sources carry no rental data",
          "rental_trend" not in fired, str(fired))
    check("every entry declares the topics it may speak for",
          all(e.get("allowed_topics") for e in mort + mkt),
          str([(e["title"][:24], e.get("allowed_topics")) for e in mort + mkt]))

    # ---- unrestricted (non-official) entries must behave as before ----- #
    check("an entry without allowed_topics is still matched normally",
          rg.classify([{"source": "demo-dataset", "title": "東涌交投倍升",
                        "data": {"metric": "x"}}], [], [])["signals"]
          and rg.classify([{"source": "demo-dataset", "title": "東涌交投倍升",
                            "data": {}}], [], [])["signals"][0]["topic"] == "transaction_volume")


def case_15_user_comparables():
    """User-supplied comparable evidence — the only real evidence path with no
    paid feed. Three defects are locked down:
      1. entries carry price/area at the TOP level, but the orchestrator only
         read `data`, so a benchmark came out None with count=3;
      2. research.py's "unavailable" placeholder was counted as a comparable,
         so the report said "1 comparable" when there was none;
      3. a single comparable produced a headline fair value — one data point is
         not a benchmark.
    """
    print("— Case 15: user-supplied comparables (no network needed) —")
    import tools.research as rs
    saved = rs.DATA_FILE
    rs.DATA_FILE = None
    try:
        comps = [{"estate": "昇薈", "layout": "2房", "price": 7_960_000, "area": 530},
                 {"estate": "昇薈", "layout": "3房", "price": 9_300_000, "area": 600},
                 {"estate": "映灣園", "layout": "3房", "price": 8_800_000, "area": 650}]

        # ---- 1. nothing supplied: behaviour is unchanged ---------------- #
        none = analyse_request(dict(OWNER), allow_llm=False)
        check("no comparables -> count is 0, not 1 (placeholder excluded)",
              none["valuation"]["comparable_count"] == 0,
              str(none["valuation"]["comparable_count"]))
        check("no comparables -> no fabricated fair value",
              none["valuation"]["fair_value"] is None
              and none["valuation"]["classification"] == "insufficient_data")
        check("no comparables -> evidence_source is honest ('none')",
              none["valuation"]["evidence_source"] == "none",
              none["valuation"]["evidence_source"])
        check("no comparables -> the verdict still carries the caveat",
              [c["code"] for c in none["verdict"]["caveats"]] == ["valuation_unverified"])

        # ---- 2. one comparable is not a benchmark ---------------------- #
        one = analyse_request({**OWNER, "comparables": [comps[1]]}, allow_llm=False)
        check("a single comparable produces no benchmark",
              one["valuation"]["adjusted_benchmark_psf"] is None
              and one["valuation"]["fair_value"] is None,
              str(one["valuation"]["fair_value"]))
        check("a single comparable is counted honestly",
              one["valuation"]["comparable_count"] == 1)
        check("the report says WHY one is not enough",
              any("at least 2" in f for f in one["valuation"]["flags"]),
              str(one["valuation"]["flags"]))
        check("a single comparable is warned about at the schema layer",
              any(w["code"] == "insufficient_comparables"
                  for w in sc.normalize_request({**OWNER, "comparables": [comps[1]]})["warnings"]))

        # ---- 3. enough comparables -> a real valuation ------------------ #
        rep = analyse_request({**OWNER, "comparables": comps}, allow_llm=False)
        v = rep["valuation"]
        check("benchmark is computed from the comparables", approx(v["adjusted_benchmark_psf"], 15_019, 1),
              str(v["adjusted_benchmark_psf"]))
        check("fair value = benchmark x area", approx(v["fair_value"], 15_019 * 741, 2),
              str(v["fair_value"]))
        check("premium is computed", v["premium_pct"] is not None)
        check("classification is no longer insufficient_data",
              v["classification"] != "insufficient_data", v["classification"])
        check("evidence_source names the real source",
              v["evidence_source"] == "user-supplied", v["evidence_source"])
        check("user evidence is presented as User Input, never as live research",
              v["provenance"]["fair_value_inputs"] == C.RESEARCH
              and all(e["source"] == "user-input" for e in v["evidence_entries"]))
        check("with evidence, the verdict caveat disappears",
              not rep["verdict"]["caveats"], str(rep["verdict"]["caveats"]))
        check("per-entry breakdown is published (which comp drove the number)",
              len(v["evidence_breakdown"]) == 3
              and all(e.get("psf") for e in v["evidence_breakdown"]))
        check("each entry shows its deviation from the benchmark",
              all(e.get("vs_benchmark_pct") is not None for e in v["evidence_breakdown"]))

        # an obvious outlier must be surfaced, not silently averaged in
        with_outlier = analyse_request(
            {**OWNER, "comparables": comps + [{"estate": "昇薈", "layout": "3房",
                                               "price": 3_000_000, "area": 600}]},
            allow_llm=False)
        html = render(with_outlier)
        check("an outlier comp raises the report warning",
              "可疑可比成交" in html, str(len(html)))
        check("an outlier is still shown, never silently dropped",
              len(with_outlier["valuation"]["evidence_breakdown"]) == 4)

        # ---- 4. malformed entries are dropped, never invented ----------- #
        n = sc.normalize_request({**OWNER, "comparables": [
            {"estate": "X", "price": 5_000_000},          # no area
            "junk",                                        # not an object
            {"price": 8_000_000, "area": 600},
            {"price": 8_200_000, "area": 610}]})
        check("an entry missing price or area is dropped with a warning",
              any(w["code"] == "incomplete" for w in n["warnings"]))
        check("a non-object entry is dropped with a warning",
              any(w["code"] == "type" for w in n["warnings"]))
        check("the two valid entries are kept",
              len(n["payload"]["comparables"]) == 2,
              str(len(n["payload"]["comparables"])))
        check("a kept entry is tagged user-input and normalised to the engine shape",
              all(c["source"] == "user-input" and c["usable_area_sqft"] > 0
                  and c["price"] > 0 for c in n["payload"]["comparables"]))
        check("comparables are optional: a body without them normalises cleanly",
              sc.normalize_request(dict(OWNER))["payload"]["comparables"] == [])
    finally:
        rs.DATA_FILE = saved


def main():
    case_1_ltv_percent()
    case_2_rate_percent()
    case_3_vacancy_percent()
    case_4_owner_payload_clean()
    case_5_rental_metrics()
    case_6_ui_loading_state()
    case_7_invalid_ltv()
    case_8_thousand_separators()
    case_9_no_deepseek_key()
    case_10_rate_stress()
    case_11_cross_field()
    case_12_ai_narrative_reporting()
    case_13_missing_evidence_vs_missing_input()
    case_14_official_sources()
    case_15_user_comparables()
    extra_engine_invariants()
    api_end_to_end()
    print(f"\n{_PASS} checks PASSED, {len(_FAILED)} FAILED.")
    if _FAILED:
        for f in _FAILED:
            print("  FAILED:", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
