# -*- coding: utf-8 -*-
"""Optional LLM client. Key resolution order:
   1) env DEEPSEEK_API_KEY
   2) DSH credential store refs.DEEPSEEK_API_KEY (~/.dsh/.credentials.yaml)
The key is never printed/logged. Absent key -> pipeline stays rule-based."""
import json, os, time, urllib.error, urllib.request


def _get_key() -> str | None:
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    try:
        import yaml
        path = os.path.join(os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh")),
                            ".credentials.yaml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        refs = data.get("refs") or {}
        k = refs.get("DEEPSEEK_API_KEY")
        return k if isinstance(k, str) and k else None
    except Exception:
        return None


def llm_available() -> bool:
    return bool(_get_key())


def chat_json_ex(system: str, user: str, max_tokens: int = 1200,
                 attempts: int = 2) -> tuple[dict | None, str | None]:
    """(parsed_json, error). Same best-effort contract as chat_json, but it says
    WHY it failed.

    The old version returned a bare None for every failure mode, so a 429, a
    timeout or a truncated reply were all reported downstream as "no API key" —
    telling the user to go configure a key that was already configured.
    Retried once: these failures are usually transient.
    """
    key = _get_key()
    if not key:
        return None, "no_api_key"
    last = "unknown"
    for i in range(max(1, attempts)):
        try:
            body = json.dumps({
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode("utf-8"))
            txt = data["choices"][0]["message"]["content"]
            return json.loads(txt), None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403):
                return None, "HTTP %d（API key 被拒）" % e.code   # retrying cannot help
            if e.code == 402:
                return None, "HTTP 402（額度不足）"
        except json.JSONDecodeError as e:
            last = f"回應不是合法 JSON（{e.msg}）"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i + 1 < attempts:
            time.sleep(1.2)
    return None, last


def chat_json(system: str, user: str, max_tokens: int = 1200) -> dict | None:
    """Back-compat wrapper: the parsed object only (None on any failure)."""
    return chat_json_ex(system, user, max_tokens)[0]
