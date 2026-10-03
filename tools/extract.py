# -*- coding: utf-8 -*-
"""tools/extract.py — Property Extraction Tool (PHASE 4)

URL -> strict property JSON. Rule-based parser first; optional LLM second pass.
Rules: never guess. Missing -> None + recorded in `missing_fields`.
Evidence: every populated field keeps source evidence in `source_fields`.

Source priority (highest first):
  1. schema.org JSON-LD published by the site  (`Product` / `RealEstateListing`
     / `BreadcrumbList`). Centaline's hk.centanet.com detail pages carry all of
     them, so price, saleable area, estate, district, block, floor, unit and
     posting date come out structurally instead of being scraped from markup.
  2. The rendered HTML: class-based hooks (`.price-number`, `.area`) and the
     legacy 售價／實用面積 text patterns.
  3. The embedded JS/JSON state (`"price":8300000`, `"saleableArea":741`).
  4. Optional LLM assist, gaps only, never overriding a rule-based fact.

`extract_from_html(html, source_url)` is exposed so tests / fixtures can drive the
same logic without a network call.
"""
from __future__ import annotations
import json, re, time, urllib.request
from html import unescape

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.llm import llm_available, chat_json

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}
FIELDS = ["estate", "block", "floor", "unit", "price", "usable_area_sqft",
          "gross_area_sqft", "layout", "bedrooms", "bathrooms", "orientation",
          "view", "asking_ppsf", "listing_date", "description"]

FLOOR_WORDS = ("地下", "頂層", "高層", "中層", "低層")


def _num(s):
    """Number from a string, honouring the Chinese 萬/億 scale words.

    "830萬" -> 8_300_000 · "1.2億" -> 120_000_000 · "8,300,000" -> 8300000.0
    (The old version deleted 萬, turning "700 萬" into 700 — a 10,000x error.)
    """
    if s is None or s == "":
        return None
    text = str(s)
    scale = 1.0
    if "億" in text:
        scale = 100_000_000.0
    elif "萬" in text or "万" in text:
        scale = 10_000.0
    stripped = re.sub(r"[,\s$＄]|HK|HKD|港元|元|萬|万|億|亿", "", text, flags=re.I)
    m = re.search(r"\d+(?:\.\d+)?", stripped)
    return float(m.group(0)) * scale if m else None


def _fetch_ex(url: str, attempts: int = 2) -> tuple[str | None, str | None]:
    """(html, reason). Retried once: a single timeout or transient 5xx used to
    abort the whole analysis and surface as a misleading "no price" error."""
    reason = None
    for i in range(max(1, attempts)):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status != 200:
                    reason = "HTTP %s" % r.status
                else:
                    raw = r.read()
                    for enc in ("utf-8", "big5", "gbk"):
                        try:
                            return raw.decode(enc), None
                        except Exception:
                            continue
                    return raw.decode("utf-8", "ignore"), None
        except Exception as e:
            reason = "%s: %s" % (type(e).__name__, e)
        if i + 1 < attempts:
            time.sleep(1.2)
    return None, reason


def _fetch(url: str) -> str | None:
    return _fetch_ex(url)[0]


def _meta(text, *names):
    for n in names:
        m = re.search(r'<meta[^>]+(?:name|property)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(n), text, re.I)
        if m:
            return unescape(m.group(1)).strip()
    return None


# --------------------------------------------------------------------------- #
#  1. schema.org JSON-LD                                                     #
# --------------------------------------------------------------------------- #
def _jsonld_objects(html: str):
    """Every schema.org object published on the page."""
    for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I):
        try:
            data = json.loads(unescape(m.group(1)).strip())
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            yield data
        # @graph style containers
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            for item in data["@graph"]:
                if isinstance(item, dict):
                    yield item


def _offer_price(offers, found: dict, ev: dict):
    """offers may be a dict, a list, or a bare number."""
    if found.get("price") is not None or offers is None:
        return
    cands = offers if isinstance(offers, list) else [offers]
    for o in cands:
        if isinstance(o, dict):
            o = o.get("price")
        v = _num(o)
        if v is not None and v >= 10000:
            found["price"] = v
            ev["price"] = {"value": v, "source": "jsonld"}
            return


_PHASE_OR_BLOCK = re.compile(
    r"^(\d+\s*期|[0-9A-Z]{1,4}\s*期|Phase\s*[0-9A-Z]+|Block\s*[0-9A-Z]+|"
    r"Tower\s*[0-9A-Z]+|Flat\s*[0-9A-Z]+|.+\s*座|\(.*座.*\))$", re.I)
_FLOOR_RE = re.compile(
    r"(地下|頂層|高層|中層|低層|\d+\s*樓|(?:Low|Middle|High|Upper|Lower|Ground)\s*Floor)",
    re.I)
_UNIT_RE = re.compile(r"[A-Z]{1,2}\s*室|\bFLAT\s*[0-9A-Z]{1,3}\b", re.I)
# Centaline separates the components of a name with "・" (zh: a space)
_NAME_SEP = " \u30fb\u2027\u00b7|\uFF0C,\u3001-"
_CRUMB_NOISE = ("主頁", "首頁", "買樓", "租樓", "賣樓", "Home Page", "Find Property")


def _split_crumbs(crumbs):
    return [c.strip() for c in (crumbs or []) if c and c.strip() not in _CRUMB_NOISE]


def _is_name_prefix(name: str, cand: str) -> bool:
    """True when `cand` is whole component of `name`.

    Both locales must pass: zh "瓏門 1期 5座" starts with "瓏門 " and en
    "OCEAN SHORES・PHASE 3・TOWER 11" starts with "OCEAN SHORES・" — matching a
    bare space separator only silently broke every English page.
    """
    if not cand or not name:
        return False
    if name == cand:
        return True
    return (name.startswith(cand) and len(name) > len(cand)
            and name[len(cand)] in _NAME_SEP)


def _estate_and_district(crumbs, product_name):
    """Estate + district from the breadcrumb trail.

    Centaline's trail reads
        主頁 > 買樓 > region > district > [district] > ESTATE > [phase] > [block]
    e.g. 新界西 > 屯門 > 屯門市中心 > 瓏門 > 1期 > 5座
    The estate is the LAST level that is a whole component of the listing's own
    name. Scanning backwards skips "Tuen Mun" (a district that also prefixes
    "Tuen Mun Town Plaza") and survives duplicated district levels, so the answer
    is the estate rather than a phase or a tower.
    """
    tail = _split_crumbs(crumbs)
    if not tail:
        return None, None
    name = (product_name or "").strip()
    idx = None
    if name:
        for i in range(len(tail) - 1, -1, -1):
            if _is_name_prefix(name, tail[i]):
                idx = i
                break
    if idx is None:                      # no name to anchor on: skip trailing levels
        i = len(tail) - 1
        while i >= 0 and _PHASE_OR_BLOCK.match(tail[i]):
            i -= 1
        idx = i if i >= 0 else None
    if idx is None:
        return None, None
    estate = tail[idx]
    j = idx - 1
    while j >= 0 and tail[j] == estate:  # district repeated in the trail
        j -= 1
    district = tail[j] if j >= 0 else None
    if district and "|" in district:     # "荃灣 | 麗城" -> "荃灣"
        district = district.split("|")[0].strip()
    return estate, district


def _block_from_crumbs(crumbs):
    tail = _split_crumbs(crumbs)
    if not tail:
        return None
    last = tail[-1]
    if "座" in last or re.match(r"^(Block|Tower|Flat)\s*[0-9A-Z]+$", last, re.I):
        return last
    return None


def _from_jsonld(html: str) -> tuple[dict, dict]:
    """Structured listing facts, or ({}, {}) when the page has no usable JSON-LD."""
    found: dict = {}
    ev: dict = {}
    product_name = None
    listing = None
    crumbs: list[str] = []

    for obj in _jsonld_objects(html):
        t = obj.get("@type")
        if t == "Product":
            product_name = product_name or (obj.get("name") or None)
            _offer_price(obj.get("offers"), found, ev)
        elif t == "RealEstateListing":
            listing = listing or obj
        elif t == "BreadcrumbList":
            for it in (obj.get("itemListElement") or []):
                if isinstance(it, dict) and it.get("name"):
                    crumbs.append(str(it["name"]).strip())

    # breadcrumb first: it gives the CLEANED district ("荃灣", not "荃灣 | 麗城"),
    # so it must win over the raw postal address below.
    estate, district = _estate_and_district(crumbs, product_name)
    if estate:
        found["estate"] = estate
    if district:
        found["district"] = district
    blk = _block_from_crumbs(crumbs)
    if blk:
        found["block"] = blk

    if listing:
        _offer_price(listing.get("offers"), found, ev)
        if listing.get("datePosted"):
            found.setdefault("listing_date", str(listing["datePosted"]).strip()[:10])
        ent = listing.get("mainEntity")
        if isinstance(ent, dict):
            addr = ent.get("address")
            if isinstance(addr, dict):
                d = addr.get("streetAddress") or addr.get("addressRegion")
                if d and not found.get("district"):
                    found["district"] = str(d).split("|")[0].strip()
            rooms = ent.get("numberOfRooms")
            if isinstance(rooms, (int, float)) and 0 < rooms < 20:
                found.setdefault("bedrooms", int(rooms))
            size = ent.get("floorSize")
            if isinstance(size, dict):
                v = _num(size.get("value"))
                if v and 50 <= v <= 20000:
                    found.setdefault("usable_area_sqft", v)
                    ev["usable_area_sqft"] = {"value": v, "source": "jsonld"}
        m = _FLOOR_RE.search(str(listing.get("name") or ""))
        if m:
            found.setdefault("floor", m.group(1).replace(" ", ""))
        m = _UNIT_RE.search(str(listing.get("name") or ""))
        if m:
            found.setdefault("unit", re.sub(r"\s+", "", m.group(0)))

    if not found.get("estate") and product_name:
        found["estate"] = str(product_name).strip()

    if found:
        for k in ("estate", "district", "block", "floor", "unit", "listing_date", "bedrooms"):
            if k in found:
                ev.setdefault(k, {"value": found[k], "source": "jsonld"})
    return found, ev


def _is_rental_page(html: str, source_url: str, crumbs_rental: bool = False) -> bool:
    """A rent listing's headline price is a MONTHLY RENT — never a purchase price."""
    if "租" in (source_url or "") and "theme=rent" in (source_url or ""):
        return True
    return bool(re.search(r'["\'](?:租|月租)["\']?\s*[:：]|每月租金', html[:20000]))


# --------------------------------------------------------------------------- #
#  2. Main extractor                                                          #
# --------------------------------------------------------------------------- #
def extract_from_html(html: str, source_url: str = "", allow_llm: bool = True) -> dict:
    def ev(f, v, src=None):
        ev_store.setdefault(f, {"value": v, "source": src or source_url[:120]})
    ev_store: dict = {}
    base = {k: None for k in FIELDS}

    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
    title = unescape(title).strip() if title else ""

    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", text)
    body = unescape(re.sub(r"\s+", " ", body))

    # ---- 1. JSON-LD (authoritative when present) ------------------------- #
    jl, jl_ev = _from_jsonld(html)
    rental_page = _is_rental_page(html, source_url)
    for k, v in jl.items():
        if k == "price" and rental_page:
            continue                      # a rent figure must never become asking price
        base[k] = v
    for k, v in jl_ev.items():
        ev_store.setdefault(k, v)

    # ---- 2. rendered markup ---------------------------------------------- #
    if base["price"] is None and not rental_page:
        # Centaline renders "$ <span>830</span><label> 萬</label>" inside .price-number
        m = re.search(r'class="price-number"[^>]*>(.{0,200}?)</div>', html, re.S | re.I)
        if m:
            v = _num(unescape(re.sub(r"<[^>]+>", " ", m.group(1))))
            if v and 100000 <= v <= 3_000_000_000:
                base["price"] = v
                ev("price", v, "html:.price-number")
        if base["price"] is None:
            for pat in (r"售價[^\d$]{0,6}([\d,]{5,})",
                        r"(?:HK\$|HK\\?\$)?[\s]?([\d,]{2,7})\s*萬",
                        r'"price"\s*:\s*"?([\d,]+)"?'):
                m = re.search(pat, body + " " + title)
                if m:
                    v = _num(m.group(1) + ("萬" if "萬" in pat else ""))
                    if v and v >= 100000:
                        base["price"] = v
                        ev("price", v)
                        break

    if base["usable_area_sqft"] is None:
        # Centaline: <span class="sy-tag">實用</span> ... <span class="area">741</span> 呎
        m = re.search(r'class="area"[^>]*>\s*([\d,]{2,6})', html)
        if m:
            v = _num(m.group(1))
            if v and 50 <= v <= 20000:
                base["usable_area_sqft"] = v
                ev("usable_area_sqft", v, "html:.area")
        else:
            m = re.search(r"實用面積[^\d]{0,8}([\d,]+)", body)
            if m:
                v = _num(m.group(1))
                if v:
                    base["usable_area_sqft"] = v
                    ev("usable_area_sqft", v)

    m = re.search(r"建築面積[^\d]{0,8}([\d,]+)", body)
    if m:
        base["gross_area_sqft"] = _num(m.group(1))

    m = re.search(r"(\d)\s*房", body)
    if m and base["bedrooms"] is None:
        base["bedrooms"] = int(m.group(1))
        ev("bedrooms", base["bedrooms"])
    m = re.search(r"(\d)\s*廁", body)
    if m:
        base["bathrooms"] = int(m.group(1))
    for kw, field in (("海景", "view"), ("園景", "view"), ("山景", "view"),
                      ("向東南", "orientation"), ("向南", "orientation"), ("向西南", "orientation"),
                      ("向北", "orientation"), ("向東", "orientation"), ("向西", "orientation")):
        if kw in body[:600] or kw in title:
            base[field] = base[field] or kw

    # estate/block guess from the title ONLY when JSON-LD gave nothing
    if base["estate"] is None:
        tl = title.replace("香港住宅物業", "").strip()
        for sep in ("出售", "售", "-", "|", "｜"):
            if sep in tl:
                tl = tl.split(sep)[0].strip()
        if tl:
            base["estate"] = tl
            ev("estate", tl)

    # ---- 3. embedded JS/JSON state --------------------------------------- #
    scripts = " ".join(m for m in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I))
    if scripts:
        def _jval(keypat):
            m = re.search(keypat, scripts)
            if m:
                return m.group(1).replace(",", "").replace('"', "").strip()
            return None
        if base["usable_area_sqft"] is None:
            v = _jval(r'"(?:saleableArea|saleable_area|usableArea|usable_area|sfa|area)"\s*:\s*"?([0-9][0-9.,]*)"?')
            if v and 100 <= float(v) <= 10000:
                base["usable_area_sqft"] = float(v)
                ev("usable_area_sqft", v)
        if base["gross_area_sqft"] is None:
            v = _jval(r'"(?:grossArea|gross_area|gfa)"\s*:\s*"?([0-9][0-9.,]*)"?')
            if v and 100 <= float(v) <= 10000:
                base["gross_area_sqft"] = float(v)
        if base["bedrooms"] is None:
            v = _jval(r'"(?:bedrooms?|room_num|rooms?)"\s*:\s*"?([0-9])"?')
            if v:
                base["bedrooms"] = int(v)
                ev("bedrooms", int(v))
        for key, field in (("floor", "floor"), ("unit", "unit"), ("block", "block")):
            if base[field] is None:
                v = _jval(r'"%s"\s*:\s*"?([^",}\]]+)"?' % key)
                if v and len(v) < 24:
                    base[field] = v.strip()
        if base["price"] is None and not rental_page:
            v = _jval(r'"(?:price|askingPrice|售價)"\s*:\s*"?([0-9][0-9.,]*)"?')
            if v and float(v) >= 100000:
                base["price"] = float(v)
                ev("price", v)

    if base["price"] and base["usable_area_sqft"]:
        base["asking_ppsf"] = round(base["price"] / base["usable_area_sqft"], 0)

    # ---- 4. optional LLM assist (fills gaps only, never overrides) -------- #
    if allow_llm and llm_available():
        gaps = [k for k in FIELDS if base[k] is None and k not in ("gross_area_sqft", "bathrooms")]
        if gaps:
            res = chat_json(
                "Extract real-estate fields from a HK property listing. Return strict JSON "
                "with only these keys and null when not present: "
                + ",".join(gaps) + ". Never invent values.",
                "URL: %s\nTITLE: %s\nTEXT(first 2500):\n%s" % (source_url, title, body[:2500]))
            if isinstance(res, dict):
                for k in gaps:
                    if res.get(k) is not None and base[k] is None:
                        base[k] = res[k]
                        ev_store.setdefault(k, {"value": res[k],
                                                "source": "llm_assist(not verified)", "confidence": "low"})

    base["source_url"] = source_url
    base["listing_type"] = "rent" if rental_page else "sale"
    base["description"] = (base.get("description") or _meta(html, "og:description", "description")) or ""
    base["source_fields"] = ev_store
    base["missing_fields"] = [k for k in FIELDS if base.get(k) is None]
    return base


def extract_property(url: str, allow_llm: bool = True) -> dict:
    html, reason = _fetch_ex(url)
    if html is None:
        return {"source_url": url, "error": "fetch_failed_or_blocked",
                "fetch_reason": reason,
                "source_fields": {}, "missing_fields": FIELDS,
                "note": "could not retrieve listing (JS/anti-bot/network); extraction unavailable"}
    return extract_from_html(html, url, allow_llm)
