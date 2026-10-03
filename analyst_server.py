# -*- coding: utf-8 -*-
"""analyst_server.py — local web UI + JSON API for AI Property Analyst (v2.0).

Run:
    python analyst_server.py [port] [scope]        # default port 80, loopback only
    set RESEARCH_DATA_FILE=...\\demo_research.json  # optional research dataset

Endpoints
    GET  /                 the 3-step input UI
    GET  /api/schema       field specs, scenario config, provenance vocabulary
    GET  /api/health       engine/schema version + whether AI narrative is available
    POST /api/analyse      {"mode","purpose","property","finance","financing",
                            "rental","acquisition","overrides"}
        -> 200 {ok, verdict, summary, metrics, risks, scenarios, html, errors, warnings}
        -> 400 {ok:false, errors:[{field,message,hint}], warnings}
        -> 500 {ok:false, errors:[...]}   (deterministic engine never needs an API key)

Standard library only. The DeepSeek narrative is optional: with no API key the
deterministic underwriting, valuation, scenarios, risks, boundary and verdict
all still complete and are returned.
"""
from __future__ import annotations

import base64, hmac, json, os, socket, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools import config as C
from tools import schema as sc
from tools.llm import llm_available
from orchestrator import analyse_request
from tools.report import render

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
MAX_BODY = 1 << 20          # 1 MiB is plenty for an input form

# The address bar name. A hostname cannot contain spaces, so the site is reached
# at <name>.localhost — resolved by the OS to loopback, so it needs no hosts-file
# edit and no administrator rights.
APP_TITLE = "AI Property Analyst"
APP_HOSTNAME = "hk-ai-property-analyst"
DEFAULT_PORT = 80

# ---- access control ------------------------------------------------------- #
# The tool has NO accounts and NO per-user data; this is a single shared door.
# It exists because every analysis spends the owner's DeepSeek credits and makes
# the server fetch third-party pages, so an open door is an open wallet.
# Any username is accepted — only the password is checked.
# Change it with:  set ANALYST_PASSWORD=...   (empty string disables the gate)
#
# SECURITY (fix, audit B10): this used to default to the literal "1234", printed
# to the console, over cleartext HTTP Basic auth. On a LAN bind that is an open
# wallet protected by a password everyone knows. When ANALYST_PASSWORD is not
# set we now GENERATE a random one and print it once, which is strictly safer
# than a default and still leaves the zero-configuration path working.
APP_PASSWORD = os.environ.get("ANALYST_PASSWORD")
APP_PASSWORD_GENERATED = False
if APP_PASSWORD is None:
    import secrets as _secrets
    APP_PASSWORD = _secrets.token_urlsafe(9)
    APP_PASSWORD_GENERATED = True
APP_REALM = "AI Property Analyst"
FAILED_AUTH_DELAY = 0.5        # seconds; slows down password guessing

# Per-IP failure budget: FAILED_AUTH_DELAY only slows one guessing thread; a
# scripted guesser gets AUTH_FAIL_LIMIT failures per window, then a temporary
# block. In-memory and per-process — restarting the server clears all state.
AUTH_FAIL_WINDOW = 60.0     # seconds a failure stays counted
AUTH_FAIL_LIMIT  = 8        # failures within the window -> temporary block
AUTH_BLOCK_TIME  = 300.0    # block duration in seconds
_auth_fails: dict[str, list[float]] = {}
_auth_blocked_until: dict[str, float] = {}
_auth_lock = threading.Lock()


def _lan_ip() -> str | None:
    """Best-effort primary LAN address (no packet is actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None

with open(os.path.join(APP_DIR, "index.html"), encoding="utf-8") as f:
    INDEX = f.read()


def _run_analysis(body: dict, attempts: int = 2) -> dict:
    """Analyse with one retry for transient/unknown failures (spec #21)."""
    last = None
    for i in range(max(1, attempts)):
        try:
            return analyse_request(body)
        except Exception as e:                      # pragma: no cover - defensive
            last = e
            traceback.print_exc()
    raise last


class H(BaseHTTPRequestHandler):
    server_version = "PropertyAnalyst/" + C.ENGINE_VERSION

    def log_message(self, fmt, *args):              # quieter console
        pass

    # ------------------------------------------------------------------ #
    def _send(self, code, ctype, body: bytes, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    # ------------------------------------------------------------------ #
    def _authorized(self) -> bool:
        """HTTP Basic gate over the whole app (UI and API alike).

        Basic auth is chosen because it needs no login page and the browser
        replays the credentials on the page's own fetch() calls automatically.
        Note it is sent in cleartext over plain HTTP: on a LAN it is a
        deliberate speed bump, not transport-grade security.
        FAILED_AUTH_DELAY slows a single guessing thread; the per-IP failure
        budget escalates scripted guessing to a temporary block.
        """
        if not APP_PASSWORD:
            return True
        ip = self.client_address[0] if self.client_address else "?"
        now = time.time()

        with _auth_lock:
            until = _auth_blocked_until.get(ip, 0.0)
            if now < until:
                self._send(429, "text/plain; charset=utf-8",
                           "嘗試次數過多，請稍後再試 / Too many attempts\n".encode("utf-8"),
                           {"Retry-After": str(int(until - now) + 1)})
                return False

        raw = ""
        header = self.headers.get("Authorization") or ""
        if header.startswith("Basic "):
            try:
                raw = base64.b64decode(header[6:]).strip().decode("utf-8", "replace")
            except Exception:
                raw = ""
        if hmac.compare_digest(raw.partition(":")[2], APP_PASSWORD):
            with _auth_lock:
                _auth_fails.pop(ip, None)
            return True

        with _auth_lock:
            fails = _auth_fails.setdefault(ip, [])
            fails.append(now)
            fails[:] = [t for t in fails if now - t <= AUTH_FAIL_WINDOW]
            if len(fails) >= AUTH_FAIL_LIMIT:
                _auth_blocked_until[ip] = now + AUTH_BLOCK_TIME
                _auth_fails.pop(ip, None)
        time.sleep(FAILED_AUTH_DELAY)
        self._send(401, "text/plain; charset=utf-8",
                   "需要登入 / Authentication required\n".encode("utf-8"),
                   {"WWW-Authenticate": f'Basic realm="{APP_REALM}", charset="UTF-8"'})
        return False

    # ------------------------------------------------------------------ #
    def do_GET(self):
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", INDEX.encode("utf-8"))
        if path == "/api/schema":
            return self._json(200, sc.field_specs_public())
        if path == "/api/health":
            return self._json(200, {"ok": True, "engine_version": C.ENGINE_VERSION,
                                    "schema_version": C.SCHEMA_VERSION,
                                    "llm_available": llm_available(),
                                    "deterministic": True})
        if path == "/favicon.ico":
            return self._send(204, "image/x-icon", b"")
        return self._send(404, "text/plain; charset=utf-8", b"not found")

    # ------------------------------------------------------------------ #
    def do_POST(self):
        if not self._authorized():
            return
        if urlparse(self.path).path != "/api/analyse":
            return self._json(404, {"ok": False,
                                    "errors": [{"field": "path",
                                                "message": "not found"}]})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._json(400, {"ok": False,
                                    "errors": [{"field": "body", "code": "size",
                                                "message": "請求內容為空或過大。"}]})
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self._json(400, {"ok": False,
                                    "errors": [{"field": "body", "code": "json",
                                                "message": f"JSON 解析失敗：{e}"}]})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False,
                                    "errors": [{"field": "body", "code": "type",
                                                "message": "請求內容必須是 JSON 物件。"}]})
        try:
            rpt = _run_analysis(body)
        except Exception as e:
            return self._json(500, {"ok": False,
                                    "errors": [{"field": "server", "code": "internal",
                                                "message": f"伺服器錯誤：{e}"}]})

        if not rpt.get("ok"):
            # validation failure -> field-level errors for inline display
            return self._json(400, {
                "ok": False,
                "errors": rpt.get("errors") or [],
                "warnings": rpt.get("warnings") or [],
                "pipeline_log": rpt.get("pipeline_log") or [],
                "engine_version": C.ENGINE_VERSION,
            })

        try:
            html = render(rpt)
        except Exception as e:                      # pragma: no cover - defensive
            traceback.print_exc()
            return self._json(500, {"ok": False,
                                    "errors": [{"field": "report", "code": "render",
                                                "message": f"報告產生失敗：{e}"}]})

        self._json(200, {
            "ok": True,
            "verdict": rpt["verdict"]["label"],
            "verdict_zh": rpt["verdict"].get("label_zh", ""),
            "verdict_code": rpt["verdict"]["code"],
            "why": rpt["verdict"]["why"],
            "summary": rpt.get("summary"),
            "metrics": rpt.get("metrics"),
            "caveats": rpt.get("caveats") or [],
            "valuation_unverified": bool((rpt.get("verdict") or {})
                                         .get("valuation_unverified")),
            "risks": [{"id": r["id"], "label": r["label"], "level": r["level"]}
                      for r in rpt.get("risks", [])],
            "risk_level": (rpt.get("risk") or {}).get("level"),
            "scenarios": rpt.get("scenarios"),
            "transaction_boundary": rpt.get("transaction_boundary"),
            "valuation": {k: v for k, v in (rpt.get("valuation") or {}).items()
                          if k != "evidence_entries"},
            "errors": [],
            "warnings": rpt.get("warnings") or [],
            "data_quality": rpt.get("data_quality"),
            "official_data_status": rpt.get("official_data_status") or {},
            "official_context": [
                {"title": e.get("title"), "source": e.get("source"),
                 "publisher": e.get("publisher"), "date": e.get("date"),
                 "metric": (e.get("data") or {}).get("metric"),
                 "value": (e.get("data") or {}).get("value"),
                 "unit": (e.get("data") or {}).get("unit")}
                for e in (rpt.get("official_context") or [])],
            "pipeline_log": rpt.get("pipeline_log") or [],
            "llm_available": llm_available(),
            "llm_narrative": bool(rpt.get("llm_narrative")),
            "llm_narrative_error": rpt.get("llm_narrative_error"),
            "engine_version": C.ENGINE_VERSION,
            "html": html,
        })


def _make_server(family, address, v6only=None):
    class _Server(ThreadingHTTPServer):
        address_family = family
        daemon_threads = True
        # On Windows SO_REUSEADDR lets a SECOND process bind a port that is
        # already being listened on: the stale instance keeps answering (with
        # its own password) while the new one looks healthy. Refusing to bind
        # is loud and fixable; a silently hijacked port is not.
        allow_reuse_address = os.name != "nt"

        def server_bind(self):
            if v6only is not None:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
            ThreadingHTTPServer.server_bind(self)
    return _Server(address, H)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    scope = (sys.argv[2] if len(sys.argv) > 2
             else os.environ.get("ANALYST_BIND", "local")).lower()
    shared = scope in ("lan", "all", "shared", "0.0.0.0", "any")
    if shared:
        # every interface: reachable from other devices on the same network
        addrs = ((socket.AF_INET, "0.0.0.0", None),
                 (socket.AF_INET6, "::", 1))       # v6only=1 so the two do not clash
    else:
        addrs = ((socket.AF_INET, "127.0.0.1", None),
                 (socket.AF_INET6, "::1", None))

    servers = []
    for family, host, v6only in addrs:
        try:
            servers.append(_make_server(family, (host, port), v6only))
        except OSError as e:
            print(f"  ! cannot bind {host}:{port} — {e}", flush=True)
    if not servers:
        print(f"port {port} is unavailable. Try:  python analyst_server.py 8765", flush=True)
        raise SystemExit(1)

    shown = "" if port == 80 else f":{port}"
    print(f"{APP_TITLE}", flush=True)
    if shared:
        ip = _lan_ip()
        print(f"  同一個網絡的人用這個:  http://{ip or '<你的區網IP>'}{shown}/", flush=True)
        print("     (其他裝置要連同一個 Wi-Fi／網絡)", flush=True)
    print(f"  http://{APP_HOSTNAME}.localhost{shown}/     <- 你自己用這個", flush=True)
    if APP_PASSWORD:
        print(f"  登入: 帳號任意 · 密碼 {APP_PASSWORD}"
              f"{'   <-- 本次隨機產生，重開會更換；設定 ANALYST_PASSWORD 可固定' if APP_PASSWORD_GENERATED else ''}",
              flush=True)
    else:
        print("  登入: 已停用（任何人都可以進入）", flush=True)
    print(f"  engine {C.ENGINE_VERSION} · schema {C.SCHEMA_VERSION} · "
          f"AI narrative {'available' if llm_available() else 'unavailable (deterministic only)'}",
          flush=True)

    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            srv.server_close()


if __name__ == "__main__":
    main()
