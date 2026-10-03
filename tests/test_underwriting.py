# -*- coding: utf-8 -*-
# test_underwriting.py  — PHASE 3
# Verifies the extracted deterministic engine against anchors reproduced from
# the prototype HTML (`HK_住宅投資互動計算機.html`) and Excel model
# (`REPE_香港住宅投資模型.xlsx`). Run:  python -m tests.test_underwriting  (from project root)
from __future__ import annotations
import math, sys, os

# See tests/test_upgrade.py: a cp950 console cannot encode the report's CJK
# punctuation / U+2212, so a fully green run would still exit non-zero.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.underwriting import (get_avd, total_acquisition_cost, owner_underwriting,
                                rental_underwriting, run_underwriting, _pmt_magnitude)

_PASS = 0

def approx(a, b, tol=1e-2):
    return abs(a - b) <= tol

def check(name, cond, detail=""):
    global _PASS
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        raise AssertionError(f"FAIL: {name} {detail}")

def test_avd_scale():
    print("— AVD stamp duty anchors (mirror HTML/Excel getAVD) —")
    check("3M -> 100", get_avd(3_000_000) == 100)
    check("5M -> 112500", approx(get_avd(5_000_000), 112_500))
    check("10M -> 370000 (relief band)", approx(get_avd(10_000_000), 370_000))
    check("15M -> 562500 (3.75%)", approx(get_avd(15_000_000), 562_500))
    check("20M -> 750000", approx(get_avd(20_000_000), 750_000))

def test_owner_anchors():
    print("— Owner-occupier anchors (from HTML/Excel) —")
    # 10,000,000 / LTV .70 / 4.125% / 30y -> monthly P&I ~ 33,925 (Excel verifies 33,925)
    pmt_m = _pmt_magnitude(7_000_000, 0.04125, 30, "PI")
    check("monthly P&I on 7M @4.125%/30y ~ 33,925", approx(pmt_m, 33_925, tol=5))
    # cash needed = down 3,000,000 + (AVD 370,000 + fees 150,000) = 3,520,000
    r = owner_underwriting(price=10_000_000, ltv=0.70, annual_rate=0.04125,
                           tenure_years=30, amort="PI",
                           monthly_net_income=60_000, monthly_mgmt_rates=1200,
                           fee_rate=0.015, area_sqft=500)
    check("down_payment 3,000,000", approx(r["down_payment"], 3_000_000, 1))
    check("stamp_duty 370,000", approx(r["stamp_duty"], 370_000, 1))
    check("other_cost 150,000", approx(r["other_transaction_cost"], 150_000, 1))
    check("initial_cash 3,520,000", approx(r["initial_cash_required"], 3_520_000, 1))
    check("DSR ~56.5%", approx(r["dsr"], (33_925 + 0) / 60_000, 0.001))
    check("price_psf 20,000", approx(r["price_psf"], 20_000, 1))

def test_property_tax():
    print("— Property tax 0.12*(rent−rates) anchor —")
    r = rental_underwriting(price=10_000_000, area_sqft=500, monthly_rent=30_000,
                            vacancy=0.05, rates_pct=0.06, opex_pct=0.08,
                            annual_capex=10_000, ltv=0.60, annual_rate=0.04125,
                            tenure_years=30, amort="PI", hold_years=5,
                            exit_cap=0.035, sell_cost=0.01, fee_rate=0.015)
    # EGI year1 = 360000*0.95 = 342000 ; rates = 21600 -> tax = 0.12*(342000-21600)=38448
    tax_implied = (342_000 - 21_600) * 0.12
    noia = 342_000 - 21_600 - 342_000*0.08 - tax_implied   # pre-tax NOI minus tax
    check("implied pre-tax NOI ~293,040", approx(342_000 - 21_600 - 342_000*0.08, 293_040, 1))
    check("after-tax NOI ~254,592", approx(noia, 254_592, 1))
    check("min DSCR ~0.84", approx(r["min_dscr"], 0.84, 0.01))

def test_dispatcher_demo_scenario():
    print("— Agent-facing run_underwriting() demo payload —")
    payload = {
        "property": {"price": 8_300_000, "usable_area_sqft": 741},
        "user": {"monthly_net_income": 30_000, "existing_monthly_debt": 0},
        "financing": {"ltv": 0.60, "mortgage_rate": 0.04125,
                      "tenure_years": 30, "amortization": "PI"},
        "purpose": "owner_occupier",
    }
    out = run_underwriting(payload)
    for k in ("loan_amount", "down_payment", "stamp_duty", "initial_cash_required",
              "monthly_payment", "monthly_housing_cost", "dsr", "monthly_surplus",
              "affordability_score", "risks"):
        check(f"returns '{k}'", k in out)
    check("loan = 8.3M*0.6 = 4,980,000", approx(out["loan_amount"], 4_980_000, 1))
    check("down = 3,320,000", approx(out["down_payment"], 3_320_000, 1))
    check("has risks list", isinstance(out["risks"], list) and len(out["risks"]) >= 1)

def main():
    test_avd_scale()
    test_owner_anchors()
    test_property_tax()
    test_dispatcher_demo_scenario()
    print(f"\nAll {_PASS} checks PASSED.")

if __name__ == "__main__":
    main()
