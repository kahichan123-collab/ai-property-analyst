# AI Property Analyst — v2.0

**AI-assisted Hong Kong Residential Property Underwriting & Investment Analysis Tool**

```
Property URL / Manual Input
  → Property Data Extraction
  → Deterministic Underwriting
  → Acquisition Cost Model
  → Valuation Engine
  → Market Research
  → Scenario Engine (BEAR / BASE / BULL)
  → Interest-Rate Stress
  → Market Regime
  → Risk Engine
  → Transaction Boundary
  → Investment Verdict
  → DeepSeek narrative (optional, interpretation only)
  → Investment Report
```

---

## 1. The one rule everything else follows

> **Deterministic Calculation First → AI Interpretation Second.**
> **AI 不是計算器。**

The LLM/DeepSeek may **never** compute or modify:

mortgage payment · loan amount · LTV · DSR · acquisition cash requirement ·
gross/net rental yield · NOI · annual cash flow · cash-on-cash return · IRR ·
equity multiple · break-even rent · break-even price · exit proceeds ·
stress-test outputs · fair value · maximum buy price · target rent ·
interest-rate ceiling · risk level · verdict.

Every one of those is produced by plain Python in `tools/*.py` — no LLM call, no
network access, fully testable (`python -m tests.test_underwriting`,
`python -m tests.test_upgrade`).

DeepSeek is given the **finished numbers** and may only do:
qualitative market commentary · research synthesis · risk explanation ·
interpretation of the deterministic outputs · investment thesis ·
scenario explanation · final narrative. Its system prompt forbids inventing or
recomputing a number, and it must mark anything uncertain as
「需進一步核實」.

**No API key ≠ no analysis.** With no DeepSeek credential the whole pipeline
still completes; the report simply states that no AI narrative was attached.

---

## 2. Layout

```
property_analyst/
├─ analyst_server.py        local UI + JSON API (stdlib only)
├─ orchestrator.py          pipeline wiring: request → report dict
├─ analyst.py               CLI (flags for every input)
├─ run.py                   minimal deterministic-engine demo
├─ app/index.html           3-step responsive input UI (vanilla JS)
├─ tools/
│  ├─ config.py             ALL policy numbers: scenarios, stress steps,
│  │                        risk thresholds, valuation bands, boundary targets
│  ├─ schema.py             request normalisation + backend validation
│  ├─ underwriting.py       deterministic engine + solvers (break-even, ceilings)
│  ├─ acquisition.py        acquisition cost model + cash-constrained max price
│  ├─ scenarios.py          BEAR/BASE/BULL + interest-rate stress
│  ├─ valuation.py          asking vs fair value, premium/discount, classification
│  ├─ regime.py             market regime classification + playbook
│  ├─ risk.py               risk detection, level, Top 3 (Risk→Mechanism→Impact→Mitigation)
│  ├─ boundary.py           transaction boundary (the "under what conditions" engine)
│  ├─ verdict.py            BUY / NEGOTIATE / WATCH / PASS + conditional decision
│  ├─ provenance.py         data-integrity ledger
│  ├─ research.py           comparables / rental / market / mortgage / policy
│  ├─ official.py           HKMA + Land Registry official (Observed) sources
│  ├─ extract.py            URL → strict listing JSON (never guesses)
│  ├─ report.py             14-section standalone HTML report
│  ├─ stress.py             owner stress table
│  └─ llm.py                optional DeepSeek client
├─ tests/test_underwriting.py   PHASE-3 anchors (28 checks)
├─ tests/test_upgrade.py        v2.0 acceptance suite (172 checks)
└─ demo_research.json           optional demo research dataset (tagged demo)
```

---

## 3. Percentage convention (the `pct()` bug is gone)

The old heuristic `pct(v){return v>1 ? v/100 : v}` is **deleted**. It could not
distinguish `1`, `0.9`, `0.05` or `5`.

* Every percentage field in the UI is entered as a **percent number** and shows a
  visible `%` unit: `60`, `4.125`, `5`, `3`, `3.5`.
* The frontend converts **once**, with `value / 100`, before sending:
  `60 → 0.60`, `4.125 → 0.04125`, `5 → 0.05`, `3.5 → 0.035`
  (`app/index.html` → `PERCENT_FIELDS` / `toWire()`).
* The backend **re-validates every range** and does not trust the frontend:
  a raw percent such as `ltv: 60` is **rejected** with
  `貸款成數 LTV 必須介乎 0%–100%（收到 60.0）。` plus the hint
  `此欄位只接受 0%–100% 之間的分數（前端須傳 percent/100）。例如 60%（輸入 60）必須傳 0.60`.
* A `%` sign inside a numeric string is refused, never reinterpreted.

---

## 4. API

### `POST /api/analyse`

```json
{
  "mode": "auto" | "manual",
  "purpose": "owner_occupier" | "rental",
  "property":  { "url": "...", "estate": "...", "district": "...", "building": "...",
                 "layout": "...", "floor": "...", "special_features": "...",
                 "asking_price": 8300000, "usable_area_sqft": 741,
                 "gross_area_sqft": 980, "building_age": 8,
                 "monthly_mgmt_rates": 1200, "rates_amount": 350 },
  "finance":   { "gross_monthly_income": 45000, "monthly_net_income": 38000,
                 "other_recurring_income": 0, "existing_monthly_debt": 0,
                 "liquid_cash": 1500000, "available_cash_for_transaction": 1800000,
                 "emergency_reserve": 300000,
                 "max_comfortable_monthly_payment": 16000 },
  "financing": { "ltv": 0.60, "mortgage_rate": 0.04125, "tenure_years": 30,
                 "amortization": "PI", "fee_rate": 0.015, "discount_rate": 0.06 },
  "rental":    { "monthly_rent": 27000, "vacancy": 0.05, "rates_pct": 0.06,
                 "opex_pct": 0.08, "annual_capex": 10000, "rent_growth": 0.03,
                 "hold_years": 5, "exit_cap": 0.035, "sell_cost": 0.01 },
  "acquisition": { "agency_fee_pct": 0.01, "legal_fee_pct": 0.005,
                   "mortgage_fees": 0, "valuation_fee": 0,
                   "renovation_furnishing": 0, "initial_reserve": 0 },
  "overrides": { "price": 8300000, "area": 741 }
}
```

* `rental` is **only** accepted for `purpose: "rental"`. For owner-occupier the
  block is dropped and reported as a warning, so UI defaults can never pollute
  owner logic — and the frontend omits the key entirely.
* Unknown fields are ignored **and reported**, never silently used.
* All numerics are real JSON numbers. Numeric strings (incl. `"8,300,000"`) are
  coerced defensively and flagged, so a comma string can never reach the engine.
* The pre-2.0 flat body is shimmed into this schema and flagged
  `legacy_flat_payload`.

**Responses**

```
200 { ok, verdict, verdict_code, why, summary, metrics, risks, risk_level,
      scenarios, transaction_boundary, valuation, errors: [], warnings,
      data_quality, pipeline_log, llm_available, engine_version, html }
400 { ok:false, errors:[{field:"financing.ltv", code, message, hint}], warnings }
500 { ok:false, errors:[{field:"server", ...}] }
```

`field` is a dotted path that maps 1:1 onto the UI inputs via `data-field`
attributes, so the error appears **under the field**
(e.g.「LTV 必須介乎 0%–100%」), never just「分析失敗」.

Other endpoints: `GET /api/health`, `GET /api/schema` (field specs, scenario
config, provenance vocabulary — the frontend displays scenario assumptions as
read-only system config), `GET /` (UI).

The server retries a failed analysis once; the UI retries transient
network/5xx failures once and never loses the result: if `window.open` is
blocked by the popup blocker, the report is kept in memory with
**開啟報告 Open Report** and **下載報告 Download** fallbacks.

---

## 5. Engines

### 5.1 Acquisition cost model (spec #9)

`Down Payment` and `Total Initial Cash Required` are different numbers and are
never conflated:

```
Purchase price − mortgage amount          = Down payment
+ stamp duty (HK AVD scale, 2024 撤辣)
+ agency fee (buyer side)   + legal fee
+ mortgage-related fees     + valuation fee
+ renovation / furnishing   + initial reserve
                                          = Total Initial Cash Required
```

Optional line items default to 0 (nothing is silently added). Agency+legal
default to the legacy combined `fee_rate` split 2:1, so the PHASE-3 anchor
(10M / LTV 70% → 3,520,000) is unchanged. A 6-month reserve is **advised**, never
auto-added. Mortgage-insurance is estimated above 70% LTV, labelled
`Assumption`, and excluded from the cash call. `max_price_for_cash()` solves the
largest price whose *total* cash fits the available budget (bisection, because
the AVD scale is piecewise).

### 5.2 Rental underwriting (spec #10)

PGI → EGI → rates → opex → pre-tax NOI → property tax
`0.12 × max(0, EGI − rates)` → after-tax NOI → debt service → annual/monthly
cash flow, gross yield, net yield, going-in cap rate, cash-on-cash, DSCR per
year, levered/unlevered IRR & NPV, equity multiple, exit value, net exit
proceeds, break-even rent (DSCR 1.00x / 1.25x) and break-even price.

### 5.3 Scenario engine (spec #11)

Shocks live in `tools/config.py` — **not** in the frontend, and never taken from
a request body:

| | rent | vacancy | growth | rate | exit cap | value p.a.* |
|---|---|---|---|---|---|---|
| BEAR | −10% | +5pp | ×0.5 | +1.00pp | ×1.15 | −2% |
| BASE | user assumptions unchanged | | | | | 0% |
| BULL | +8% | −2pp | ×1.5 | −0.25pp | ×0.92 | +3% |

Each scenario reports property value, rent, mortgage payment, annual cash flow,
yield, IRR and equity multiple. (*The market-value growth column is used only for
the owner-occupier illustrative exit, where no rent roll exists; it is labelled
`Assumption`.)

### 5.4 Interest-rate stress (spec #12)

Rates `+1.00pp` / `+2.00pp`, re-running the model: monthly payment, DSR, annual
cash flow, debt service and cash-flow deterioration, shown as
**Base affordability vs Stress affordability**. Framed explicitly as
*Mortgage Affordability Stress Test / Interest Rate Sensitivity — an internal
sensitivity analysis, **NOT** a statement of bank approval policy.*

### 5.5 Valuation (spec #14)

`Fair value = adjusted comparable benchmark psf × area`, `premium = asking /
fair value − 1`, classified as 明顯低於估值 / 低於估值 / 估值合理 / 高於估值 /
明顯高於估值 — or **Insufficient Data** when no comparable evidence is
connected. The engine never fabricates a fair value; an income-approach
cross-check exists but is labelled `Assumption` and never sets the headline
number.

**User-supplied comparable evidence (STEP 1 → 可比成交).** There is no free
feed of Hong Kong transaction prices, so the UI accepts 2–5 comparables
(estate / layout / price / area) as an optional list, sent as `comparables`.
They are validated in `tools/schema.py`, tagged `user-input`, override anything
a connected source returns, and flow through the same deterministic
`compute_comparable_metrics()` — so the valuation stops being `Insufficient
Data` and the verdict caveat disappears. Two honesty rules are enforced:

* **At least 2 usable comparables** (`research.MIN_COMPARABLES_FOR_BENCHMARK`).
  One data point is not a benchmark — it can be a distressed sale or a
  different floor — so a single comparable leaves the valuation at
  `Insufficient Data` and says why.
* **Per-entry breakdown.** The report lists every comparable with its psf and
  its deviation from the benchmark, and warns above ±15%, so a stale or
  mistyped entry cannot hide inside a median.
* An entry missing price or area is dropped with a warning — never guessed.

### 5.6 Market regime (spec #15)

Deterministic keyword/signal classification across interest rates, mortgage
conditions, transaction volume, price trend, rental trend, supply, policy and
macro → Recovery / Expansion / Neutral / Correction / Stress, with
property impact, downside implication and underwriting implication. Research
entries are listed separately from the model's classification; with no evidence
at all the regime says `neutral` with confidence `none` instead of inventing a
view.

**Official data sources (`tools/official.py`).** Free, documented, official
sources are wired so the regime is not evidence-free by default:

| Source | Provides | Endpoint |
|---|---|---|
| HKMA Residential Mortgage Survey | new-loan pricing (HIBOR / BLR), approvals, average LTV, delinquency ratio | `api.hkma.gov.hk` (documented JSON API) |
| Land Registry via DATA.GOV.HK | agreements for sale & purchase received for registration, by consideration band | `data.gov.hk` CKAN → `landreg.gov.hk/datagovhk/consideration_<year>.json` |

Every such entry is tagged `Observed` with the publisher, URL and period, cached
under `.cache/` for 12 hours, and degrades to nothing on failure — a broken
source can never break an analysis. Disable with `OFFICIAL_DATA=0`.

**Topic scoping (`allowed_topics`).** Keyword matching alone cannot separate
topics that share a word: 「下跌」appears in the `price_trend`, `rental_trend`
*and* `transaction_volume` rule lists, so a Land Registry volume figure once
scored all three and the report announced price and rental trends it had no data
for. A source that knows what it measures now declares `allowed_topics` (derived
from `REGIME_SIGNAL_RULES`, so it cannot drift) and is only counted there.
Entries without the field behave exactly as before.

### 5.7 Risk engine (spec #16)

Detects high LTV · weak income coverage · high (stress) DSR · negative rental
cash flow · high interest-rate sensitivity · low rental yield · high asking
premium · old building · maintenance exposure · liquidity · concentration ·
exit-assumption risk. Interest-rate sensitivity is judged by
**payment shock × ability to absorb** (stress DSR / stressed cash flow), not by
the percentage move alone. Output: Risk Level (Low/Medium/High/Very High) plus
**Top 3** risks, each as Risk → Mechanism → Financial Impact → Mitigation.

### 5.8 Transaction boundary (spec #17) — the core new capability

```
Maximum Buy Price        = tightest of: base-DSR cap, stress-DSR cap,
                           custom comfortable payment, available cash,
                           fair value less safety margin,
                           (rental) DSCR 1.25x, DSCR 1.00x at +2pp,
                           required net yield, positive cash flow
Target Rent              = most demanding of: DSCR 1.25x, required net yield,
                           cash-flow break-even
Maximum Comfortable LTV  = min(base-DSR cap, stress-DSR cap, policy cap)
Interest-rate Ceiling    = rate at which DSR hits the 50% bank cap
Minimum Rental Yield     = max(2.00% floor, mortgage rate + 1.00pp)
Downside boundary        = equity cushion, break-even vacancy, break-even rate
```

Every constraint is listed with its basis and the binding one is marked, so the
answer is auditable rather than a single opaque number.

### 5.9 Investment verdict (spec #18)

Fixed vocabulary **BUY / NEGOTIATE / WATCH / PASS**, driven by deterministic
blockers and buy-checks, plus:

```
BUY IF   price ≤ X AND rent ≥ Y AND stress DSR ≤ Z AND net yield ≥ W AND risk ≤ Medium
PASS IF  price > X OR stress cash flow < 0 OR fair-value premium > 15%
         OR stress DSR > 60% OR risk level = Very High
```

together with `what would change the decision` (price cut needed, rent needed,
rate ceiling, LTV reduction, missing evidence).

### 5.10 Property data extraction (URL → listing JSON)

`tools/extract.py` reads a listing in four passes, highest confidence first, and
**never guesses**: a field that is not on the page stays `None` and is listed in
`missing_fields`.

1. **schema.org JSON-LD** — Centaline publishes a `Product`, a
   `RealEstateListing` (price, `floorSize.value`, `numberOfRooms`, `datePosted`,
   address) and a `BreadcrumbList` on every detail page. Price, saleable area,
   estate, district, block, floor, unit and posting date come out of this
   structurally, so they survive layout changes that break CSS selectors.
   The estate is the **last breadcrumb level that is a whole component of the
   listing's own name** — scanning backwards is what keeps "Tuen Mun" (a district
   that also prefixes "Tuen Mun Town Plaza") and phase levels such as "1期" /
   "PHASE 3" from being mistaken for the estate.
2. **Rendered markup** — class hooks (`.price-number`, `.area`) and the legacy
   售價／實用面積 patterns.
3. **Embedded JS/JSON state** — `"price":8300000`, `"saleableArea":741`.
4. **Optional LLM assist** — fills remaining gaps only, never overrides a
   rule-based fact.

Two correctness rules are enforced here:

* **萬/億 are scales, not noise.** `_num("700 萬")` is 7,000,000 — the old parser
  deleted 萬 and returned 700, a 10,000× error.
* **A rent page's headline price is a monthly rent**, never a purchase price
  (`listing_type: "rent"`), so it can never be used as the asking price.

`analyst_server.py` serves the same `app/index.html`; a fetch is retried once,
because a single timeout used to abort the whole analysis and surface as a
misleading "the page has no price".

### 5.11 The address bar name (no administrator rights needed)

The server listens on **port 80** and binds loopback on **both stacks**
(`127.0.0.1` and `::1`), so the tool answers at

```
http://hk-ai-property-analyst.localhost/
```

A hostname cannot contain spaces — `http://HK AI Property Analyst/` is a search
query, not a URL. `<name>.localhost` is resolved by the OS itself (RFC 6761), so
this needs **no hosts-file edit and no elevated rights**; `APP_HOSTNAME` in
`analyst_server.py` is the single place to change it. `::1` matters: `*.localhost`
carries both an A and an AAAA record, so an IPv4-only bind leaves the pretty URL
unreachable (and a local proxy reports it as a bare `502`). `http://127.0.0.1/`
still serves the identical app.

### 5.12 Sharing it, and the password gate

Windows 上最省事的是雙擊 `start_analyst.cmd`：它會啟動伺服器、**等到服務真的回應
才開瀏覽器**，並且在已經有實例在跑時直接開頁面而不是重複綁埠；若偵測到上次用隨機
密碼啟動的殘留實例，會先把它收掉。等效的指令版本：

```bash
python analyst_server.py                 # loopback only (default)
python analyst_server.py 80 lan          # every interface — LAN can reach it
set ANALYST_PASSWORD=...                 # change the password ("" disables the gate)
set ANALYST_BIND=lan                     # same as passing "lan"
```

The whole app — UI and API alike — sits behind **HTTP Basic auth** (any username,
password from `ANALYST_PASSWORD`; when that variable is unset the server generates a
random one and prints it once — the launcher pins it in `start_analyst.ps1`). Basic auth is used because it
needs no login page and the browser replays the credentials on the page's own
`fetch()` calls automatically, so `app/index.html` needs no knowledge of it.
Failed attempts are delayed by `FAILED_AUTH_DELAY` to slow guessing.

Who can reach it:

| Bind | Loopback | Same Wi-Fi | Internet |
|---|---|---|---|
| `local` (default) | yes | no | no |
| `lan` | yes | yes, via `http://<lan-ip>/` | no |

Notes worth keeping in mind:

* The name `*.localhost` is resolved **per machine**, so it is not shareable —
  anyone else typing it reaches their own computer. Share the LAN IP instead.
* Basic auth is cleartext over plain HTTP. On a LAN it is a deliberate speed
  bump, not transport-grade security; put it behind HTTPS (a tunnel or reverse
  proxy) if it ever faces the internet.
* **Every analysis spends the owner's DeepSeek credits** and makes the server
  fetch third-party pages. An unprotected instance is an open wallet, which is
  why the gate is on by default rather than opt-in.
* Windows Defender Firewall is on for all profiles here; an inbound *Allow* rule
  for `python.exe` already exists on the Public profile. If the Wi-Fi is
  classified Private and other devices cannot connect, allow `python.exe` when
  Windows prompts.

### 5.13 Client-side behaviour: keyboard, drafts, cross-field rules

**The step strip is a real tab widget.** It used to be three inert `<div>`s —
not merely unreachable by keyboard, but unclickable by mouse too. It is now a
`role="tablist"` of `<button role="tab">` elements with `aria-selected` and
`aria-controls`, the panels are `role="tabpanel"` with `aria-labelledby`, and the
ARIA tabs keyboard contract is implemented: **Tab reaches the strip once**
(roving `tabindex`), **←/→/↑/↓ move between steps, Home/End jump to the ends**,
and Enter/Space activate. Moving forward still runs the same validation as
*Next*; when it fails, focus deliberately stays on the offending field rather
than being pulled back to the tab.

**Drafts survive an accidental refresh.** Every keystroke, step change and mode
change writes the whole form to `sessionStorage` under `hkap.draft.v1`, and
`init()` restores it (fields, mode, purpose and the step you were on). Storage is
`sessionStorage` rather than `localStorage` on purpose: it survives F5 but dies
with the tab, so personal financial figures are not left on the machine
indefinitely. Writes are suppressed until `state.ready` is set, so restoring can
never overwrite the draft it is reading. A **清除草稿 Clear draft** button wipes
it and reloads. Every storage call is wrapped — private mode or a quota error
degrades to "no draft" rather than breaking the form.

**Cross-field rules are enforced on both sides.** `FIELD_SPECS` only
range-checks one value at a time, so `tools/schema.py::cross_field_checks()`
adds the rules that need two values visible together, and the browser mirrors
them in `crossChecks()` for immediate feedback. The backend keeps its own copy
because it never trusts the frontend.

| Rule | Severity | Why |
|---|---|---|
| 實用面積 > 建築面積 | **error** | physically impossible |
| 每月淨收入 > 每月總收入 | warning | usually a typo |
| 最高舒適月供 > 每月淨收入 | warning | the payment would consume all income |
| 可用交易現金 > 可動用現金 | warning | the money has to come from somewhere |

Impossible combinations block; merely surprising ones are surfaced in an amber
**提醒（不阻擋送出）** box and never block submission, so a legitimate edge case
is flagged without being locked out.

**Verifying the client logic.** `node tools/_ui_check.js` runs the real inline
script from `app/index.html` against a small DOM stub and exercises the field
rules, cross-field rules, tab ARIA state and draft round-trip (**20 checks**).
It is not a browser, but it means the client rules are executed rather than
eyeballed. Backend cross-field rules are covered by `case_11_cross_field()` in
`tests/test_upgrade.py`.

**Measured on 24 live Centaline detail pages** (buy list, `tools/_bench_centanet.py`),
Chinese and English locales:

| | price | saleable area | estate name |
|---|---|---|---|
| before | 24/24 | **1/24** | mostly a phase or tower ("1期", "Tower 11") |
| after | 24/24 | **24/24** | 24/24 correct |

`resolve_inputs()` maps the extractor's `price` onto the schema's
`asking_price` — the two names differ, and looking up the wrong one silently
discarded a correctly extracted price. User input and
`overrides.price` always win over anything extracted.

---

## 6. Report structure (spec #19)

Page 1 shows the verdict banner and a KPI board — Verdict · Asking Price · Fair
Value · Cash Required · Mortgage · Monthly Payment · Net Yield · Base IRR ·
Stress IRR · Risk Level — followed by the 14 sections:

1 Executive Summary · 2 Property Snapshot · 3 Financing · 4 Acquisition Cost ·
5 Rental Economics · 6 Valuation · 7 Scenario Analysis · 8 Interest Rate Stress
Test · 9 Market Regime · 10 Risk Analysis · 11 Transaction Boundary ·
12 Investment Verdict · 13 Data Quality & Assumptions · 14 Sources

Plus the deterministic-first pipeline log and, when a key exists, the AI
narrative clearly marked 「輔助解釋，不取代任何數字」.

---

## 7. Data integrity (spec #20)

Every material number carries a provenance badge —
`Observed` · `User Input` · `Estimated` · `Assumption` · `Research Approximation`
· `Model Output` — with a confidence and a basis, summarised in section 13
(hard evidence vs soft/assumed items, evidence ratio, unresolved inputs,
warnings). Research sources are always displayed separately from model output.
The rule **「資料不足 → AI 猜一個數字」is forbidden** is enforced structurally:
valuation returns `Insufficient Data`, the regime returns `neutral/none`, and the
verdict names the missing inputs.

**Missing market evidence ≠ missing financial input.** A missing comparable set
does not make the financial case unknown — affordability, the transaction
boundary and the stress tests are all complete without it. So the verdict is
*not* downgraded to WATCH for it (a buyer with overwhelming cash would otherwise
be told "insufficient data" forever). Instead the verdict carries a mandatory
`caveats` entry (`valuation_unverified`) that is stated in the JSON, in the
API response and in the report banner — and it still blocks the
`valuation_available` / `premium_acceptable` BUY checks. Only inputs that make
the *financial* case unknown (e.g. no income) force WATCH, via
`verdict.missing_inputs`.

---

## 8. Running it

**Requires Python 3.10+ — standard library only, no third-party packages.**

```bash
# web UI + API
python analyst_server.py                 # http://hk-ai-property-analyst.localhost/
python analyst_server.py 8765            # different port: ...:8765/
set RESEARCH_DATA_FILE=demo_research.json   # optional: demo research dataset
set OFFICIAL_DATA=0                         # optional: disable HKMA/Land Registry fetch

# CLI (percentages typed as percentages)
python analyst.py --url "https://hk.centanet.com/..." --income 90000 \
    --ltv 60 --rate 4.125 --tenure 30 --cash 1500000 --available-cash 1800000 \
    --out report.html
python analyst.py --price 8300000 --area 741 --income 90000 --ltv 60 --rate 4.125 \
    --purpose rental --rent 27000 --vacancy 5 --exit-cap 3.5 --hold-years 5 \
    --json out.json --out report_rental.html
python analyst.py --url "https://example/listing" --html-file fixtures/sample_listing.html \
    --price 8300000 --area 741 --income 90000 --ltv 60 --rate 4.125   # offline fixture

# deterministic engine demo + tests
python run.py
python -m tests.test_underwriting     # 28 PHASE-3 anchors
python -m tests.test_upgrade          # 172 v2.0 acceptance checks
```

---

## 9. Self-test results (spec #24)

`python -m tests.test_upgrade` → **172 checks PASSED, 0 FAILED**, with and
without a research dataset configured.

| # | Case | Result |
|---|---|---|
| 1 | LTV = 60 → 0.60 | ✅ frontend `/100`, engine loan = price × 0.60; raw `60` rejected |
| 2 | Rate = 4.125 → 0.04125 | ✅ payment matches deterministic `pmt()`; raw `4.125` rejected |
| 3 | Vacancy = 5 → 0.05 | ✅ EGI recomputed with 5% vacancy; raw `5` rejected |
| 4 | Owner-occupier sends no rental assumptions | ✅ key absent in UI payload; backend drops + warns if present |
| 5 | Rental computes yield / NOI / cash flow / IRR | ✅ each metric re-derived independently in the test |
| 6 | API loading disables all inputs | ✅ `setBusy()` disables input/select/textarea/button, blocks step switching, guards duplicate submit; loading/success/error states |
| 7 | Invalid LTV = 130 rejected | ✅ HTTP 400, `financing.ltv`, 「LTV 必須介乎 0%–100%」 |
| 8 | Price 8,300,000 displayed, numeric to backend | ✅ `8,300,000` shown, `8300000.0` received; `%` inside numbers refused |
| 9 | No DeepSeek key → underwriting still completes | ✅ full report, narrative simply absent, skip logged |
| 10 | Rate +2% recomputes mortgage / DSR / cash flow | ✅ +2.00pp row matches `pmt()`, DSR and cash flow recomputed |

Plus engine invariants: acquisition identity (3,520,000 anchor preserved),
boundary derivations and binding-constraint logic, scenario ordering
(bear < base < bull), risk vocabulary and Top-3 cap, verdict vocabulary and
conditional expressions, all 14 report sections, provenance badges, no
unresolved template braces, and a **live HTTP end-to-end test** against
`ThreadingHTTPServer` (health / schema / UI / valid owner POST / invalid LTV 400 /
rental POST).

The PHASE-3 anchor suite (`tests/test_underwriting.py`, 28 checks) still passes
unchanged: AVD 3M→100, 5M→112,500, 10M→370,000, 15M→562,500, 20M→750,000;
10M/LTV 70%/4.125%/30y → PMT 33,925, cash 3,520,000, DSR 56.5%; property tax
38,448, after-tax NOI 254,592, DSCR 0.84.

---

## 10. Known limitations

* Research sources are not yet connected to live transaction/rental feeds — supply
  `RESEARCH_DATA_FILE` or the valuation honestly returns `Insufficient Data`.
  Demo entries are tagged `source="demo-dataset"`.
* IRR uses annual year-end cash flows, not monthly.
* The mortgage rate is held constant across the hold period; only the stress
  table varies it.
* Owner-occupier IRR needs a sale assumption (stated in the report).
* Bank approval is **not** predicted — `Indicative Financing Capacity` only.
* AVD bands and the >HK$100M rate must be re-verified if the law changes.
* Minimum rental yield uses a positive-carry hurdle (rate + 1.00pp). In a
  negative-carry market this legitimately produces a Maximum Buy Price well below
  the asking price; every constraint and the binding one are printed so the
  conclusion can be audited.
