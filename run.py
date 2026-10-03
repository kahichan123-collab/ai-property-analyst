# -*- coding: utf-8 -*-
"""run.py — CLI demo of the deterministic underwriting engine.

Usage:
  python run.py                     # runs the PHASE-3 demo payload
  python run.py <file.json>         # underwrite from a JSON payload file
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.underwriting import run_underwriting

DEMO = {
    "property": {"price": 8_300_000, "usable_area_sqft": 741},
    "user": {"monthly_net_income": 30_000, "existing_monthly_debt": 0},
    "financing": {"ltv": 0.60, "mortgage_rate": 0.04125, "tenure_years": 30,
                  "amortization": "PI"},
    "purpose": "owner_occupier",
}

def main():
    payload = DEMO
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            payload = json.load(f)
    out = run_underwriting(payload)
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
