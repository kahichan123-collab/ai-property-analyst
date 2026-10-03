# -*- coding: utf-8 -*-
"""tools/regime.py — market regime engine (spec #15).

Deterministic keyword/signal classifier over the research entries produced by
tools/research.py. Two things are kept strictly apart and labelled:

    research sources  -> the raw entries (source, date, confidence)
    model output      -> the regime score/classification and its playbook

The regime is only ever stated with the evidence that produced it. With no
research evidence the engine says so (`neutral` + confidence "none") instead of
inventing a market view. The DeepSeek narrative may then interpret the regime —
it does not decide it.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import config as C

CONF_RANK = {"high": 3, "medium": 2, "low": 1}

REGIME_PLAYBOOK = {
    "recovery": {
        "property_impact": "成交量與價格由低位回升，議價空間開始收窄。",
        "downside_implication": "復甦初期仍可能反覆；若利率再度上升，回升可被打斷。",
        "underwriting_implication": "可以較貼近估值入市，但仍應以壓力情境 DSR 作為上限。",
    },
    "expansion": {
        "property_impact": "價格與租金同步向上，優質盤源去化快。",
        "downside_implication": "高位入市後若進入調整，短期資本損失風險較大。",
        "underwriting_implication": "避免追價；以公允價值與最大買入價作為硬上限。",
    },
    "neutral": {
        "property_impact": "價格與成交大致橫行，買賣雙方拉鋸。",
        "downside_implication": "區間上落，時間成本與持有成本成為主要風險。",
        "underwriting_implication": "現金流與租金回報比資本增值更值得重視。",
    },
    "correction": {
        "property_impact": "價格回落、成交轉弱，賣方議價意願提高。",
        "downside_implication": "估值可能持續下移；高 LTV 入市易出現負資產。",
        "underwriting_implication": "要求更大折讓、較低 LTV 與更保守的退出假設。",
    },
    "stress": {
        "property_impact": "成交萎縮、價格明顯受壓，流動性轉差。",
        "downside_implication": "難以在需要時變現；租金亦可能同步下跌。",
        "underwriting_implication": "除非價格顯著低於公允價值且現金流穩健，否則應觀望。",
    },
}


def _blob(entry: dict) -> str:
    """Text the signal rules match against.

    An entry may override this with an explicit ``matched_text`` field, for
    sources whose ``data.note`` *describes* a metric rather than reporting a
    movement (matching the note let definition text masquerade as direction).
    """
    override = entry.get("matched_text")
    if override:
        return str(override)
    d = entry.get("data") or {}
    return " ".join([str(entry.get("title") or ""), str(entry.get("source") or ""),
                     " ".join(f"{k} {v}" for k, v in d.items())])


def _declared_topics(entry: dict) -> set | None:
    """The topics a source is entitled to speak about, or None if unrestricted.

    Keyword matching alone cannot separate topics that share a word: "下跌"
    appears in the price_trend, rental_trend AND transaction_volume rule lists,
    so a Land Registry volume figure scored all three and the report announced
    price and rental trends it had no data for. An official source knows which
    topic it measures, so it declares ``allowed_topics`` and is only counted
    there. Entries without the field behave exactly as before.
    """
    declared = entry.get("allowed_topics")
    if not declared:
        return None
    return set(declared)


def _rule_vocabulary() -> dict:
    """movement word -> the topics whose rule lists contain it (derived, so it
    cannot drift away from REGIME_SIGNAL_RULES)."""
    vocab: dict[str, set] = {}
    for topic, (pos_kw, neg_kw) in C.REGIME_SIGNAL_RULES.items():
        for kw in list(pos_kw) + list(neg_kw):
            vocab.setdefault(kw, set()).add(topic)
    return vocab


def topics_for_word(*words: str) -> list:
    """Topics whose rules use any of these movement words (for data sources
    that emit direction words and must declare where they may be counted)."""
    vocab = _rule_vocabulary()
    out: set = set()
    for w in words:
        out |= vocab.get(w, set())
    return sorted(out)


def _confidence(entries: list, topics_hit: int) -> str:
    if not entries or not topics_hit:
        return "none"
    ranks = [CONF_RANK.get(str(e.get("confidence", "low")).lower(), 1) for e in entries]
    avg = sum(ranks) / len(ranks)
    if topics_hit >= 4 and avg >= 2.0:
        return "medium"
    if topics_hit >= 2:
        return "low_to_medium"
    return "low"


def classify(market_entries: list | None, mortgage_entries: list | None,
             policy_entries: list | None) -> dict:
    entries = [e for e in (list(market_entries or []) + list(mortgage_entries or [])
                           + list(policy_entries or []))
               if str(e.get("source")) != "unavailable"]
    signals = []
    score = 0
    for topic in C.REGIME_TOPICS:
        pos_kw, neg_kw = C.REGIME_SIGNAL_RULES.get(topic, ([], []))
        pos, neg, hits = [], [], []
        for e in entries:
            declared = _declared_topics(e)
            if declared is not None and topic not in declared:
                continue          # this source does not measure this topic
            blob = _blob(e)
            low = blob.lower()
            p = [k for k in pos_kw if k.lower() in low]
            n = [k for k in neg_kw if k.lower() in low]
            if p or n:
                hits.append(e)
            pos += p
            neg += n
        if not pos and not neg:
            continue
        direction = 0
        if pos and not neg:
            direction = 2
        elif neg and not pos:
            direction = -2
        score += direction
        signals.append({
            "topic": topic,
            "direction": ("positive" if direction > 0 else
                          "negative" if direction < 0 else "mixed"),
            "weight": direction,
            "positive_keywords": sorted(set(pos)),
            "negative_keywords": sorted(set(neg)),
            "evidence": [{"title": e.get("title"), "source": e.get("source"),
                          "date": e.get("date"), "confidence": e.get("confidence")}
                         for e in hits[:4]],
            "provenance": C.RESEARCH,
        })

    regime = C.regime_from_score(score) if signals else "neutral"
    play = REGIME_PLAYBOOK[regime]
    return {
        "regime": regime,
        "label": C.REGIME_LABELS[regime],
        "score": score,
        "confidence": _confidence(entries, len(signals)),
        "topics_assessed": len(signals),
        "topics_total": len(C.REGIME_TOPICS),
        "signals": signals,
        "data_available": bool(entries),
        "evidence_count": len(entries),
        "current_regime": C.REGIME_LABELS[regime],
        "property_impact": play["property_impact"],
        "downside_implication": play["downside_implication"],
        "underwriting_implication": play["underwriting_implication"],
        "provenance": {
            "regime_classification": C.MODEL,
            "signal_evidence": C.RESEARCH,
            "playbook_text": C.ASSUMED,
        },
        "note": ("Regime is a deterministic classification of the supplied research "
                 "entries; the raw sources are listed separately from the model output. "
                 "This is not a market forecast."),
    }
