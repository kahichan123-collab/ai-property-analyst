# -*- coding: utf-8 -*-
"""tools/schema.py — request normalisation + backend validation (spec #3, #5).

Design rules
------------
1. **Percentages are decimal fractions on the wire.** The UI collects "60",
   "4.125", "5" and sends ``percent / 100`` → ``0.60``, ``0.04125``, ``0.05``.
   The backend therefore validates every fraction field against 0–1 and
   *rejects* a raw percent (e.g. ``ltv=60``) instead of guessing which of
   "1 or 0.01" the caller meant. The error message says exactly that.
2. **Rental assumptions are only accepted for rental purpose.** When
   ``purpose=owner_occupier`` any supplied rental block is dropped (and
   reported), so UI defaults can never pollute owner-occupier logic — and the
   reverse: a rental request without rent is a validation error.
3. **Flat/legacy payloads are shimmed, not silently accepted forever.** The old
   flat body shape is mapped into the nested schema and flagged as legacy.
4. Numeric values must be real numbers. Numeric strings (including "8,300,000")
   are coerced as a defensive fallback and flagged with a warning, so a comma
   string can never reach the financial engine as a string.
"""
from __future__ import annotations

import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C


# --------------------------------------------------------------------------- #
#  Field specifications                                                       #
# --------------------------------------------------------------------------- #
#  kind: money | fraction | int | years | text | enum
#  fraction fields are validated 0–1 (or their own tighter range) and reject
#  raw percentages with an explicit hint.
F = lambda label, kind, lo=None, hi=None, default=None, required=False: {
    "label": label, "kind": kind, "min": lo, "max": hi,
    "default": default, "required": required,
}

FIELD_SPECS: dict[str, dict] = {
    # ---- property -------------------------------------------------------- #
    "property.url": F("樓盤網址 Property URL", "text", default=""),
    "property.estate": F("屋苑 Estate", "text"),
    "property.district": F("地區 District", "text"),
    "property.building": F("大廈 Building", "text"),
    "property.layout": F("間隔 Layout", "text"),
    "property.floor": F("樓層 Floor", "text"),
    "property.orientation": F("座向 Orientation", "text"),
    "property.view": F("景觀 View", "text"),
    "property.special_features": F("特殊條件 Special features", "text"),
    "property.building_age": F("樓齡 Building age (年)", "years", 0, 120),
    "property.asking_price": F("叫價 Asking Price (HK$)", "money", 1, None),
    "property.usable_area_sqft": F("實用面積 Saleable area (呎)", "money", 1, None),
    "property.gross_area_sqft": F("建築面積 Gross area (呎)", "money", 1, None),
    "property.monthly_mgmt_rates": F("每月管理費 Management fee (HK$)", "money", 0, None),
    "property.rates_amount": F("每月差餉地租 Rates (HK$)", "money", 0, None),
    "property.listing_date": F("放盤日期 Listing date", "text"),
    # ---- personal finance ------------------------------------------------ #
    "finance.gross_monthly_income": F("每月總收入 Gross monthly income (HK$)", "money", 0, None),
    "finance.monthly_net_income": F("每月淨收入 Net monthly income (HK$)", "money", 0, None),
    "finance.other_recurring_income": F("其他固定收入 Other recurring income (HK$)", "money", 0, None, 0),
    "finance.existing_monthly_debt": F("現有每月債務 Existing monthly debt (HK$)", "money", 0, None, 0),
    "finance.liquid_cash": F("可動用現金 Liquid cash (HK$)", "money", 0, None),
    "finance.available_cash_for_transaction": F("可用於交易的現金 Available cash (HK$)", "money", 0, None),
    "finance.emergency_reserve": F("應急儲備 Emergency reserve (HK$)", "money", 0, None),
    "finance.max_comfortable_monthly_payment": F("最高舒適月供 Max comfortable payment (HK$)", "money", 0, None),
    # ---- financing ------------------------------------------------------- #
    # Defaults reference tools/config.py: normalize_request() fills these in
    # BEFORE the engine sees the payload, so a literal here would silently
    # shadow the config fallback and become the real policy number.
    "financing.ltv": F("貸款成數 LTV", "fraction", 0.0, 1.0, C.DEFAULT_OWNER_LTV),
    "financing.mortgage_rate": F("按揭年利率 Mortgage rate", "fraction", 0.0001, 0.30,
                                 C.DEFAULT_MORTGAGE_RATE),
    "financing.tenure_years": F("還款年期 Tenure (年)", "int", 1, 40, C.DEFAULT_TENURE_YEARS),
    "financing.amortization": F("還款方式 Amortization", "enum", None, None,
                                C.DEFAULT_AMORTIZATION),
    "financing.fee_rate": F("交易費用率 Transaction cost rate", "fraction", 0.0, 0.10,
                            C.DEFAULT_FEE_RATE),
    "financing.discount_rate": F("折現率 Discount rate", "fraction", 0.0, 0.50,
                                 C.DEFAULT_DISCOUNT_RATE),
    # ---- rental assumptions (rental purpose only) ------------------------ #
    "rental.monthly_rent": F("每月租金 Monthly rent (HK$)", "money", 0, None),
    "rental.vacancy": F("空置率 Vacancy", "fraction", 0.0, 1.0, 0.05),
    "rental.rates_pct": F("差餉地租比率 Rates % of PGI", "fraction", 0.0, 1.0, 0.06),
    "rental.opex_pct": F("管理維修比率 Opex % of EGI", "fraction", 0.0, 1.0, 0.08),
    "rental.annual_capex": F("每年維修預備 Annual capex (HK$)", "money", 0, None, 10000),
    "rental.rent_growth": F("租金年增長 Rent growth", "fraction", -0.30, 0.30, 0.03),
    "rental.hold_years": F("持有年期 Hold years", "int", 1, 50, 5),
    "rental.exit_cap": F("退出資本化率 Exit cap rate", "fraction", 0.0001, 0.25, 0.035),
    "rental.sell_cost": F("賣出成本 Selling cost", "fraction", 0.0, 1.0, 0.01),
    # ---- acquisition line items ----------------------------------------- #
    "acquisition.agency_fee_pct": F("代理佣金率 Agency fee %", "fraction", 0.0, 0.10),
    "acquisition.legal_fee_pct": F("律師費率 Legal fee %", "fraction", 0.0, 0.10),
    "acquisition.mortgage_fees": F("按揭相關費用 Mortgage fees (HK$)", "money", 0, None, 0),
    "acquisition.valuation_fee": F("估價費 Valuation fee (HK$)", "money", 0, None, 0),
    "acquisition.renovation_furnishing": F("裝修傢俬 Renovation (HK$)", "money", 0, None, 0),
    "acquisition.initial_reserve": F("初始儲備 Initial reserve (HK$)", "money", 0, None, 0),
    # ---- overrides ------------------------------------------------------- #
    "overrides.price": F("價格覆寫 Price override (HK$)", "money", 1, None),
    "overrides.area": F("面積覆寫 Area override (呎)", "money", 1, None),
}

SECTION_OF = {k: k.split(".")[0] for k in FIELD_SPECS}

PERCENT_HINT = ("此欄位只接受 {lo}–{hi} 之間的分數（前端須傳 percent/100）。"
                "例如 60%（輸入 60）必須傳 0.60；收到 {raw:g}。")

ALLOWED_AMORT = {"PI", "IO"}
PURPOSE_ALIASES = {
    "owner_occupier": "owner_occupier", "owner": "owner_occupier",
    "self_use": "owner_occupier", "自住": "owner_occupier",
    "rental": "rental", "investment": "rental", "buy_to_let": "rental",
    "btl": "rental", "收租": "rental",
}

RENTAL_KEYS = [k.split(".")[1] for k in FIELD_SPECS if k.startswith("rental.")]

# A fair value needs more than one data point: a single trade can be distressed,
# a different layout, or a different floor, and presenting it as a benchmark
# would be a fabricated certainty. Below this count the valuation stays
# Insufficient Data — but the entries are never discarded, they are reported.
MIN_COMPARABLES = 2

# pre-2.0 aliases accepted inside the property block, mapped explicitly later
LEGACY_SECTION_ALIASES = {"property.price", "property.area"}


# --------------------------------------------------------------------------- #
#  Coercion helpers                                                           #
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_number(value):
    """Return (float|None, note).

    Accepts real numbers and numeric strings — including thousands separators,
    currency symbols and a trailing '%' (which is DROPPED with a note, never
    silently interpreted as a fraction: "5%" is ambiguous input the UI must
    resolve to 0.05 before sending).
    """
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "boolean_rejected"
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip()
    if not s:
        return None, None
    had_symbol = bool(re.search(r"[,$HK元萬\s%]", s))
    if "%" in s:
        return None, "percent_sign_rejected"
    s2 = re.sub(r"(HK\$|HKD|港?元|,|\s)", "", s, flags=re.I)
    m = _NUM_RE.search(s2)
    if not m:
        return None, "not_numeric"
    try:
        n = float(m.group(0))
    except ValueError:
        return None, "not_numeric"
    return n, "coerced_from_string"


def _clean_section(raw_section, prefix, errors, warnings):
    out = {}
    if not isinstance(raw_section, dict):
        if raw_section not in (None, ""):
            errors.append({"field": prefix, "code": "type",
                           "message": f"{prefix} 必須是物件（object）。"})
        return out
    for key, value in raw_section.items():
        path = f"{prefix}.{key}"
        if path in LEGACY_SECTION_ALIASES:
            continue          # handled explicitly in normalize_request()
        spec = FIELD_SPECS.get(path)
        if spec is None:
            warnings.append({"field": path, "code": "ignored_unknown_field",
                             "message": f"已忽略未知欄位 {path}。"})
            continue
        if value in (None, ""):
            continue
        kind = spec["kind"]
        if kind in ("text",):
            out[key] = str(value).strip()
            continue
        if kind == "enum":
            v = str(value).strip()
            if path == "financing.amortization":
                v = v.upper()
                if v not in ALLOWED_AMORT:
                    errors.append({"field": path, "code": "enum",
                                   "message": "還款方式必須為 PI 或 IO。"})
                    continue
            out[key] = v
            continue
        num, note = parse_number(value)
        if num is None:
            msg = (f"{spec['label']} 必須是數字。" if note != "percent_sign_rejected"
                   else f"{spec['label']} 不可包含 '%'，請以 percent/100 傳送（例如 5% → 0.05）。")
            errors.append({"field": path, "code": "not_numeric", "message": msg})
            continue
        if note:
            warnings.append({"field": path, "code": note,
                             "message": f"{spec['label']} 原本是字串 \"{value}\"，已轉為數字 {num}。"})
        if kind in ("int", "years"):
            num = int(round(num))
        lo, hi = spec["min"], spec["max"]
        if lo is not None and num < lo:
            errors.append({"field": path, "code": "range",
                           "message": _range_msg(spec, lo, hi, num)})
            continue
        if hi is not None and num > hi:
            hint = None
            if kind == "fraction" and abs(num) > 1.0:
                hint = PERCENT_HINT.format(raw=num, lo=_as_pct(lo), hi=_as_pct(hi))
            errors.append({"field": path, "code": "range",
                           "message": _range_msg(spec, lo, hi, num), "hint": hint})
            continue
        out[key] = num
    return out


def _as_pct(x):
    if x is None:
        return "—"
    return f"{x * 100:g}%"


def _range_msg(spec, lo, hi, num):
    label = spec["label"]
    if spec["kind"] == "fraction":
        if hi == 1.0:
            return f"{label} 必須介乎 {_as_pct(lo)}–{_as_pct(hi)}（收到 {num}）。"
        return f"{label} 必須介乎 {_as_pct(lo)}–{_as_pct(hi)}（收到 {num}）。"
    if lo is not None and hi is not None:
        return f"{label} 必須介乎 {lo:g}–{hi:g}（收到 {num:g}）。"
    if lo is not None:
        return f"{label} 必須大於或等於 {lo:g}（收到 {num:g}）。"
    return f"{label} 不可大於 {hi:g}（收到 {num:g}）。"


# --------------------------------------------------------------------------- #
#  Legacy flat payload shim                                                   #
# --------------------------------------------------------------------------- #
LEGACY_FLAT = {"url", "income", "debt", "ltv", "rate", "tenure", "price", "area",
               "monthly_mgmt_rates", "monthly_rent", "vacancy", "rates_pct",
               "opex_pct", "annual_capex", "rent_growth", "hold_years", "exit_cap",
               "sell_cost", "estate", "layout", "cost_rate"}


def _from_legacy(raw: dict) -> dict:
    """Map the pre-2.0 flat body into the nested schema (flagged as legacy)."""
    return {
        "mode": raw.get("mode", "auto"),
        "purpose": raw.get("purpose", "owner_occupier"),
        "property": {
            "url": raw.get("url", ""),
            "estate": raw.get("estate"),
            "layout": raw.get("layout"),
            "asking_price": raw.get("price"),
            "usable_area_sqft": raw.get("area"),
            "monthly_mgmt_rates": raw.get("monthly_mgmt_rates"),
        },
        "finance": {"monthly_net_income": raw.get("income"),
                    "existing_monthly_debt": raw.get("debt")},
        "financing": {"ltv": raw.get("ltv"), "mortgage_rate": raw.get("rate"),
                      "tenure_years": raw.get("tenure")},
        "rental": {k: raw.get(k) for k in RENTAL_KEYS if raw.get(k) not in (None, "")},
        "acquisition": raw.get("acquisition") or {},
        "overrides": {"price": raw.get("price_override"), "area": raw.get("area_override")},
    }


# --------------------------------------------------------------------------- #
#  Normalisation + validation                                                 #
# --------------------------------------------------------------------------- #
def _clean_comparables(raw_items, errors, warnings, cap: int = 12) -> list:
    """User-supplied comparable evidence -> the internal entry shape.

    A single trade tells you nothing: one comp can be a distressed sale, a
    different layout or a different floor. At least ``MIN_COMPARABLES`` are
    required before any benchmark is produced, and every entry is tagged
    ``user-input`` so the report never presents it as an observed market feed.

    Returns [] (silently) when the caller supplied nothing at all, so adding the
    field stays strictly optional and no existing request changes behaviour.
    """
    if raw_items in (None, "", []):
        return []
    if not isinstance(raw_items, list):
        errors.append({"field": "comparables", "code": "type",
                       "message": "comparables 必須是陣列（每筆為一個物件）。"})
        return []
    clean: list = []
    for i, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            warnings.append({"field": f"comparables[{i}]", "code": "type",
                             "message": f"第 {i} 筆可比成交不是物件，已忽略。"})
            continue
        price, _ = parse_number(item.get("price"))
        area, _ = parse_number(item.get("usable_area_sqft", item.get("area")))
        if not price or not area or price <= 0 or area <= 0:
            warnings.append({"field": f"comparables[{i}]", "code": "incomplete",
                             "message": f"第 {i} 筆可比成交缺價格或面積，已忽略"
                                        f"（兩者皆為必要，否則無法計算呎價）。"})
            continue
        estate = str(item.get("estate") or "").strip()
        layout = str(item.get("layout") or "").strip()
        title = str(item.get("title") or "").strip()
        if not title:
            title = " ".join(x for x in (estate, layout) if x) or f"可比成交 {i}"
        clean.append({
            "source": "user-input",
            "title": title,
            "estate": estate or None,
            "layout": layout or None,
            "date": str(item.get("date") or "").strip() or None,
            "price": price,
            "usable_area_sqft": area,
            "confidence": "low",
        })
    if len(clean) > cap:
        warnings.append({"field": "comparables", "code": "capped",
                         "message": f"可比成交只採用首 {cap} 筆（收到 {len(clean)} 筆）。"})
        clean = clean[:cap]
    if clean and len(clean) < MIN_COMPARABLES:
        warnings.append({
            "field": "comparables", "code": "insufficient_comparables",
            "message": (f"只有 {len(clean)} 筆可比成交；少於 {MIN_COMPARABLES} 筆不足以"
                        f"推算公允價值（單一成交可能是特殊成交、不同間隔或樓層），"
                        f"估值將維持 Insufficient Data。")})
    return clean


def normalize_request(raw: dict) -> dict:
    """raw request body -> {"ok", "payload", "errors", "warnings"}."""
    errors: list[dict] = []
    warnings: list[dict] = []
    if not isinstance(raw, dict):
        return {"ok": False, "payload": None,
                "errors": [{"field": "body", "code": "type",
                            "message": "請求內容必須是 JSON 物件。"}],
                "warnings": []}

    if "property" not in raw and "finance" not in raw and "financing" not in raw:
        if LEGACY_FLAT & set(raw.keys()):
            raw = _from_legacy(raw)
            warnings.append({"field": "body", "code": "legacy_flat_payload",
                             "message": "收到舊版扁平 payload，已自動轉為 2.0 巢狀 schema。"})
        else:
            return {"ok": False, "payload": None,
                    "errors": [{"field": "body", "code": "schema",
                                "message": "缺少 property / finance / financing 區塊。"}],
                    "warnings": warnings}

    mode = str(raw.get("mode", "auto")).strip().lower()
    if mode not in ("auto", "manual"):
        errors.append({"field": "mode", "code": "enum",
                       "message": "mode 必須為 auto 或 manual。"})
        mode = "auto"

    purpose_raw = str(raw.get("purpose", "owner_occupier")).strip().lower()
    purpose = PURPOSE_ALIASES.get(purpose_raw)
    if purpose is None:
        errors.append({"field": "purpose", "code": "enum",
                       "message": "purpose 必須為 owner_occupier 或 rental。"})
        purpose = "owner_occupier"

    payload = {"mode": mode, "purpose": purpose,
               "property": _clean_section(raw.get("property"), "property", errors, warnings),
               "finance": _clean_section(raw.get("finance"), "finance", errors, warnings),
               "financing": _clean_section(raw.get("financing"), "financing", errors, warnings),
               "rental": {}, "acquisition": {}, "comparables": [],
               "overrides": _clean_section(raw.get("overrides"), "overrides", errors, warnings),
               "meta": {}}

    raw_rental = raw.get("rental") or {}
    if purpose == "rental":
        payload["rental"] = _clean_section(raw_rental, "rental", errors, warnings)
        if not payload["rental"].get("monthly_rent"):
            errors.append({"field": "rental.monthly_rent", "code": "required",
                           "message": "收租模式必須填寫每月租金（Monthly rent）。"})
    else:
        dropped = sorted(k for k, v in (raw_rental or {}).items() if v not in (None, ""))
        if dropped:
            warnings.append({"field": "rental", "code": "ignored_not_applicable",
                             "message": f"自住模式已忽略不適用的收租假設：{', '.join(dropped)}。"})
        payload["meta"]["ignored_rental_fields"] = dropped

    payload["acquisition"] = _clean_section(raw.get("acquisition"), "acquisition", errors, warnings)
    # Optional user-supplied comparable evidence (the only way to get market
    # evidence without a paid feed). Absent -> [] and nothing changes.
    payload["comparables"] = _clean_comparables(raw.get("comparables"), errors, warnings)

    # legacy aliases: property.price / property.area
    prop_raw = raw.get("property") or {}
    if isinstance(prop_raw, dict):
        if "asking_price" not in payload["property"] and prop_raw.get("price") not in (None, ""):
            num, _ = parse_number(prop_raw.get("price"))
            if num is not None:
                payload["property"]["asking_price"] = num
        if "usable_area_sqft" not in payload["property"] and prop_raw.get("area") not in (None, ""):
            num, _ = parse_number(prop_raw.get("area"))
            if num is not None:
                payload["property"]["usable_area_sqft"] = num

    # defaults for financing
    for key in ("ltv", "mortgage_rate", "tenure_years", "fee_rate", "discount_rate"):
        if key not in payload["financing"]:
            payload["financing"][key] = FIELD_SPECS[f"financing.{key}"]["default"]
    payload["financing"].setdefault("amortization", C.DEFAULT_AMORTIZATION)
    for key in ("other_recurring_income", "existing_monthly_debt"):
        payload["finance"].setdefault(key, FIELD_SPECS[f"finance.{key}"]["default"])
    if purpose == "rental":
        for key in RENTAL_KEYS:
            payload["rental"].setdefault(key, FIELD_SPECS[f"rental.{key}"]["default"])

    return {"ok": not errors, "payload": payload, "errors": errors, "warnings": warnings}


# --------------------------------------------------------------------------- #
#  Post-extraction validation                                                 #
# --------------------------------------------------------------------------- #
def resolve_inputs(payload: dict, ext: dict | None) -> dict:
    """Merge extracted listing data with user input; user input wins.

    NOTE the key naming: the extractor calls the listing price `price`, while the
    request schema calls it `asking_price`. Looking up `ext["asking_price"]` finds
    nothing, so a perfectly extracted price (and area) was silently thrown away
    and the user was told their URL "had no price". Map it explicitly.
    """
    prop = dict(payload.get("property") or {})
    ext = ext or {}
    resolved = {}
    for key in ("estate", "district", "layout", "floor", "orientation",
                "view", "special_features", "building_age", "gross_area_sqft",
                "listing_date"):
        resolved[key] = prop.get(key) or ext.get(key)
    # extractor says "block", the schema calls the same thing "building"
    resolved["building"] = prop.get("building") or ext.get("building") or ext.get("block")
    ext_price = ext.get("asking_price")
    if ext_price is None:
        ext_price = ext.get("price")
    for key in ("usable_area_sqft", "monthly_mgmt_rates", "rates_amount"):
        value = prop.get(key)
        if value is None:
            value = ext.get(key)
        resolved[key] = value
    resolved["asking_price"] = prop.get("asking_price")
    if resolved["asking_price"] is None:
        resolved["asking_price"] = ext_price
    ov = payload.get("overrides") or {}
    price_source = "user_input"
    if ov.get("price") is not None:
        resolved["asking_price"] = ov["price"]
        price_source = "override"
    elif resolved.get("asking_price") is not None and prop.get("asking_price") is not None:
        price_source = "user_input"
    elif resolved.get("asking_price") is not None:
        price_source = "extracted"
    if ov.get("area") is not None:
        resolved["usable_area_sqft"] = ov["area"]
    resolved["_price_source"] = price_source
    return resolved


def validate_resolved(payload: dict, resolved: dict, mode: str,
                      extraction_ok: bool = False,
                      extraction_error: str | None = None) -> list[dict]:
    """Errors that can only be judged once extraction has run.

    `extraction_ok` means "a price was actually extracted" (not merely "the page
    was fetched"). In auto mode a missing price is always an extraction outcome,
    never a blank field the user forgot to fill, so the remedy names the real
    cause: an unreachable page vs a page that genuinely carries no price.
    """
    errors: list[dict] = []
    price = resolved.get("asking_price")
    area = resolved.get("usable_area_sqft")
    if price is None:
        if mode == "auto":
            if extraction_error:
                errors.append({"field": "property.asking_price", "code": "unavailable",
                               "message": "無法開啟該樓盤網址（網站阻擋自動讀取或網絡問題），"
                                          "系統不會猜測價格。請確認網址在瀏覽器可直接開啟，"
                                          "或改用「價格覆寫」／「手動輸入物業資料」。",
                               "hint": "價格覆寫只需填叫價（HK$）；面積可另填「面積覆寫」。",
                               "severity": "error"})
            else:
                # the page WAS read, it simply does not carry a price we can trust
                errors.append({"field": "property.asking_price", "code": "unavailable",
                               "message": "已讀取樓盤頁面，但頁面沒有可辨識的叫價"
                                          "（部分樓盤不公開價格，或頁面由 JavaScript 產生），"
                                          "系統不會猜測價格。請在「價格覆寫 Price override」"
                                          "填寫叫價，或改用「手動輸入物業資料」。",
                               "hint": "價格覆寫只需填叫價（HK$）；面積可另填「面積覆寫」。",
                               "severity": "error"})
        else:
            errors.append({"field": "property.asking_price", "code": "required",
                           "message": "必須提供叫價 Asking Price（> 0）。",
                           "severity": "error"})
    elif price <= 0:
        errors.append({"field": "property.asking_price", "code": "range",
                       "message": "叫價必須大於 0。", "severity": "error"})
    if area is not None and area <= 0:
        errors.append({"field": "property.usable_area_sqft", "code": "range",
                       "message": "實用面積必須大於 0。", "severity": "error"})
    if payload.get("purpose") == "rental":
        rent = (payload.get("rental") or {}).get("monthly_rent") \
            or (resolved.get("monthly_rent"))
        if not rent or rent <= 0:
            errors.append({"field": "rental.monthly_rent", "code": "required",
                           "message": "收租模式必須提供每月租金（> 0）。",
                           "severity": "error"})
    return errors


def cross_field_checks(payload: dict, resolved: dict) -> tuple[list[dict], list[dict]]:
    """Business-logic relations BETWEEN fields -> (errors, warnings).

    FIELD_SPECS only range-checks one value at a time. These rules need two or
    more values visible together, and they are enforced here rather than only in
    the browser because the backend never trusts the frontend.

    Impossible combinations are errors. Merely surprising-but-possible ones are
    warnings, so a legitimate edge case is flagged without being blocked.
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    prop = payload.get("property") or {}
    pf = payload.get("finance") or {}

    usable = resolved.get("usable_area_sqft")
    gross = resolved.get("gross_area_sqft")
    if gross is None:
        gross = prop.get("gross_area_sqft")
    if usable and gross and usable > gross:
        errors.append({"field": "property.gross_area_sqft", "code": "cross_field",
                       "message": f"建築面積（{gross:,.0f} 呎）不可小於實用面積（{usable:,.0f} 呎）。",
                       "hint": "實用面積通常小於建築面積；請檢查兩個數字是否填反了。",
                       "severity": "error"})

    net = pf.get("monthly_net_income")
    gross_income = pf.get("gross_monthly_income")
    if net is not None and gross_income is not None and net > gross_income:
        warnings.append({"field": "finance.monthly_net_income", "code": "cross_field",
                         "message": f"每月淨收入（HK${net:,.0f}）高於每月總收入（HK${gross_income:,.0f}）。",
                         "severity": "warning"})

    comfort = pf.get("max_comfortable_monthly_payment")
    if net is not None and comfort is not None and comfort > net:
        warnings.append({"field": "finance.max_comfortable_monthly_payment", "code": "cross_field",
                         "message": f"最高舒適月供（HK${comfort:,.0f}）高於每月淨收入（HK${net:,.0f}）。",
                         "hint": "月供已用盡全部收入，生活開支無處可出。",
                         "severity": "warning"})

    available = pf.get("available_cash_for_transaction")
    liquid = pf.get("liquid_cash")
    if available is not None and liquid is not None and available > liquid:
        warnings.append({"field": "finance.available_cash_for_transaction", "code": "cross_field",
                         "message": f"可用於交易的現金（HK${available:,.0f}）高於可動用現金（HK${liquid:,.0f}）。",
                         "severity": "warning"})

    return errors, warnings


def field_specs_public() -> dict:
    """Schema description exposed to the frontend (GET /api/schema)."""
    return {"schema_version": C.SCHEMA_VERSION, "engine_version": C.ENGINE_VERSION,
            "fields": FIELD_SPECS, "rental_only_fields": [f"rental.{k}" for k in RENTAL_KEYS],
            "percent_fields": [k for k, v in FIELD_SPECS.items() if v["kind"] == "fraction"],
            "scenarios": {k: {"label": v["label"], "label_zh": v["label_zh"],
                              "description": v["description"]}
                          for k, v in C.SCENARIOS.items()},
            "rate_stress_steps": C.RATE_STRESS_STEPS,
            "provenance_labels": C.PROVENANCE_ORDER}
