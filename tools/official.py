# -*- coding: utf-8 -*-
"""tools/official.py — official Hong Kong data sources (spec #20: Observed facts).

Why this module exists
----------------------
tools/research.py can only return "no connected live source" for market context,
which left the market-regime engine permanently `neutral` / confidence `none`.
Two *official, free, documented* sources are now wired instead of guessed:

    HKMA Residential Mortgage Survey   (api.hkma.gov.hk, documented JSON API)
        -> new-loan pricing trend (HIBOR / BLR referenced), new-loan approvals,
           average LTV, delinquency ratio
    Land Registry (DATA.GOV.HK, hk-lr-data5-landreg)
        -> agreements for sale & purchase received for registration, by
           consideration band, per month  (a transaction-volume proxy)

Design rules
------------
* **Observed, not estimated.** Every entry is tagged ``C.OBSERVED`` with the
  publisher, the URL and the period it covers. Nothing here is a forecast and
  nothing is derived from a news article.
* **Never fabricate.** If a source cannot be fetched, it contributes nothing
  and the report simply has less evidence — the same rule the rest of the app
  follows. A failure is never converted into a market view.
* **Measured direction only.** Trend words are emitted strictly from comparing
  real values in the payload; when the change is inside a noise band the word
  is "持平" (flat) rather than a guess.
* **Cache, never block.** Results are cached under ``.cache/`` for
  ``CACHE_TTL_HOURS``. A network failure falls back to stale cache, and only
  then to nothing. No source is allowed to break an analysis.
* Standard library only, like the rest of the project.

The parsers (``parse_*``) are pure functions over already-decoded JSON, so they
are unit-testable without a network call; the fetchers are thin wrappers.
"""
from __future__ import annotations

import json, os, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C

# --------------------------------------------------------------------------- #
#  Sources                                                                    #
# --------------------------------------------------------------------------- #
HKMA_MORTGAGE_API = ("https://api.hkma.gov.hk/public/market-data-and-statistics/"
                     "monthly-statistical-bulletin/banking/residential-mortgage-survey"
                     "?segment=new&offset=0")
LANDREG_DATASET_API = ("https://data.gov.hk/en-data/api/3/action/package_show"
                       "?id=hk-lr-data5-landreg")
LANDREG_PUBLISHER = "https://www.landreg.gov.hk/en/monthly/monthly.htm"

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}

# A single module switch so tests (and an offline user) can disable all network
# access without monkeypatching each function.
ENABLED = os.environ.get("OFFICIAL_DATA", "1") not in ("0", "", "off", "false")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "official.json")
CACHE_TTL_HOURS = 12.0
TIMEOUT = 20

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
MONTH_NAMES = {v: k for k, v in MONTHS.items()}


# --------------------------------------------------------------------------- #
#  Cache                                                                      #
# --------------------------------------------------------------------------- #
def _read_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(cache: dict) -> None:
    """Atomic-ish write; a broken cache must never be fatal."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _fresh(slot: dict | None) -> bool:
    if not slot:
        return False
    age_h = (time.time() - float(slot.get("fetched_at") or 0)) / 3600.0
    return age_h < CACHE_TTL_HOURS


def _fetch_json(url: str, attempts: int = 2) -> dict | None:
    """GET -> parsed JSON, or None. Retried once (transient DNS/TLS hiccups are
    real: the same URL timed out once and returned in 0.2s the next try)."""
    for i in range(max(1, attempts)):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8-sig", "replace"))
        except Exception:
            if i + 1 < attempts:
                time.sleep(1.0)
    return None


def _cached(key: str, fetch, parse):
    if not ENABLED:
        return [], "disabled"
    cache = _read_cache()
    if _fresh(cache.get(key)):
        return parse(cache[key]["payload"]), "cache"
    payload = fetch()
    if payload is not None:
        try:
            entries = parse(payload)
        except Exception:
            entries = []
        if entries:
            cache[key] = {"fetched_at": time.time(), "payload": payload}
            _write_cache(cache)
            return entries, "live"
    if cache.get(key):
        return parse(cache[key]["payload"]), "stale"
    return [], "unavailable"


# --------------------------------------------------------------------------- #
#  Direction helper (measured, never guessed)                                 #
# --------------------------------------------------------------------------- #
def _pct(x) -> float | None:
    """HKMA returns '0.61' meaning 0.61 percent. Store 0.0061.

    The whole project keeps fractions in decimal form (schema, engine, report
    formatting all assume it). Keeping the raw percent here made the report print
    '息差 61.000%' for a 0.61% margin.
    """
    try:
        return float(x) / 100.0
    except (TypeError, ValueError):
        return None


def _pct_str(x) -> str:
    """0.61 (raw percent from HKMA) -> '0.61%'."""
    try:
        return f"{float(x):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _pct_change(new, old) -> float | None:
    try:
        new, old = float(new), float(old)
        if old == 0:
            return None
        return new / old - 1.0
    except (TypeError, ValueError):
        return None


def _scope(*movement_words) -> list:
    """Declare which market topics this entry may be counted against.

    Derived from tools/config.py REGIME_SIGNAL_RULES (via tools/regime.py) so it
    cannot drift: "下跌" appears in the price, rental AND volume rule lists, and
    without a declared scope a Land Registry volume figure scored all three,
    making the report announce price and rental trends it had no data for.
    """
    from tools import regime as _rg
    return _rg.topics_for_word(*movement_words)


# --------------------------------------------------------------------------- #
#  HKMA — residential mortgage survey                                         #
# --------------------------------------------------------------------------- #
def parse_hkma_mortgage(payload: dict) -> list:
    """New-loan pricing / approvals / LTV / delinquency from the HKMA survey.

    The API returns newest-first. ``ir_new_loans_approved_hibor`` and
    ``..._blr`` are margins over the reference rate (a LOWER number means the
    loan is priced more cheaply), so the trend word is inverted against the
    raw direction on purpose.
    """
    recs = ((payload or {}).get("result") or {}).get("records") or []
    recs = [r for r in recs if r.get("end_of_month")]
    if not recs:
        return []
    latest = recs[0]
    period = str(latest.get("end_of_month"))
    out = []

    def num(rec, key):
        v = rec.get(key)
        return None if v in (None, "", "*") else v

    # ---- new-loan pricing trend (HIBOR-referenced, used when available) --- #
    hibor_now = num(latest, "ir_new_loans_approved_hibor")
    hibor_prev = None
    for r in recs[1:6]:
        hibor_prev = num(r, "ir_new_loans_approved_hibor")
        if hibor_prev is not None:
            break
    if hibor_now is not None:
        word = "持平"
        if hibor_prev is not None and abs(float(hibor_now)) >= 0.0005:
            d = float(hibor_now) - float(hibor_prev)
            # margin over HIBOR: falling margin = cheaper credit = easing
            word = "下調" if d < -0.0005 else ("上調" if d > 0.0005 else "持平")
        out.append({
            "source": "HKMA Residential Mortgage Survey",
            "title": f"新批按揭定價（HIBOR 為基準）：息差 {_pct_str(hibor_now)}，方向{word}（{period}）",
            "date": period,
            "data": {"metric": "mortgage_margin_hibor", "value": _pct(hibor_now),
                     "percent": float(hibor_now), "unit": "margin over HIBOR",
                     "trend": word},
            "relevance": "mortgage-context",
            "confidence": "high",
            "url": HKMA_MORTGAGE_API,
            "publisher": "HKMA",
            "provenance": C.OBSERVED,
            # only this field is keyword-matched by tools/regime.py
            "matched_text": (f"按揭利率方向：{word}。新批按揭息差 {_pct_str(hibor_now)}。"),
            "allowed_topics": _scope(word),
        })

    # ---- BLR-referenced pricing (the other half of the market) ----------- #
    blr_now = num(latest, "ir_new_loans_approved_blr")
    if blr_now is not None:
        out.append({
            "source": "HKMA Residential Mortgage Survey",
            "title": f"新批按揭定價（最優惠利率為基準）：息差 {_pct_str(blr_now)}（{period}）",
            "date": period,
            "data": {"metric": "mortgage_margin_blr", "value": _pct(blr_now),
                     "percent": float(blr_now), "unit": "margin over BLR"},
            "relevance": "mortgage-context",
            "confidence": "high",
            "url": HKMA_MORTGAGE_API,
            "publisher": "HKMA",
            "provenance": C.OBSERVED,
            # a pricing level, not a movement: the mortgage topic only
            "allowed_topics": ["mortgage"],
        })

    # ---- new-loan approvals (momentum) ---------------------------------- #
    amt = num(latest, "new_loans_approved_amt")
    mom = num(latest, "new_loans_approved_amt_mom")
    if amt is not None and mom is not None:
        m = float(mom)
        # "減少" is volume-specific; "下跌" also sits in the price and rental
        # rule lists, which is how an approval figure once produced price and
        # rental signals. See regime._declared_topics.
        word = "增加" if m > 0.02 else ("減少" if m < -0.02 else "持平")
        out.append({
            "source": "HKMA Residential Mortgage Survey",
            "title": f"新批按揭金額 HK${float(amt) / 1000:,.2f}bn，按月{word} {abs(m):.1%}（{period}）",
            "date": period,
            "data": {"metric": "new_loans_approved_amt", "value": amt,
                     "unit": "HK$ Mn", "mom": m, "trend": word},
            "relevance": "mortgage-context",
            "confidence": "high",
            "url": HKMA_MORTGAGE_API,
            "publisher": "HKMA",
            "provenance": C.OBSERVED,
            "allowed_topics": _scope(word),
        })

    # ---- average LTV + delinquency (credit conditions) ------------------ #
    ltv = num(latest, "new_loans_approved_lv_ratio")
    if ltv is not None:
        out.append({
            "source": "HKMA Residential Mortgage Survey",
            "title": f"新批按揭平均按揭成數 {_pct_str(ltv)}（{period}）",
            "date": period,
            "data": {"metric": "ltv", "value": _pct(ltv), "percent": float(ltv),
                     "unit": "ratio"},
            "relevance": "mortgage-context",
            "confidence": "high",
            "url": HKMA_MORTGAGE_API,
            "publisher": "HKMA",
            "provenance": C.OBSERVED,
            # credit conditions -> the mortgage topic only (no movement word, so
            # the scope must be declared explicitly)
            "allowed_topics": ["mortgage"],
        })
    dq = num(latest, "delinquency_ratio")
    if dq is not None:
        out.append({
            "source": "HKMA Residential Mortgage Survey",
            "title": f"按揭拖欠比率 {_pct_str(dq)}（{period}）",
            "date": period,
            "data": {"metric": "delinquency_ratio", "value": _pct(dq),
                     "percent": float(dq), "unit": "ratio"},
            "relevance": "mortgage-context",
            "confidence": "high",
            "url": HKMA_MORTGAGE_API,
            "publisher": "HKMA",
            "provenance": C.OBSERVED,
            "allowed_topics": ["mortgage"],
        })
    return out


# --------------------------------------------------------------------------- #
#  Land Registry — agreements for sale & purchase, by consideration band      #
# --------------------------------------------------------------------------- #
def _landreg_pick_year(payload: dict, want_year: int) -> tuple[str | None, dict | None]:
    """Newest published JSON resource for ``want_year`` (falls back to newest)."""
    resources = ((payload or {}).get("result") or {}).get("resources") or []
    cands = [(r.get("name") or "", r.get("url")) for r in resources
             if str(r.get("format", "")).upper() == "JSON" and r.get("url")]
    if not cands:
        return None, None

    def year_of(name):
        digits = "".join(ch for ch in name if ch.isdigit())
        return int(digits) if len(digits) == 4 else -1

    exact = [(y, n, u) for n, u in cands if (y := year_of(n)) == want_year]
    pool = exact or sorted(((year_of(n), n, u) for n, u in cands), reverse=True)
    if not pool:
        return None, None
    _, name, url = pool[0]
    return name, {"url": url, "name": name}


def _months_of(year_payload) -> tuple[int, float] | None:
    """(latest_month, total_count) from one year's consideration JSON."""
    rows = year_payload if isinstance(year_payload, list) else []
    total = None
    for row in rows:
        if str(row.get("Range of Consideration ($ million)", "")).strip().lower() == "total":
            total = row
            break
    if total is None:
        return None
    latest_m, latest_v = None, None
    for mname, mnum in MONTHS.items():
        raw = str(total.get(mname, "") or "").replace(",", "").strip()
        if raw == "":
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        if latest_m is None or mnum > latest_m:
            latest_m, latest_v = mnum, v
    if latest_m is None:
        return None
    return latest_m, latest_v


def parse_landreg(payload: dict, year_payload=None, year_name: str = "") -> list:
    """Transaction-volume proxy from the Land Registry monthly counts.

    ``payload`` is the DATA.GOV.HK package JSON (resource list); ``year_payload``
    is the decoded consideration_<year>.json. Both are injected so the parser
    stays pure and testable.
    """
    if not isinstance(year_payload, list):
        return []
    latest = _months_of(year_payload)
    if latest is None:
        return []
    month, count = latest
    year_label = "".join(ch for ch in (year_name or "") if ch.isdigit())
    period = f"{year_label}-{month:02d}" if len(year_label) == 4 else None
    out = []

    # the full published year, so the trend is visible and auditable
    series = {}
    for row in year_payload:
        if str(row.get("Range of Consideration ($ million)", "")).strip().lower() != "total":
            continue
        for mname, mnum in MONTHS.items():
            raw = str(row.get(mname, "") or "").replace(",", "").strip()
            if raw:
                try:
                    series[mnum] = float(raw)
                except ValueError:
                    pass
    months_sorted = sorted(series)
    word = "持平"
    change = None
    if len(months_sorted) >= 3:
        recent = series[months_sorted[-1]]
        prior = sum(series[m] for m in months_sorted[-4:-1]) / 3.0
        change = _pct_change(recent, prior)
        if change is not None:
            # Volume-specific vocabulary: "上升/下跌" also sit in the price and
            # rental rule lists, so using them here scored all three topics.
            word = "減少" if change < -0.10 else ("增加" if change > 0.10 else "持平")

    out.append({
        "source": "Land Registry (DATA.GOV.HK)",
        "title": (f"住宅買賣合約送交註冊宗數 {count:,.0f} 宗（{period or '最新月份'}），"
                  f"相對前三個月平均{word}"
                  + (f" {abs(change):.1%}" if change is not None else "")),
        "date": period,
        "data": {"metric": "registered_transactions", "value": count,
                 "unit": "agreements", "trend": word,
                 "monthly_series": series,
                 "note": "送交土地註冊處註冊的住宅買賣合約數目（按金額分類之總數）"},
        "relevance": "transaction_volume",
        "confidence": "high",
        "url": LANDREG_PUBLISHER,
        "publisher": "Land Registry",
        "provenance": C.OBSERVED,
        "allowed_topics": _scope(word),
    })

    # a second, coarse view: how much of the market sits in the lower bands,
    # i.e. whether activity is concentrated in smaller tickets
    month_key = MONTH_NAMES.get(month)
    small = 0.0
    total_now = 0.0
    for row in year_payload:
        label = str(row.get("Range of Consideration ($ million)", "")).strip()
        raw = str(row.get(month_key, "") or "").replace(",", "").strip()
        if not raw:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        if label.lower() == "total":
            total_now = v
        elif label in ("Less than 1", "1 to less than 2", "2 to less than 3"):
            small += v
    if total_now > 0:
        out.append({
            "source": "Land Registry (DATA.GOV.HK)",
            "title": (f"其中 HK$300 萬以下細價樓佔 {small / total_now:.1%}"
                      f"（{count:,.0f} 宗中的 {small:,.0f} 宗）"),
            "date": period,
            "data": {"metric": "small_ticket_share", "value": small / total_now,
                     "unit": "share", "note": "交易金額結構，反映上車盤主導程度"},
            "relevance": "transaction_volume",
            "confidence": "high",
            "url": LANDREG_PUBLISHER,
            "publisher": "Land Registry",
            "provenance": C.OBSERVED,
            # a composition fact, not a movement -> volume topic only
            "allowed_topics": ["transaction_volume"],
        })
    return out


# --------------------------------------------------------------------------- #
#  Public entry point                                                         #
# --------------------------------------------------------------------------- #
def _fetch_hkma():
    return _fetch_json(HKMA_MORTGAGE_API)


def _fetch_landreg():
    """One extra request: resolve the newest year's JSON resource, then read it."""
    pkg = _fetch_json(LANDREG_DATASET_API)
    if not pkg:
        return None
    year = time.localtime().tm_year
    name, chosen = _landreg_pick_year(pkg, year)
    if not chosen:
        return None
    year_payload = _fetch_json(chosen["url"])
    if year_payload is None:
        return None
    return {"package": pkg, "year": year_payload, "name": name}


def _parse_landreg_bundle(bundle):
    return parse_landreg(bundle.get("package") or {}, bundle.get("year"),
                         bundle.get("name") or "")


def mortgage_context() -> tuple[list, str]:
    """(entries, status). Never raises; never blocks an analysis."""
    try:
        return _cached("hkma_mortgage", _fetch_hkma, parse_hkma_mortgage)
    except Exception:
        return [], "unavailable"


def transaction_context() -> tuple[list, str]:
    try:
        return _cached("landreg", _fetch_landreg, _parse_landreg_bundle)
    except Exception:
        return [], "unavailable"


def fetch_all() -> tuple[list, dict]:
    """All official entries (newest-first per source) plus a status map."""
    mortgage, s1 = mortgage_context()
    transactions, s2 = transaction_context()
    return list(mortgage) + list(transactions), {
        "hkma_mortgage": s1, "landreg": s2,
        "cache_ttl_hours": CACHE_TTL_HOURS,
        "enabled": ENABLED,
    }


def _clear_cache() -> None:
    """Test/debug helper: forget every cached payload."""
    try:
        os.remove(CACHE_FILE)
    except Exception:
        pass
