# -*- coding: utf-8 -*-
"""
tools/underwriting.py
====================
Deterministic Hong Kong residential underwriting engine.

Extracted from the interactive prototype `HK_住宅投資互動計算機.html`
(and mirrored to `REPE_香港住宅投資模型.xlsx`).

PHILOSOPHY (project-wide):
  * LLM   = reasoning / extraction / orchestration / narrative
  * Code  = ALL financial calculations below (deterministic, testable)
  * Web   = external information (comparables, rental, market, policy)

Nothing in this module calls an LLM. Nothing in this module reads scenario
assumptions from an unvalidated request. Unknowns are surfaced as None / NaN /
explicit "assumption" — never guessed.

Public entry points:
    run_underwriting(payload: dict) -> dict      # dispatcher (legacy schema)
    owner_underwriting(...)                      # owner-occupier
    rental_underwriting(...)                     # buy-to-let / rental
    rental_cashflows(...)                        # full year-by-year chain (no LLM)
    stress_test_owner(...)
    get_avd(price)                               # HK AVD, marginal-relief scale
    # solver helpers (used by the transaction-boundary engine)
    loan_from_payment / price_for_dsr / rate_for_dsr_target
    break_even_rent / break_even_price
"""

from __future__ import annotations

from tools import config as C

# --------------------------------------------------------------------------- #
#  Reference anchors (verified against HTML/Excel in-session)                 #
#  - AVD: $3M->100, $5M->112500, $10M->370000, $20M->750000, $15M->562500     #
#  - Owner 10,000,000 / LTV .70 / 4.125% / 30y: monthly P&I ~ 33,925          #
#    -> down 3,000,000; AVD 370,000 + fees 150,000 = cash needed 3,520,000    #
#    -> DSR (income 60,000) ~ 56.5%                                           #
#  - Rental: EGI 342,000 & rates 21,600 => PropertyTax = 0.12*(EGI-|rates|)   #
#    = 38,448 ; pre-tax NOI 293,040 -> after-tax NOI 254,592                  #
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  Basic financial helpers (pure, deterministic)                              #
# --------------------------------------------------------------------------- #
def pmt(annual_rate: float, n_periods: int, pv: float) -> float:
    """Level monthly payment (negative = outflow). rate is ANNUAL (nominal)."""
    r = annual_rate / 12.0
    if r == 0:
        return -pv / n_periods
    f = (1.0 + r) ** n_periods
    return -(pv * r * f) / (f - 1.0)


def npv(rate: float, cfs: list) -> float:
    return sum(cf / (1.0 + rate) ** i for i, cf in enumerate(cfs))


def irr(cfs: list) -> float:
    """IRR of an equally spaced cash-flow list (year 0 first). NaN if no sign change."""
    pos = any(c > 1 for c in cfs)
    neg = any(c < -1 for c in cfs)
    if not (pos and neg):
        return float("nan")
    lo, hi = -0.95, 5.0
    for _ in range(400):
        m = (lo + hi) / 2.0
        v = npv(m, cfs)
        if abs(v) < 1e-7:
            return m
        if v > 0:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2.0


def _pmt_magnitude(loan: float, annual_rate: float, tenure_years: int, amort: str) -> float:
    """Positive monthly payment (or interest-only)."""
    if str(amort).upper() == "IO":
        return loan * annual_rate / 12.0
    return abs(pmt(annual_rate, tenure_years * 12, loan))


def _remaining_balance(loan: float, annual_rate: float, tenure_years: int, hold_years: int,
                       amort: str) -> float:
    if str(amort).upper() == "IO":
        return loan
    r = annual_rate / 12.0
    n = hold_years * 12
    if n >= tenure_years * 12:
        return 0.0
    # FIX (audit B7): a 0% mortgage divided by zero here, raising ZeroDivisionError
    # inside the report pipeline. pmt() and loan_from_payment() both guard r == 0;
    # this function did not. At r = 0 the balance is simply the straight-line
    # remainder: loan - (loan / n_periods) * months_elapsed.
    if r == 0:
        return loan - (loan / (tenure_years * 12)) * n
    p = abs(pmt(annual_rate, tenure_years * 12, loan))   # positive magnitude
    return loan * (1 + r) ** n - p * (((1 + r) ** n - 1) / r)


def loan_from_payment(monthly_payment: float, annual_rate: float, tenure_years: int,
                      amort: str = "PI") -> float | None:
    """Inverse of pmt(): the loan whose monthly payment equals the given amount."""
    if monthly_payment is None or monthly_payment <= 0:
        return None
    if str(amort).upper() == "IO":
        return monthly_payment * 12.0 / annual_rate if annual_rate > 0 else None
    if tenure_years is None or tenure_years <= 0:
        return None
    r = annual_rate / 12.0
    n = tenure_years * 12
    if r == 0:
        return monthly_payment * n
    return monthly_payment * (1 - (1 + r) ** (-n)) / r


def price_for_dsr(monthly_net_income: float, existing_monthly_debt: float,
                  target_dsr: float, ltv: float, annual_rate: float,
                  tenure_years: int, amort: str = "PI") -> float | None:
    """Largest price whose base DSR equals ``target_dsr`` (deterministic)."""
    if not monthly_net_income or monthly_net_income <= 0 or ltv <= 0:
        return None
    allowed = monthly_net_income * target_dsr - (existing_monthly_debt or 0.0)
    if allowed <= 0:
        return None
    loan = loan_from_payment(allowed, annual_rate, tenure_years, amort)
    return round(loan / ltv, 0) if loan else None


def rate_for_dsr_target(price: float, ltv: float, tenure_years: int,
                        monthly_net_income: float, existing_monthly_debt: float,
                        target_dsr: float, amort: str = "PI",
                        lo: float = 0.0005, hi: float = 0.40) -> float | None:
    """Interest rate at which the DSR hits ``target_dsr`` for a fixed price."""
    if not price or not monthly_net_income or monthly_net_income <= 0 or ltv <= 0:
        return None
    target_payment = monthly_net_income * target_dsr - (existing_monthly_debt or 0.0)
    if target_payment <= 0:
        return None
    loan = price * ltv

    def payment_at(rate: float) -> float:
        return _pmt_magnitude(loan, rate, tenure_years, amort)

    if payment_at(lo) > target_payment:
        return None                      # already above the target at ~0% rate
    if payment_at(hi) < target_payment:
        return round(hi, 6)              # never breaches within the searched range
    a, b = lo, hi
    for _ in range(200):
        m = (a + b) / 2.0
        if payment_at(m) < target_payment:
            a = m
        else:
            b = m
    return round((a + b) / 2.0, 6)


# --------------------------------------------------------------------------- #
#  HK ad valorem stamp duty (AVD) - Scale, incl. marginal-relief bands         #
# --------------------------------------------------------------------------- #
def get_avd(price: float) -> float:
    """HK Ad Valorem Stamp Duty (2024 'full removal'; identical for all buyers).
    Scale 2 with marginal-relief crossing bands, mirroring Inland Revenue."""
    if price <= 4_000_000:
        return 100
    if price <= 4_323_780:
        return 100 + (price - 4_000_000) * 0.20
    if price <= 4_500_000:
        return price * 0.015
    if price <= 4_935_480:
        return 67_500 + (price - 4_500_000) * 0.10
    if price <= 6_000_000:
        return price * 0.0225
    if price <= 6_642_860:
        return 135_000 + (price - 6_000_000) * 0.10
    if price <= 9_000_000:
        return price * 0.030
    if price <= 10_080_000:
        return 270_000 + (price - 9_000_000) * 0.10
    if price <= 20_000_000:
        return price * 0.0375
    if price <= 21_739_120:
        return 750_000 + (price - 20_000_000) * 0.10
    if price <= 100_000_000:
        return price * 0.0425
    return price * 0.065   # > HK$100M (latest budget)


def total_acquisition_cost(price: float, fee_rate: float = 0.015) -> float:
    """Stamp duty + other fees (agency ~1% + legal, editable). Cash, not financed."""
    return get_avd(price) + price * fee_rate


# --------------------------------------------------------------------------- #
#  Owner-occupier underwriting                                                 #
# --------------------------------------------------------------------------- #
def owner_underwriting(price, ltv=0.60, annual_rate=0.04125, tenure_years=30,
                       amort="PI", monthly_net_income=None, existing_monthly_debt=0.0,
                       monthly_mgmt_rates=C.DEFAULT_MONTHLY_MGMT_RATES,
                       fee_rate=C.DEFAULT_FEE_RATE, area_sqft=None,
                       other_recurring_income=0.0, rates_amount=0.0,
                       mortgage_fees=0.0, valuation_fee=0.0,
                       renovation_furnishing=0.0, initial_reserve=0.0):
    """Owner-occupier affordability + acquisition cash.

    Optional acquisition line items default to 0, so the PHASE-3 anchor
    (10M / LTV .70 / fee_rate .015 -> initial_cash 3,520,000) is unchanged.
    The management-fee and fee-rate fallbacks come from tools/config.py.
    """
    loan = price * ltv
    down = price - loan
    acq = total_acquisition_cost(price, fee_rate)
    extra_upfront = (float(mortgage_fees or 0.0) + float(valuation_fee or 0.0)
                     + float(renovation_furnishing or 0.0) + float(initial_reserve or 0.0))
    initial_cash = down + acq + extra_upfront
    pmt_m = _pmt_magnitude(loan, annual_rate, tenure_years, amort)
    housing_cost = pmt_m + monthly_mgmt_rates
    recognised_income = ((monthly_net_income + (other_recurring_income or 0.0))
                         if monthly_net_income is not None else None)
    surpl = (recognised_income - housing_cost - existing_monthly_debt
             if recognised_income is not None else None)
    dsr = None
    if recognised_income:
        dsr = (pmt_m + existing_monthly_debt) / recognised_income

    # Affordability score (mirrors HTML buildOwn): DSR 40% | LTV 30% | surplus buffer 30%
    def _clamp10(x):
        return max(0.0, min(10.0, x))

    s_dsr = _clamp10((0.5 - dsr) / 0.2 * 10) if dsr is not None else 0.0
    s_ltv = _clamp10(10 - (ltv - 0.4) / 0.5 * 10)
    s_buf = _clamp10((surpl / 15000 * 10) if surpl is not None else 0.0)
    score = round((s_dsr * 0.4 + s_ltv * 0.3 + s_buf * 0.3) * 10) / 10

    return {
        "loan_amount": round(loan, 2),
        "down_payment": round(down, 2),
        "stamp_duty": round(get_avd(price), 2),
        "other_transaction_cost": round(price * fee_rate, 2),
        "acquisition_cost": round(acq, 2),
        "extra_upfront_cost": round(extra_upfront, 2),
        "initial_cash_required": round(initial_cash, 2),
        "total_initial_cash_required": round(initial_cash, 2),
        "monthly_payment": round(pmt_m, 2),
        "monthly_housing_cost": round(housing_cost, 2),
        "monthly_mgmt_rates": round(monthly_mgmt_rates, 2),
        "recognised_income": round(recognised_income, 2) if recognised_income is not None else None,
        "dsr": round(dsr, 6) if dsr is not None else None,
        "monthly_surplus": round(surpl, 2) if surpl is not None else None,
        "affordability_score": score,
        "price_psf": round(price / area_sqft, 2) if area_sqft else None,
        "annual_housing_cost": round(housing_cost * 12.0, 2),
        "risks": _owner_risks(dsr, ltv, surpl),
    }


def _owner_risks(dsr, ltv, surplus):
    r = []
    if dsr is not None and dsr > 0.50:
        r.append("high_dsr: mortgage payment exceeds 50% of income")
    if dsr is not None and dsr > 0.40:
        r.append("elevated_dsr: payment > 40% of income (thin headroom)")
    if ltv > 0.70:
        r.append("high_leverage: LTV > 70% (needs mortgage insurance)")
    if surplus is not None and surplus < 0:
        r.append("negative_surplus: income does not cover housing cost")
    if not r:
        r.append("no_major_flag")
    return r


# --------------------------------------------------------------------------- #
#  Rental (buy-to-let) cash-flow chain                                         #
# --------------------------------------------------------------------------- #
def rental_cashflows(price, area_sqft, monthly_rent, vacancy=0.05, rates_pct=0.06,
                     opex_pct=0.08, opex_growth=0.025, annual_capex=10000.0,
                     ltv=0.60, annual_rate=0.04125, tenure_years=30, amort="PI",
                     rent_growth=0.03, hold_years=5, exit_cap=0.035,
                     sell_cost=0.01, fee_rate=0.015, disc_rate=0.06):
    """Year-by-year deterministic rental model. Returns every array the report needs.

    Income chain (mirrors the prototype HTML):
        PGI        = 12 x monthly rent, growing at rent_growth
        EGI        = PGI x (1 - vacancy)
        rates      = -PGI x rates_pct                      (差餉 + 地租)
        opex       = -EGI x opex_pct, growing at opex_growth (Year 1 base)
        pre-tax NOI= EGI + rates + opex
        property tax = 0.12 x max(0, EGI - |rates|)        ((rent-rates) x 80% x 15%)
        after-tax NOI = pre-tax NOI - property tax
    """
    hold_years = int(hold_years)
    acq = total_acquisition_cost(price, fee_rate)
    loan = price * ltv
    pmt_m = _pmt_magnitude(loan, annual_rate, tenure_years, amort)
    ads = pmt_m * 12.0

    n = hold_years + 2
    pgi = [0.0] * n
    egi = [0.0] * n
    rat = [0.0] * n
    opx = [0.0] * n
    ptx = [0.0] * n
    npre = [0.0] * n
    naft = [0.0] * n
    pgi[1] = monthly_rent * 12.0
    for y in range(2, n):
        pgi[y] = pgi[y - 1] * (1 + rent_growth)
    for y in range(1, n):
        egi[y] = pgi[y] * (1 - vacancy)
        rat[y] = -pgi[y] * rates_pct
        opx[y] = -egi[y] * opex_pct if y == 1 else opx[y - 1] * (1 + opex_growth)
        npre[y] = egi[y] + rat[y] + opx[y]
        ptx[y] = 0.12 * max(0.0, egi[y] - abs(rat[y]))    # (rent−rates)*80%*15%
        naft[y] = npre[y] - ptx[y]

    opcf, sale, unlev, lev, dscr, cash_after_debt = [], [], [], [], [], []
    for y in range(hold_years + 1):
        opcf.append(naft[y] - annual_capex if y >= 1 else 0.0)
        sale.append((npre[y + 1] / exit_cap) * (1 - sell_cost)
                    if (y == hold_years and y >= 1 and exit_cap) else 0.0)
        unlev.append(-(price + acq) if y == 0 else opcf[y] + sale[y])
        pay = _remaining_balance(loan, annual_rate, tenure_years, hold_years, amort) if y == hold_years else 0.0
        lev.append(unlev[0] + loan if y == 0 else unlev[y] - ads - pay)
        cash_after_debt.append(0.0 if y == 0 else opcf[y] - ads)
        dscr.append(npre[y] / ads if (y >= 1 and ads > 0) else None)

    equity = -lev[0]
    exit_value = (npre[hold_years + 1] / exit_cap) if (hold_years >= 1 and exit_cap) else 0.0
    remaining = _remaining_balance(loan, annual_rate, tenure_years, hold_years, amort)
    return {
        "price": price, "area_sqft": area_sqft, "loan": loan, "acq": acq,
        "annual_debt_service": ads, "monthly_payment": pmt_m,
        "pgi": pgi, "egi": egi, "rates": rat, "opex": opx, "property_tax": ptx,
        "noi_pre_tax": npre, "noi_after_tax": naft,
        "operating_cf": opcf, "sale_proceeds": sale, "unlevered_cf": unlev,
        "levered_cf": lev, "dscr": dscr, "cash_after_debt": cash_after_debt,
        "equity_invested": equity, "exit_value": exit_value,
        "remaining_balance": remaining,
        "exit_proceeds_net": exit_value * (1 - sell_cost) - remaining,
        "total_distributions": sum(c for c in lev if c > 0),
    }


def rental_underwriting(price, area_sqft, monthly_rent, vacancy=0.05, rates_pct=0.06,
                        opex_pct=0.08, opex_growth=0.025, annual_capex=10000.0,
                        ltv=0.60, annual_rate=0.04125, tenure_years=30, amort="PI",
                        rent_growth=0.03, hold_years=5, exit_cap=0.035,
                        sell_cost=0.01, fee_rate=C.DEFAULT_FEE_RATE,
                        disc_rate=C.DEFAULT_DISCOUNT_RATE,
                        mortgage_fees=0.0, valuation_fee=0.0,
                        renovation_furnishing=0.0, initial_reserve=0.0):
    """Full rental underwriting: yield / NOI / cash flow / DSCR / IRR / multiple.

    Legacy keys are preserved for the PHASE-3 anchors; the extended metric block
    is additive.
    """
    extra_upfront = (float(mortgage_fees or 0.0) + float(valuation_fee or 0.0)
                     + float(renovation_furnishing or 0.0) + float(initial_reserve or 0.0))
    cf = rental_cashflows(price=price, area_sqft=area_sqft, monthly_rent=monthly_rent,
                          vacancy=vacancy, rates_pct=rates_pct, opex_pct=opex_pct,
                          opex_growth=opex_growth, annual_capex=annual_capex, ltv=ltv,
                          annual_rate=annual_rate, tenure_years=tenure_years, amort=amort,
                          rent_growth=rent_growth, hold_years=hold_years, exit_cap=exit_cap,
                          sell_cost=sell_cost, fee_rate=fee_rate, disc_rate=disc_rate)
    acq, loan, ads = cf["acq"], cf["loan"], cf["annual_debt_service"]
    npre, naft, dscr = cf["noi_pre_tax"], cf["noi_after_tax"], cf["dscr"]
    unlev, lev = cf["unlevered_cf"], cf["levered_cf"]
    equity = cf["equity_invested"] + extra_upfront
    pos = cf["total_distributions"]

    annual_cf_y1 = naft[1] - annual_capex - ads
    noi_y1 = npre[1]
    min_dscr = min(x for x in dscr if x is not None)
    coeff = _noi_coefficient(vacancy, rates_pct, opex_pct)

    return {
        # ---- legacy / anchor keys --------------------------------------- #
        "price": round(price, 2), "acquisition_cost": round(acq, 2),
        "loan_amount": round(loan, 2), "ads": round(ads, 2),
        "price_psf": round(price / area_sqft, 2) if area_sqft else None,
        "rent_psf": round(monthly_rent / area_sqft, 2) if area_sqft else None,
        "going_in_cap_pre_tax": round(npre[1] / price, 6),
        "rental_yield_after_tax": round(naft[1] / price, 6),
        "min_dscr": round(min_dscr, 6),
        "unlevered_irr": round(irr(unlev), 6),
        "levered_irr": round(irr(lev), 6),
        "unlevered_npv": round(npv(disc_rate, unlev), 2),
        "levered_npv": round(npv(disc_rate, lev), 2),
        "equity_multiple": round(pos / equity, 4) if equity > 0 else None,
        "after_tax_noi_y1": round(naft[1], 2),
        "monthly_cash_flow_after_tax": round(annual_cf_y1 / 12.0, 2),
        "safety_score": _rental_safety(ltv, dscr, naft[1], price, annual_rate, cf["egi"][1], ads),
        "risks": _rental_risks(dscr, ads, naft[1], annual_capex, ltv, cf["egi"][1]),

        # ---- deterministic rental metric block (spec #10) --------------- #
        "gross_rental_income_y1": round(cf["pgi"][1], 2),
        "effective_gross_income_y1": round(cf["egi"][1], 2),
        "rates_y1": round(abs(cf["rates"][1]), 2),
        "opex_y1": round(abs(cf["opex"][1]), 2),
        "property_tax_y1": round(cf["property_tax"][1], 2),
        "noi_pre_tax_y1": round(noi_y1, 2),
        "noi_after_tax_y1": round(naft[1], 2),
        "annual_debt_service": round(ads, 2),
        "monthly_payment": round(cf["monthly_payment"], 2),
        "annual_cash_flow_y1": round(annual_cf_y1, 2),
        "monthly_cash_flow_y1": round(annual_cf_y1 / 12.0, 2),
        "gross_yield": round(cf["pgi"][1] / price, 6),
        "net_yield_pre_tax": round(noi_y1 / price, 6),
        "net_yield": round(naft[1] / price, 6),
        "cap_rate_going_in": round(noi_y1 / price, 6),
        "cash_on_cash": round(annual_cf_y1 / equity, 6) if equity > 0 else None,
        "initial_cash_required": round(price + acq - loan + extra_upfront, 2),
        "equity_invested": round(equity, 2),
        "cash_flow_by_year": [round(c, 2) for c in cf["levered_cf"]],
        "operating_cash_flow_by_year": [round(c, 2) for c in cf["cash_after_debt"]],
        "noi_pre_tax_by_year": [round(x, 2) for x in npre[1:]],
        "noi_after_tax_by_year": [round(x, 2) for x in naft[1:]],
        "dscr_by_year": [round(x, 4) if x is not None else None for x in dscr],
        "hold_years": hold_years,
        "exit_cap": exit_cap,
        "exit_value": round(cf["exit_value"], 2),
        "remaining_balance_at_exit": round(cf["remaining_balance"], 2),
        "exit_proceeds_net": round(cf["exit_proceeds_net"], 2),
        "total_distributions": round(pos, 2),
        "disc_rate": disc_rate,
        "break_even_rent": _be_rent(price, ads, coeff, 1.00, area_sqft),
        "break_even_rent_dscr_target": _be_rent(price, ads, coeff, 1.25, area_sqft),
        "break_even_price": break_even_price(noi_y1, ltv, annual_rate, tenure_years,
                                             amort, 1.00),
        "break_even_price_dscr_target": break_even_price(noi_y1, ltv, annual_rate,
                                                         tenure_years, amort, 1.25),
        "noi_coefficient": round(coeff, 6),
    }


def _noi_coefficient(vacancy, rates_pct, opex_pct) -> float:
    """pre-tax NOI per HK$1 of PGI in year 1 (before capex)."""
    occ = 1.0 - vacancy
    return occ - rates_pct - occ * opex_pct


def _be_rent(price, ads, coeff, target_dscr, area_sqft):
    if coeff <= 0 or ads <= 0:
        return None
    pgi = target_dscr * ads / coeff
    return {"monthly_rent": round(pgi / 12.0, 0),
            "annual_pgi": round(pgi, 0),
            "rent_psf": round(pgi / 12.0 / area_sqft, 2) if area_sqft else None,
            "target_dscr": target_dscr}


def break_even_rent(price, ltv, annual_rate, tenure_years, amort, vacancy, rates_pct,
                    opex_pct, target_dscr=1.0, area_sqft=None):
    """Monthly rent at which DSCR equals ``target_dscr`` (deterministic)."""
    loan = price * ltv
    ads = _pmt_magnitude(loan, annual_rate, tenure_years, amort) * 12.0
    return _be_rent(price, ads, _noi_coefficient(vacancy, rates_pct, opex_pct),
                    target_dscr, area_sqft)


def break_even_price(noi_pre_tax_y1, ltv, annual_rate, tenure_years, amort="PI",
                     target_dscr=1.0):
    """Largest price whose DSCR at year-1 NOI equals ``target_dscr``."""
    if not noi_pre_tax_y1 or noi_pre_tax_y1 <= 0 or ltv <= 0 or target_dscr <= 0:
        return None
    allowed_payment = noi_pre_tax_y1 / (target_dscr * 12.0)   # monthly ADS allowed
    loan = loan_from_payment(allowed_payment, annual_rate, tenure_years, amort)
    return round(loan / ltv, 0) if loan else None


def _rental_safety(ltv, dscr, naft1, price, annual_rate, egi1, ads):
    def c(x): return max(0.0, min(10.0, x))
    s_ltv = c(10 - (ltv - 0.4) / 0.5 * 10)
    d1 = next(x for x in dscr if x is not None)
    s_dscr = c((d1 - 0.8) / 0.8 * 10)
    yd_after = naft1 / price
    s_spread = c((yd_after - annual_rate) / 0.04 * 10)
    s_cov = c((egi1 / ads - 1) * 10)
    return round((s_ltv * 0.25 + s_dscr * 0.30 + s_spread * 0.25 + s_cov * 0.20) * 10) / 10


def _rental_risks(dscr, ads, naft1, annual_capex, ltv, egi1):
    r = []
    if dscr[1] is not None and dscr[1] < 1.0:
        r.append("weak_dscr: pre-tax NOI < debt service (negative cash flow)")
    if egi1 - ads < 0:
        r.append("rent_shortfall: gross rent < annual mortgage payment")
    if naft1 - annual_capex - ads < 0:
        r.append("negative_after_tax_cash_flow")
    if ltv > 0.70:
        r.append("high_leverage")
    if not r:
        r.append("no_major_flag")
    return r


# --------------------------------------------------------------------------- #
#  Stress tests                                                               #
# --------------------------------------------------------------------------- #
def stress_test_owner(**base):
    base = dict(base)
    out = {}

    def variant(rate=None, price=None, income=None, mgmt=None):
        p = dict(base)
        if rate is not None:
            p["annual_rate"] = rate
        if price is not None:
            p["price"] = price
        if income is not None:
            p["monthly_net_income"] = income
        if mgmt is not None:
            p["monthly_mgmt_rates"] = mgmt
        return owner_underwriting(**p)

    out["base"] = owner_underwriting(**base)
    out["rate_+1%"] = variant(base["annual_rate"] + 0.01)
    out["rate_+2%"] = variant(base["annual_rate"] + 0.02)
    out["price_-10%"] = variant(None, price=base["price"] * 0.90)
    out["price_-15%"] = variant(None, price=base["price"] * 0.85)
    out["income_-10%"] = variant(
        None, None, income=base["monthly_net_income"] * 0.90 if base["monthly_net_income"] else None)
    out["expense_higher"] = variant(None, None, mgmt=base.get("monthly_mgmt_rates", 0) + 1500)
    return out


def rate_sensitivity(kind: str, base_kwargs: dict, steps=None) -> list:
    """Interest-rate stress table (spec #12) for owner or rental underwriting."""
    from tools import config as _C
    steps = list(steps if steps is not None else _C.RATE_STRESS_STEPS)
    base_rate = base_kwargs.get("annual_rate", 0.04125)
    rows = []
    for step in [0.0] + steps:
        kw = dict(base_kwargs)
        kw["annual_rate"] = base_rate + step
        if kind == "rental":
            r = rental_underwriting(**kw)
            rows.append({
                "scenario": "base" if step == 0 else f"rate_+{step * 100:.2f}pp",
                "rate": round(base_rate + step, 6),
                "rate_delta_pp": round(step * 100, 3),
                "is_base": step == 0,
                "monthly_payment": r["monthly_payment"],
                "dsr": None,
                "annual_debt_service": r["annual_debt_service"],
                "annual_cash_flow": r["annual_cash_flow_y1"],
                "monthly_cash_flow": r["monthly_cash_flow_y1"],
                "min_dscr": r["min_dscr"],
                "net_yield": r["net_yield"],
                "noi_pre_tax_y1": r["noi_pre_tax_y1"],
                "levered_irr": r["levered_irr"],
                "equity_multiple": r["equity_multiple"],
            })
        else:
            r = owner_underwriting(**kw)
            rows.append({
                "scenario": "base" if step == 0 else f"rate_+{step * 100:.2f}pp",
                "rate": round(base_rate + step, 6),
                "rate_delta_pp": round(step * 100, 3),
                "is_base": step == 0,
                "monthly_payment": r["monthly_payment"],
                "monthly_housing_cost": r["monthly_housing_cost"],
                "dsr": r["dsr"],
                "monthly_surplus": r["monthly_surplus"],
                "affordability_score": r["affordability_score"],
            })
    # deterioration vs base
    b = rows[0]
    for row in rows[1:]:
        if b["monthly_payment"]:
            row["payment_increase_pct"] = round(
                (row["monthly_payment"] - b["monthly_payment"]) / b["monthly_payment"], 6)
        if kind == "rental":
            row["cash_flow_deterioration"] = round(row["annual_cash_flow"] - b["annual_cash_flow"], 2)
            row["annual_debt_service_increase"] = round(
                row["annual_debt_service"] - b["annual_debt_service"], 2)
        else:
            if b["dsr"] is not None and row["dsr"] is not None:
                row["dsr_increase"] = round(row["dsr"] - b["dsr"], 6)
            if b["monthly_surplus"] is not None and row["monthly_surplus"] is not None:
                row["surplus_deterioration"] = round(
                    row["monthly_surplus"] - b["monthly_surplus"], 2)
    return rows


# --------------------------------------------------------------------------- #
#  Public dispatcher (agent-facing entry point, legacy schema)                 #
# --------------------------------------------------------------------------- #
def run_underwriting(payload: dict) -> dict:
    """Dispatch a structured request (see schema in README / tools/schema.py)."""
    prop = payload.get("property", {})
    fin = payload.get("financing", {})
    user = payload.get("user", payload.get("finance", {}))
    purpose = payload.get("purpose", "owner_occupier")

    price = prop.get("price", prop.get("asking_price"))
    area = prop.get("usable_area_sqft", prop.get("area_sqft"))

    if price is None:
        raise ValueError("property.price is required")

    fee_rate = fin.get("fee_rate", C.DEFAULT_FEE_RATE)

    if purpose in ("owner_occupier", "self_use"):
        result = owner_underwriting(
            price=price,
            ltv=fin.get("ltv", C.DEFAULT_OWNER_LTV),
            annual_rate=fin.get("mortgage_rate", C.DEFAULT_MORTGAGE_RATE),
            tenure_years=fin.get("tenure_years", C.DEFAULT_TENURE_YEARS),
            amort=fin.get("amortization", C.DEFAULT_AMORTIZATION),
            monthly_net_income=user.get("monthly_net_income"),
            existing_monthly_debt=user.get("existing_monthly_debt", 0),
            monthly_mgmt_rates=user.get("monthly_mgmt_rates", C.DEFAULT_MONTHLY_MGMT_RATES),
            fee_rate=fee_rate,
            area_sqft=area,
            other_recurring_income=user.get("other_recurring_income", 0.0),
        )
        result["purpose"] = "owner_occupier"
        return result

    if purpose in ("rental", "investment", "buy_to_let"):
        rental = payload.get("rental", prop)
        result = rental_underwriting(
            price=price,
            area_sqft=area,
            monthly_rent=rental.get("monthly_rent"),
            vacancy=rental.get("vacancy", 0.05),
            rates_pct=rental.get("rates_pct", 0.06),
            opex_pct=rental.get("opex_pct", 0.08),
            annual_capex=rental.get("annual_capex", 10000),
            ltv=fin.get("ltv", C.DEFAULT_OWNER_LTV),
            annual_rate=fin.get("mortgage_rate", C.DEFAULT_MORTGAGE_RATE),
            tenure_years=fin.get("tenure_years", C.DEFAULT_TENURE_YEARS),
            amort=fin.get("amortization", C.DEFAULT_AMORTIZATION),
            hold_years=rental.get("hold_years", 5),
            exit_cap=rental.get("exit_cap", 0.035),
            sell_cost=rental.get("sell_cost", 0.01),
            rent_growth=rental.get("rent_growth", 0.03),
            fee_rate=fee_rate,
            disc_rate=fin.get("discount_rate", C.DEFAULT_DISCOUNT_RATE),
        )
        result["purpose"] = "rental"
        return result

    raise ValueError(f"unsupported purpose: {purpose}")
