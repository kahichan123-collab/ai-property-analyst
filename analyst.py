# -*- coding: utf-8 -*-
"""analyst.py — AI Property Analyst CLI (v2.0 entry point)

Examples
--------
Owner-occupier from a listing URL::

    python analyst.py --url "https://hk.centanet.com/..." --income 90000 \
        --ltv 60 --rate 4.125 --tenure 30 --purpose owner_occupier \
        --cash 5000000 --available-cash 5000000 --out report.html

Rental with full assumptions (percentages are typed as percentages)::

    python analyst.py --url "https://hk.centanet.com/..." --income 90000 \
        --ltv 60 --rate 4.125 --purpose rental --rent 27000 --vacancy 5 \
        --exit-cap 3.5 --hold-years 5 --age 8 --out report_rental.html

A local listing HTML file (fixture / offline) instead of a live fetch::

    python analyst.py --url "https://example/listing" \
        --html-file fixtures/sample_listing.html \
        --price 8300000 --area 741 --income 90000 --ltv 60 --rate 4.125

Machine-readable output (full response envelope): ``--json out.json``.
Percentages are ALWAYS typed as percentages on the CLI (60, 4.125, 3.5) and
converted to fractions exactly once, before the request is built.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestrator import analyse_request
from tools.report import write_report


def pct(v):
    """CLI percentages are typed as percentages and converted once, here."""
    return None if v is None else v / 100.0


def main():
    ap = argparse.ArgumentParser(
        description="AI Property Analyst — deterministic underwriting first, "
                    "AI narrative second")
    ap.add_argument("--url", default="", help="property listing URL (omit for manual input)")
    ap.add_argument("--html-file", default=None,
                    help="local listing HTML instead of a live fetch (fixtures/offline)")
    # ---- property -------------------------------------------------------- #
    ap.add_argument("--price", type=float, default=None, help="asking price (HK$)")
    ap.add_argument("--area", type=float, default=None, help="saleable area (sqft)")
    ap.add_argument("--estate", default=None)
    ap.add_argument("--district", default=None)
    ap.add_argument("--building", default=None)
    ap.add_argument("--layout", default=None)
    ap.add_argument("--floor", default=None)
    ap.add_argument("--age", type=float, default=None, help="building age (years)")
    ap.add_argument("--mgmt", type=float, default=None, help="monthly management fee HK$")
    ap.add_argument("--rates", type=float, default=None, help="monthly rates HK$")
    # ---- personal finance ------------------------------------------------ #
    ap.add_argument("--income", type=float, default=None, help="monthly NET income")
    ap.add_argument("--gross-income", type=float, default=None)
    ap.add_argument("--other-income", type=float, default=0.0)
    ap.add_argument("--debt", type=float, default=0.0, help="existing monthly debt")
    ap.add_argument("--cash", type=float, default=None, help="liquid cash HK$")
    ap.add_argument("--available-cash", type=float, default=None,
                    help="cash available for the transaction HK$")
    ap.add_argument("--reserve", type=float, default=None, help="emergency reserve HK$")
    ap.add_argument("--max-payment", type=float, default=None,
                    help="maximum comfortable monthly housing payment HK$")
    # ---- financing (percentages) ----------------------------------------- #
    ap.add_argument("--ltv", type=float, default=60.0, help="LTV in percent, e.g. 60")
    ap.add_argument("--rate", type=float, default=4.125,
                    help="mortgage rate in percent, e.g. 4.125")
    ap.add_argument("--tenure", type=int, default=30)
    ap.add_argument("--amort", default="PI", choices=["PI", "IO"])
    ap.add_argument("--discount-rate", type=float, default=6.0,
                    help="discount rate in percent")
    ap.add_argument("--purpose", default="owner_occupier",
                    choices=["owner_occupier", "rental"])
    # ---- rental (percentages) -------------------------------------------- #
    ap.add_argument("--rent", type=float, default=None, help="monthly rent HK$")
    ap.add_argument("--vacancy", type=float, default=5.0, help="vacancy in percent")
    ap.add_argument("--rates-pct", type=float, default=6.0, help="rates as pct of PGI")
    ap.add_argument("--opex-pct", type=float, default=8.0, help="opex as pct of EGI")
    ap.add_argument("--capex", type=float, default=10000.0, help="annual capex reserve HK$")
    ap.add_argument("--rent-growth", type=float, default=3.0, help="annual rent growth, percent")
    ap.add_argument("--hold-years", type=int, default=5)
    ap.add_argument("--exit-cap", type=float, default=3.5, help="exit cap rate in percent")
    ap.add_argument("--sell-cost", type=float, default=1.0, help="selling cost in percent")
    # ---- acquisition extras ---------------------------------------------- #
    ap.add_argument("--renovation", type=float, default=0.0)
    ap.add_argument("--mortgage-fees", type=float, default=0.0)
    ap.add_argument("--valuation-fee", type=float, default=0.0)
    ap.add_argument("--initial-reserve", type=float, default=0.0)
    # ---- output ----------------------------------------------------------- #
    ap.add_argument("--out", default="property_report.html")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="also write the full JSON response envelope here")
    ap.add_argument("--no-llm", action="store_true", help="skip the AI narrative")
    a = ap.parse_args()

    request = {
        "mode": "auto" if a.url else "manual",
        "purpose": a.purpose,
        "property": {
            "url": a.url,
            "estate": a.estate, "district": a.district, "building": a.building,
            "layout": a.layout, "floor": a.floor, "building_age": a.age,
            "monthly_mgmt_rates": a.mgmt, "rates_amount": a.rates,
        },
        "finance": {
            "monthly_net_income": a.income, "gross_monthly_income": a.gross_income,
            "other_recurring_income": a.other_income,
            "existing_monthly_debt": a.debt, "liquid_cash": a.cash,
            "available_cash_for_transaction": a.available_cash,
            "emergency_reserve": a.reserve,
            "max_comfortable_monthly_payment": a.max_payment,
        },
        "financing": {
            "ltv": pct(a.ltv), "mortgage_rate": pct(a.rate),
            "tenure_years": a.tenure, "amortization": a.amort,
            "discount_rate": pct(a.discount_rate),
        },
        "acquisition": {
            "mortgage_fees": a.mortgage_fees, "valuation_fee": a.valuation_fee,
            "renovation_furnishing": a.renovation,
            "initial_reserve": a.initial_reserve,
        },
        "overrides": {},
    }
    # price/area are still sent inside `property` for manual mode; for a live URL
    # they act as overrides when extraction cannot read the listing.
    if a.price is not None or a.area is not None:
        request["overrides"] = {"price": a.price, "area": a.area}
    if not a.url:
        request["property"]["asking_price"] = a.price
        request["property"]["usable_area_sqft"] = a.area
    if a.purpose == "rental":
        request["rental"] = {
            "monthly_rent": a.rent, "vacancy": pct(a.vacancy),
            "rates_pct": pct(a.rates_pct), "opex_pct": pct(a.opex_pct),
            "annual_capex": a.capex, "rent_growth": pct(a.rent_growth),
            "hold_years": a.hold_years, "exit_cap": pct(a.exit_cap),
            "sell_cost": pct(a.sell_cost),
        }

    ext_html = None
    if a.html_file:
        with open(a.html_file, encoding="utf-8") as f:
            ext_html = f.read()

    rpt = analyse_request(request, ext_html=ext_html, allow_llm=not a.no_llm)

    if not rpt.get("ok"):
        print("分析未完成 — 輸入未通過驗證：", file=sys.stderr)
        for e in rpt.get("errors", []):
            print(f"  · {e.get('field')}: {e.get('message')}", file=sys.stderr)
            if e.get("hint"):
                print(f"    hint: {e['hint']}", file=sys.stderr)
        return 2

    path = write_report(rpt, a.out)
    v = rpt["verdict"]
    s = rpt["summary"]
    print("verdict          :", v["label"])
    print("why              :", v["why"])
    ap_price = (s.get("property") or {}).get("asking_price")
    print("asking price     :", f"HK${ap_price:,.0f}" if ap_price else "—")
    print("fair value       :", f"HK${s['fair_value']:,.0f}" if s.get("fair_value")
          else "Insufficient Data")
    print("total cash req.  :", f"HK${s['total_initial_cash_required']:,.0f}"
          if s.get("total_initial_cash_required") else "—")
    print("monthly payment  :", f"HK${s['monthly_payment']:,.0f}"
          if s.get("monthly_payment") else "—")
    if a.purpose == "rental":
        print("net yield        :", f"{s['net_yield'] * 100:.2f}%"
              if s.get("net_yield") is not None else "—")
        print("base / stress IRR:", f"{s['base_irr'] * 100:.1f}% / {s['stress_irr'] * 100:.1f}%"
              if s.get("base_irr") is not None else "—")
    print("maximum buy price:", f"HK${s['maximum_buy_price']:,.0f}"
          if s.get("maximum_buy_price") else "—")
    print("risk level       :", s.get("risk_level"))
    print("AI narrative     :", "attached" if rpt.get("llm_narrative")
          else "not used (deterministic results unaffected)")
    print("report written   :", path)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(rpt, f, ensure_ascii=False, indent=2, default=str)
        print("json written     :", a.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
