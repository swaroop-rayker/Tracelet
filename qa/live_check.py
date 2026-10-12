"""Security headers and CSP, verified by test against a deployed URL (F13.AC1, F13.AC2).

    ./scripts/tl live-check https://tracelet.duckdns.org

No sign-in and no visit: public surfaces only. An unknown slug is answered with the capture
surface's 404 and records nothing (F1.AC4, F2.AC14). Chromium, Firefox and WebKit load the
login page, the privacy page and that 404 with a CSP violation listener installed before
any page script runs: a natural load must report none, and positive controls (inline style,
style attribute, inline script, a third-party image) must each be blocked and reported.

`eval` is not exercised: Playwright's evaluate runs as debugger code, which every engine
exempts from CSP. The header checks assert that no policy carries 'unsafe-eval'.

On a workstation whose antivirus re-signs HTTPS (api/certs/README.md, ERRORS E21) the task
runner sets TRACELET_LIVE_INSECURE=1: certificate errors are then ignored and the report
says the certificate was NOT verified. Check it from elsewhere (M9 used the VM).
First written for M9 (MILESTONES), where it found ERRORS E81.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import APIRequestContext, sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TRACELET_LIVE_URL", "")).rstrip("/")
HOST = urlsplit(BASE).netloc
INSECURE = os.environ.get("TRACELET_LIVE_INSECURE") == "1"
OUT = Path("/qa/out")
SPA_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
BASELINE = {
    "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "cross-origin-opener-policy": "same-origin",
    "x-frame-options": "DENY",
    "permissions-policy": (
        "geolocation=(self), camera=(), microphone=(), payment=(), usb=(), "
        "magnetometer=(), gyroscope=()"
    ),
}
INIT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push({directive: e.violatedDirective, blocked: e.blockedURI,
                     page: location.pathname});
});
"""
CONTROLS = """
async () => {
  const before = window.__csp.length;
  const s = document.createElement('style'); s.textContent = 'body{outline:1px solid red}';
  document.head.appendChild(s);
  document.body.setAttribute('style', 'outline: 2px solid blue');
  const sc = document.createElement('script'); sc.textContent = 'window.__ran = 1';
  document.head.appendChild(sc);
  let evalBlocked = false;
  try { (0, eval)('1'); } catch (e) { evalBlocked = true; }
  const img = document.createElement('img'); img.src = 'https://example.com/qa-m9.png';
  document.body.appendChild(img);
  await new Promise((r) => setTimeout(r, 1500));
  return {fresh: window.__csp.slice(before), inlineRan: window.__ran === 1, evalBlocked};
}
"""

# The 404 an unknown slug gets is a capture-surface page too, and records nothing.
PAGES = ("/login", "/privacy", f"/r/qa-m9-browser-{secrets.token_hex(4)}")

failures: list[str] = []
report: dict[str, Any] = {"http": {}, "engines": {}}


def check(cond: bool, what: str) -> None:
    if not cond:
        failures.append(what)


def csp_is_strict(csp: str, where: str) -> None:
    directives = {d.strip().split(" ", 1)[0]: d.strip() for d in csp.split(";") if d.strip()}
    for name in ("script-src", "style-src", "default-src"):
        value = directives.get(name, "")
        check("'unsafe-inline'" not in value, f"{where}: {name} allows 'unsafe-inline'")
        check("'unsafe-eval'" not in value, f"{where}: {name} allows 'unsafe-eval'")
    check(
        directives.get("frame-ancestors") == "frame-ancestors 'none'",
        f"{where}: frame-ancestors is not 'none'",
    )
    check(
        directives.get("object-src") == "object-src 'none'"
        or directives.get("default-src") == "default-src 'none'",
        f"{where}: plugins are not blocked",
    )


def baseline(headers: dict[str, str], where: str) -> None:
    for name, value in BASELINE.items():
        check(headers.get(name) == value, f"{where}: {name} = {headers.get(name)!r}")
    check("server" not in headers, f"{where}: Server header present: {headers.get('server')}")
    check("x-powered-by" not in headers, f"{where}: X-Powered-By present")


def fetch(api: APIRequestContext, path: str, **kw: Any) -> tuple[int, dict[str, str], str]:
    response = api.get(BASE + path, max_redirects=0, **kw)
    headers = {k.lower(): v for k, v in response.headers.items()}
    body = response.text()
    report["http"][path] = {"status": response.status, "headers": headers}
    return response.status, headers, body


def http_checks(api: APIRequestContext) -> None:
    status, h, body = fetch(api, "/")
    check(status == 200, f"/: status {status}")
    check(
        h.get("content-security-policy") == SPA_CSP,
        f"/: CSP differs: {h.get('content-security-policy')}",
    )
    check(h.get("cache-control") == "no-cache", "/: index.html must not be cached")
    baseline(h, "/")
    csp_is_strict(h.get("content-security-policy", ""), "/")
    asset = re.search(r'src="(/assets/[^"]+\.js)"', body)
    check(asset is not None, "/: no hashed script in index.html")
    if asset:
        status, h, _ = fetch(api, asset.group(1))
        check(status == 200, f"asset: status {status}")
        check(
            h.get("cache-control") == "public, max-age=31536000, immutable",
            f"asset: cache-control {h.get('cache-control')}",
        )
        baseline(h, "asset")

    status, h, _ = fetch(api, "/login")
    check(status == 200 and h.get("content-security-policy") == SPA_CSP, "/login: SPA CSP")
    baseline(h, "/login")

    nonces = []
    for _ in range(2):
        status, h, _ = fetch(api, "/privacy")
        check(status == 200, f"/privacy: status {status}")
        csp = h.get("content-security-policy", "")
        csp_is_strict(csp, "/privacy")
        baseline(h, "/privacy")
        nonces.append(re.findall(r"'nonce-([^']+)'", csp))
    check(bool(nonces[0]) and nonces[0] != nonces[1], "/privacy: nonce missing or reused")

    slug = f"/r/qa-m9-{secrets.token_hex(6)}"
    status, h, body = fetch(api, slug)
    check(status == 404, f"unknown slug: status {status}, must be 404")
    check("location" not in h, "unknown slug: must not redirect")
    csp_is_strict(h.get("content-security-policy", ""), "unknown slug")
    baseline(h, "unknown slug")
    check("qa-m9" not in body, "unknown slug: the 404 echoes the slug")
    report["unknown_slug"] = slug

    for path, code in (("/api/v1/auth/me", 401), ("/api/v1/qa-m9-nowhere", 404)):
        status, h, body = fetch(api, path)
        check(status == code, f"{path}: status {status}, want {code}")
        check(
            h.get("content-type", "").startswith("application/problem+json"),
            f"{path}: not Problem Details",
        )
        try:
            problem = json.loads(body)
        except ValueError:
            problem = {}
        check(
            re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", str(problem.get("trace_id", ""))) is not None,
            f"{path}: no ULID trace_id",
        )
        check(
            "Traceback" not in body and "sqlalchemy" not in body.lower(),
            f"{path}: internal detail leaked",
        )
        baseline(h, path)

    status, h, _ = fetch(api, "/healthz")
    check(status == 200, f"/healthz: {status}")

    plain = api.get(f"http://{HOST}/login", max_redirects=0)
    location = plain.headers.get("location", "")
    check(
        plain.status in (301, 308) and location == f"https://{HOST}/login",
        f"http: {plain.status} -> {location}",
    )


def browser_checks(pw: Any, engine: str) -> None:
    out: dict[str, Any] = {}
    browser = getattr(pw, engine).launch()
    ctx = browser.new_context(ignore_https_errors=INSECURE, viewport={"width": 1280, "height": 900})
    ctx.add_init_script(INIT)
    page = ctx.new_page()
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    for path in PAGES:
        page.goto(BASE + path, wait_until="domcontentloaded")
        page.wait_for_selector("body *", timeout=20_000)
        page.wait_for_timeout(1500)
        natural = page.evaluate("window.__csp")
        controls = page.evaluate(CONTROLS)
        directives = sorted({v["directive"].split(" ")[0] for v in controls["fresh"]})
        out[path] = {
            "natural": natural,
            "controls": directives,
            "inline_ran": controls["inlineRan"],
            "eval_blocked": controls["evalBlocked"],
        }
        check(natural == [], f"{engine} {path}: CSP violations on a natural load: {natural}")
        check(not controls["inlineRan"], f"{engine} {path}: an injected inline script RAN")
        # Not asserted: page.evaluate runs as debugger code ("debugger eval code"), which
        # every engine exempts from CSP, so eval succeeds here whatever the policy says. The
        # guarantee is the header check: no policy carries 'unsafe-eval' (csp_is_strict).
        check(
            any(d.startswith("style-src") for d in directives),
            f"{engine} {path}: inline style not reported",
        )
        check(
            any(d.startswith("script-src") for d in directives),
            f"{engine} {path}: inline script not reported",
        )
        check(
            any(d.startswith(("img-src", "default-src")) for d in directives),
            f"{engine} {path}: third-party image not reported",
        )
        # The screenshot comes after the readings: WebKit's own capture can trip style-src-elem.
        page.screenshot(path=str(OUT / f"{engine}{path.replace('/', '_')}.png"))
    out["console_errors"] = console_errors
    report["engines"][engine] = out
    browser.close()


def main() -> int:
    if not BASE.startswith("https://"):
        print("usage: live_check.py https://<site>   (or TRACELET_LIVE_URL)")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    engines = (os.environ.get("TRACELET_LIVE_ENGINES") or "chromium,firefox,webkit").split(",")
    report["base"] = BASE
    report["certificate_verified"] = not INSECURE
    with sync_playwright() as pw:
        api = pw.request.new_context(ignore_https_errors=INSECURE)
        http_checks(api)
        api.dispose()
        for engine in engines:
            browser_checks(pw, engine)
    report["failures"] = failures
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    for engine, data in report["engines"].items():
        for path in PAGES:
            d = data[path]
            print(
                f"{engine:9} {path:9} natural={len(d['natural'])} controls={','.join(d['controls'])} "
                f"inline_ran={d['inline_ran']} eval_blocked={d['eval_blocked']}"
            )
        if data["console_errors"]:
            print(f"{engine:9} console errors: {data['console_errors'][:3]}")
    print(f"unknown slug probed: {report.get('unknown_slug')}")
    if INSECURE:
        print("NOTE: certificate NOT verified (TLS re-signed on this machine); check it elsewhere.")
    print("FAILURES:" if failures else "ALL CHECKS PASSED", *failures, sep="\n  ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
