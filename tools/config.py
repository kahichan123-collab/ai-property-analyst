# -*- coding: utf-8 -*-
"""tools/config.py — centralised system configuration.

Spec references:
  #9  acquisition cost model            -> ACQUISITION_DEFAULTS
  #11 scenario engine                   -> SCENARIOS
  #12 interest-rate stress testing      -> RATE_STRESS_STEPS
  #13 bank vs personal affordability    -> AFFORDABILITY_BANDS
  #14 valuation engine                  -> VALUATION_BANDS
  #15 market regime engine              -> REGIME_RULES
  #16 risk engine                       -> RISK_THRESHOLDS
  #17 transaction boundary              -> BOUNDARY_TARGETS
  #20 data integrity / uncertainty      -> provenance vocabulary

Every *policy* number lives here only. Scenario shocks are deliberately NOT in
the frontend: the UI may display them but never owns them, and the engine never
reads scenario assumptions from a request body it did not validate.

Pure data + tiny pure helpers. No I/O, no LLM, no side effects.
"""
from __future__ import annotations

ENGINE_VERSION = "2.0.0"
SCHEMA_VERSION = "2.0"

# --------------------------------------------------------------------------- #
#  Provenance vocabulary (spec #20)                                            #
# --------------------------------------------------------------------------- #
OBSERVED = "Observed"                     # read from a source of record
USER = "User Input"                       # typed by the user
ESTIMATED = "Estimated"                   # derived from other data, some error
ASSUMED = "Assumption"                    # a stated default, not evidence
RESEARCH = "Research Approximation"       # from news/research, approximate
MODEL = "Model Output"                    # produced by the deterministic engine

PROVENANCE_ORDER = [OBSERVED, USER, ESTIMATED, ASSUMED, RESEARCH, MODEL]

# --------------------------------------------------------------------------- #
#  Interest-rate stress (spec #12)                                             #
# --------------------------------------------------------------------------- #
# Framing is deliberately NOT "the bank will approve on this basis".
RATE_STRESS_STEPS = [0.01, 0.02]          # +1.00pp, +2.00pp
RATE_STRESS_FRAMING = ("Mortgage Affordability Stress Test / Interest Rate "
                       "Sensitivity — an internal sensitivity analysis, NOT a "
                       "statement of bank approval policy.")
BANK_DSR_CAP = 0.50                       # commonly cited HKMA/bank guideline
PERSONAL_DSR_COMFORT = 0.40               # personal-comfort benchmark (our policy)

# --------------------------------------------------------------------------- #
#  Acquisition cost model (spec #9)                                            #
# --------------------------------------------------------------------------- #
ACQUISITION_DEFAULTS = {
    "agency_fee_pct": 0.01,      # buyer-side agency commission (customary 1%)
    "legal_fee_pct": 0.005,      # conveyancing + disbursements ≈ 0.5% of price
    "mortgage_fees": 0.0,        # bank fees / mortgage insurance — explicit input
    "valuation_fee": 0.0,        # bank valuation fee — explicit input
    "renovation_furnishing": 0.0,
    "initial_reserve": 0.0,
}
RESERVE_MONTHS_RECOMMENDED = 6    # advisory only; never silently added

# --------------------------------------------------------------------------- #
#  Fallback defaults used when a request omits a value.                        #
#  Every number the engine assumes lives HERE — one place to audit / change.   #
#                                                                              #
#  Scope: these are the fallbacks of the DETERMINISTIC ENGINE (orchestrator,   #
#  boundary, scenarios, underwriting). tools/schema.py and app/index.html also #
#  reference the financing ones, so that a normalised request and a direct     #
#  engine call can never disagree about an omitted field.                      #
#  NOT here: the underwriting helpers' own calculation defaults (unrelated to  #
#  policy), the scenario shocks (SCENARIOS above), and the acquisition cost    #
#  model's own ACQUISITION_DEFAULTS.                                           #
#                                                                              #
#  MAINTENANCE: changing a value here takes effect on the next import/restart  #
#  — the underwriting function signatures and FIELD_SPECS bind these constants #
#  at import time (the CLI dispatcher and _rental_kwargs read them per call).  #
#  No code anywhere else needs editing; that is the point of this block.       #
# --------------------------------------------------------------------------- #
DEFAULT_MONTHLY_MGMT_RATES = 1200.0   # HK$/month, assumed when not provided
DEFAULT_FEE_RATE           = 0.015    # agency+legal combined fallback (2:1 split)
DEFAULT_DISCOUNT_RATE      = 0.06     # NPV discount-rate fallback
DEFAULT_OWNER_LTV          = 0.60     # used when the request omits financing.ltv
DEFAULT_MORTGAGE_RATE      = 0.04125  # 4.125% p.a., used when the rate is omitted
DEFAULT_TENURE_YEARS       = 30
DEFAULT_AMORTIZATION       = "PI"     # principal & interest

# --------------------------------------------------------------------------- #
#  Owner-occupier hold assumption (only used for owner-mode exit illustration) #
# --------------------------------------------------------------------------- #
OWNER_HOLD_YEARS_DEFAULT = 5

# --------------------------------------------------------------------------- #
#  Scenario engine (spec #11)                                                  #
# --------------------------------------------------------------------------- #
# Rental scenarios shock the cash-flow model (rent, vacancy, growth, rate, exit
# capitalisation rate).  Owner scenarios shock the rate/expense path and use the
# explicit market-value growth assumption below for the illustrative exit.
SCENARIOS = {
    "bear": {
        "label": "BEAR",
        "label_zh": "悲觀情境",
        "description": "租金下跌、空置上升、增長放緩、利率上升、退出估值轉弱。",
        "rent_mult": 0.90,          # -10% rent
        "vacancy_add": 0.05,        # +5pp vacancy
        "rent_growth_mult": 0.50,   # growth halved
        "rate_add": 0.01,           # +1.00pp
        "exit_cap_mult": 1.15,      # cap expands -> exit value falls
        "value_growth_annual": -0.02,   # owner-mode market value assumption
        "opex_mult": 1.15,
    },
    "base": {
        "label": "BASE",
        "label_zh": "基準情境",
        "description": "完全採用使用者輸入的假設，不作任何額外衝擊。",
        "rent_mult": 1.00,
        "vacancy_add": 0.0,
        "rent_growth_mult": 1.00,
        "rate_add": 0.0,
        "exit_cap_mult": 1.00,
        "value_growth_annual": 0.0,
        "opex_mult": 1.00,
    },
    "bull": {
        "label": "BULL",
        "label_zh": "樂觀情境",
        "description": "租金上升、空置下降、增長較強、利率持平或下降、退出估值較強。",
        "rent_mult": 1.08,          # +8% rent
        "vacancy_add": -0.02,       # -2pp vacancy (floored at 0)
        "rent_growth_mult": 1.50,
        "rate_add": -0.0025,        # -0.25pp (floored)
        "exit_cap_mult": 0.92,      # cap compresses -> exit value rises
        "value_growth_annual": 0.03,
        "opex_mult": 0.95,
    },
}
SCENARIO_ORDER = ["bear", "base", "bull"]

# --------------------------------------------------------------------------- #
#  Affordability interpretation bands (spec #13)                               #
# --------------------------------------------------------------------------- #
AFFORDABILITY_BANDS = {
    # (upper_bound_exclusive, label) evaluated in order on base DSR
    "dsr": [(0.35, "comfortable"), (0.45, "manageable"), (0.50, "stretched"),
            (0.60, "over_extended"), (99.0, "unaffordable")],
    "stress_dsr": [(0.45, "comfortable"), (0.55, "manageable"), (0.60, "stretched"),
                   (0.70, "over_extended"), (99.0, "unaffordable")],
}
INDICATIVE_FINANCING_NOTE = (
    "Indicative financing capacity only. Actual bank approval depends on the "
    "lender's own income recognition, credit assessment, stress-test rate and "
    "property valuation — this model does not predict it."
)

# --------------------------------------------------------------------------- #
#  Valuation bands (spec #14)                                                  #
# --------------------------------------------------------------------------- #
VALUATION_BANDS = {
    # premium = asking / fair_value - 1   (fraction)
    "very_undervalued_lt": -0.15,
    "undervalued_lt": -0.05,
    "expensive_gte": 0.05,
    "very_expensive_gte": 0.15,
}
VALUATION_LABELS = {
    "very_undervalued": "明顯低於估值 / Well below fair value",
    "undervalued": "低於估值 / Undervalued",
    "fair": "估值合理 / Fairly Valued",
    "expensive": "高於估值 / Expensive",
    "very_expensive": "明顯高於估值 / Well above fair value",
    "insufficient_data": "資料不足 / Insufficient Data",
}
FAIR_VALUE_DISCOUNT_BUFFER = 0.05   # buy-target buffer below fair value

# --------------------------------------------------------------------------- #
#  Market regime engine (spec #15)                                             #
# --------------------------------------------------------------------------- #
REGIME_ORDER = ["stress", "correction", "neutral", "expansion", "recovery"]
REGIME_LABELS = {
    "recovery": "Recovery 復甦",
    "expansion": "Expansion 擴張",
    "neutral": "Neutral 中性",
    "correction": "Correction 調整",
    "stress": "Stress 壓力",
}
# score is summed from detected signals; higher = healthier market.
REGIME_SCORE_BANDS = [(-4, "stress"), (-1, "correction"), (1, "neutral"),
                      (4, "expansion"), (99, "recovery")]
REGIME_TOPICS = ["interest_rate", "mortgage", "transaction_volume",
                 "price_trend", "rental_trend", "supply", "policy", "macro"]
REGIME_SIGNAL_RULES = {
    # topic -> (positive keywords, negative keywords)
    "interest_rate": (["減息", "下調", "回落", "cut", "lower", "fall"], ["加息", "上升", "上調", "hike", "higher", "rise"]),
    "mortgage": (["寬鬆", "放寬", "低位", "easing", "loose"], ["收緊", "緊縮", "tighten", "high"]),
    "transaction_volume": (["倍升", "上升", "增加", "活躍", "新高", "rise", "up"],
                           ["下跌", "萎縮", "減少", "淡靜", "放緩", "fall", "down"]),
    "price_trend": (["上升", "回升", "向好", "新高", "升", "rise", "up"], ["下跌", "回落", "下滑", "跌", "fall", "down"]),
    "rental_trend": (["上升", "回升", "租金升", "rise", "up"], ["下跌", "回落", "跌", "fall", "down"]),
    "supply": (["供應緊張", "供應少"], ["供應增加", "供應充足", "新供應", "供應多"]),
    "policy": (["撤辣", "放寬", "利好", "支持"], ["加辣", "收緊", "不利"]),
    "macro": (["復甦", "增長", "改善", "樂觀"], ["衰退", "放緩", "惡化", "悲觀"]),
}

# --------------------------------------------------------------------------- #
#  Risk engine (spec #16)                                                      #
# --------------------------------------------------------------------------- #
RISK_LEVELS = ["low", "medium", "high", "very_high"]
RISK_LEVEL_LABELS = {
    "low": "Low 低",
    "medium": "Medium 中",
    "high": "High 高",
    "very_high": "Very High 極高",
}
RISK_THRESHOLDS = {
    "ltv_high": 0.70,
    "ltv_very_high": 0.80,
    "dsr_elevated": 0.40,
    "dsr_high": 0.50,
    "dsr_very_high": 0.60,
    "stress_dsr_high": 0.60,
    "stress_dsr_very_high": 0.70,
    "dscr_weak": 1.00,
    "dscr_target": 1.25,
    "payment_increase_high": 0.15,     # +2pp rate moves payment by >15%
    "payment_increase_very_high": 0.25,
    "net_yield_low": 0.025,
    "net_yield_very_low": 0.015,
    "premium_high": 0.08,
    "premium_very_high": 0.15,
    "building_age_old": 40,            # years
    "building_age_very_old": 55,
    "maintenance_psf_high": 6.0,       # HK$/sqft/month mgmt+rates
    "liquidity_cash_ratio_high": 0.80,   # price / (price + liquid cash)
    "liquidity_cash_ratio_very_high": 0.92,
    "exit_cap_compression": 0.005,     # exit cap below going-in cap by >0.5pp
}
RISK_CATALOG = {
    "high_ltv": ("高槓桿 High LTV",
                 "貸款成數偏高 → 每月供款對利率與樓價下跌更敏感。",
                 "利率 +2pp 時供款上升；樓價下跌時可能出現負資產。",
                 "降低 LTV、增加首期，或選擇較長年期以降低月供。"),
    "weak_income_coverage": ("入息覆蓋偏弱 Weak income coverage",
                             "每月供款佔入息比例偏高，收入一旦波動即難以維持。",
                             "DSR 上升；失業或減薪會直接造成供款違約風險。",
                             "提高首期以降低貸款額，或選擇較低樓價的單位。"),
    "high_dsr_stress": ("壓力下供款過重 High stress DSR",
                        "在利率上升情境下，供款佔比超越銀行與個人舒適水平。",
                        "壓力情境下每月盈餘轉負，需動用儲備供樓。",
                        "預留更大現金緩衝，或下調買入價以減少貸款額。"),
    "negative_rental_cash_flow": ("租金現金流為負 Negative rental cash flow",
                                  "租金收入不足以支付供款與支出，需每月補貼。",
                                  "每年現金流出，長期蠶食儲備並降低回報。",
                                  "提高租金、降低買入價、增加首期或延長年期。"),
    "high_interest_rate_sensitivity": ("利率敏感度高 High rate sensitivity",
                                       "供款對利率變動的彈性大（浮息按揭尤甚）。",
                                       "利率 +2pp 時供款大幅上升，現金流惡化。",
                                       "考慮定息或上限利率按揭，或降低貸款額。"),
    "low_rental_yield": ("租金回報偏低 Low rental yield",
                         "淨租金回報低於按揭成本，形成負利差。",
                         "持有期間現金流受壓，總回報依賴資本增值。",
                         "以更低價格買入，或選擇租金回報較高的地區／單位。"),
    "high_asking_premium": ("開價溢價偏高 High asking premium",
                            "開價明顯高於可比基準推算的公允價值。",
                            "即時帳面溢價，議價空間不足會拉低 IRR。",
                            "以公允價值為錨議價，或轉看其他盤源。"),
    "old_building_risk": ("樓齡風險 Building age",
                          "樓齡偏高影響按揭年期、維修成本與轉售流動性。",
                          "銀行或縮短年期／降低估值；大維修可能一次過支出。",
                          "預留維修儲備、確認大維修紀錄，並考慮較短持有期。"),
    "maintenance_exposure": ("維修支出風險 Maintenance exposure",
                             "管理費＋差餉或年度維修預備佔比較高。",
                             "淨現金流被侵蝕，實際回報低於表面租金回報。",
                             "核實管理費與大維修計劃，並將支出計入模型。"),
    "liquidity_risk": ("流動性風險 Liquidity risk",
                       "交易所需現金接近或超過可動用資金。",
                       "完成交易後儲備不足，突發支出或失業即出現財務壓力。",
                       "保留應急儲備、降低首期以外的額外支出或調低買入價。"),
    "concentration_risk": ("集中度風險 Concentration risk",
                           "單一物業佔個人資產比重過高。",
                           "樓市或利率逆轉時，個人資產同步受損。",
                           "分散資產、控制單一物業佔比，或降低槓桿。"),
    "exit_assumption_risk": ("退出假設風險 Exit assumption risk",
                             "退出資本化率低於入市資本化率，隱含需要估值擴張才能達標。",
                             "若退出時 cap rate 回升，賣出價與 IRR 顯著低於預期。",
                             "以較保守（較高）的退出 cap rate 重做情境測試。"),
}

# --------------------------------------------------------------------------- #
#  Transaction boundary targets (spec #17)                                     #
# --------------------------------------------------------------------------- #
BOUNDARY_TARGETS = {
    "target_base_dsr": 0.40,        # comfortable base-case payment ratio
    "max_base_dsr": 0.45,
    "max_stress_dsr": 0.55,         # at rate + 2pp
    "comfortable_ltv_cap": 0.70,    # bank/insurance ceiling we consider
    "target_dscr": 1.25,
    "min_dscr": 1.00,
    "min_net_yield_floor": 0.02,    # absolute floor for "worth it at all"
    "yield_spread_over_rate": 0.01, # require net yield > mortgage rate + 1pp
}

# --------------------------------------------------------------------------- #
#  Verdict thresholds (spec #18)                                               #
# --------------------------------------------------------------------------- #
VERDICT_LABELS = {"buy": "BUY", "negotiate": "NEGOTIATE", "watch": "WATCH", "pass": "PASS"}
# Chinese gloss, always shown next to the English verdict word. "PASS" means
# 「放棄這筆交易」 (decline the deal) — in Chinese, pass 也讀成「通過／合格」,
# which is the opposite polarity, so the bare word misleads readers badly.
VERDICT_LABELS_ZH = {"BUY": "買入", "NEGOTIATE": "議價", "WATCH": "觀察", "PASS": "放棄"}
VERDICT_THRESHOLDS = {
    "pass_if_premium_gt": 0.15,          # >15% above fair value -> PASS
    "pass_if_stress_dsr_gt": 0.60,
    "pass_if_price_over_max_buy": 0.05,  # asking > max buy price by >5% -> PASS
    "buy_if_stress_dsr_le": 0.45,
    "buy_if_premium_le": 0.00,
    "buy_if_dscr_ge": 1.20,
    "negotiate_if_premium_le": 0.08,
}


# --------------------------------------------------------------------------- #
#  Tiny pure helpers                                                           #
# --------------------------------------------------------------------------- #
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def band_label(table_key: str, value: float | None) -> str | None:
    """Map a ratio onto its affordability band label (spec #13)."""
    if value is None:
        return None
    for upper, label in AFFORDABILITY_BANDS.get(table_key, []):
        if value < upper:
            return label
    return None


def valuation_class(premium: float | None) -> str:
    """Classify asking price vs fair value (spec #14)."""
    if premium is None:
        return "insufficient_data"
    b = VALUATION_BANDS
    if premium < b["very_undervalued_lt"]:
        return "very_undervalued"
    if premium < b["undervalued_lt"]:
        return "undervalued"
    if premium >= b["very_expensive_gte"]:
        return "very_expensive"
    if premium >= b["expensive_gte"]:
        return "expensive"
    return "fair"


def regime_from_score(score: float) -> str:
    for upper, name in REGIME_SCORE_BANDS:
        if score <= upper:
            return name
    return "neutral"


def scenario_config(name: str) -> dict:
    return dict(SCENARIOS.get(name, SCENARIOS["base"]))
