#!/usr/bin/env python3
"""Long-lived TikTok scraper worker.

Protocol:
  stdin:  one JSON request per line: {"id": "<id>", "username": "<u>"}
  stdout: one JSON response per line: {"id": "<id>", "profile": {...}} or
                                      {"id": "<id>", "error": {"code","message"}}
  stderr: log lines (free-form)

Reuses single Selenium driver across requests. Exit on EOF.
"""
import json
import re
import sys
import time

from profile_parse import classify_user_detail
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

WAF_MARKERS = ("_wafchallengeid", "captcha-verify", "tiktok-verify")
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,32}$")


def log(msg: str) -> None:
    sys.stderr.write(f"[worker] {msg}\n")
    sys.stderr.flush()


def make_driver(executable_path: str | None, user_agent: str) -> webdriver.Chrome:
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_argument("--window-size=1280,800")
    o.add_argument("--log-level=3")
    o.page_load_strategy = "eager"
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    o.add_argument(f"user-agent={user_agent}")
    if executable_path:
        o.binary_location = executable_path

    d = webdriver.Chrome(service=Service(), options=o)
    try:
        d.execute_cdp_cmd("Network.enable", {})
        d.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {
                "urls": [
                    "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif",
                    "*.mp4", "*.m4s", "*.webm",
                    "*.woff", "*.woff2", "*.ttf",
                ]
            },
        )
    except WebDriverException as e:
        log(f"CDP block setup failed: {e}")
    return d


def is_waf(html: str) -> bool:
    return any(m in html for m in WAF_MARKERS)


def read_cookies(cookies_path: str | None) -> list[dict] | None:
    """Read exported browser cookies (Cookie-Editor JSON format) from disk.
    They are injected on demand, never at boot — scraping stays anonymous
    except for the authenticated retry of restricted profiles."""
    if not cookies_path:
        return None
    try:
        with open(cookies_path, encoding="utf-8") as f:
            cookies = json.load(f)
        log(f"read {len(cookies)} cookies from {cookies_path}")
        return cookies
    except (OSError, ValueError) as e:
        log(f"cookie read failed ({e}); authenticated retries disabled")
        return None


def apply_cookies(driver: webdriver.Chrome, cookies: list[dict]) -> None:
    """Inject cookies into the current session. The driver must already be
    on a tiktok.com page (add_cookie is domain-scoped)."""
    loaded = 0
    for c in cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".tiktok.com"),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", True)),
        }
        if c.get("expirationDate"):
            cookie["expiry"] = int(c["expirationDate"])
        try:
            driver.add_cookie(cookie)
            loaded += 1
        except WebDriverException as e:
            log(f"cookie {c.get('name')!r} rejected: {e}")
    log(f"applied {loaded}/{len(cookies)} cookies")


def clear_session(driver: webdriver.Chrome) -> None:
    try:
        driver.delete_all_cookies()
    except WebDriverException as e:
        log(f"cookie clear failed: {e}")


def scrape_once(driver: webdriver.Chrome, username: str, timeout: int) -> dict:
    if not USERNAME_RE.match(username):
        return {"error": {"code": "SCRAPE_ERROR", "message": "invalid username"}}

    url = f"https://www.tiktok.com/@{username}"
    driver.set_page_load_timeout(timeout)
    try:
        driver.get(url)
    except TimeoutException:
        pass

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "script#__UNIVERSAL_DATA_FOR_REHYDRATION__")
            )
        )
    except TimeoutException:
        if is_waf(driver.page_source):
            return {"error": {"code": "WAF_BLOCKED", "message": "WAF challenge"}}
        # Page never rendered its data script: transient/blocked, NOT proof
        # the account is missing (real not-found pages do render it).
        return {"error": {"code": "TIMEOUT", "message": "no rehydrate script"}}

    if is_waf(driver.page_source):
        return {"error": {"code": "WAF_BLOCKED", "message": "WAF challenge"}}

    script_text = driver.execute_script(
        "var el=document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
        "return el?el.textContent:null;"
    )
    if not script_text:
        return {"error": {"code": "SCRAPE_ERROR", "message": "empty rehydrate"}}
    try:
        payload = json.loads(script_text)
    except json.JSONDecodeError as e:
        return {"error": {"code": "SCRAPE_ERROR", "message": f"bad json: {e}"}}

    return classify_user_detail(payload, username)


def respond(req_id: str, body: dict, t0: float) -> None:
    body["id"] = req_id
    body["elapsed_ms"] = int((time.time() - t0) * 1000)
    sys.stdout.write(json.dumps(body) + "\n")
    sys.stdout.flush()


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--executable-path", default=None)
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument(
        "--cookies-path",
        default=None,
        help="JSON file of exported tiktok.com cookies, used only to retry "
        "restricted (audience-controlled) profiles with an authenticated session",
    )
    args = p.parse_args()

    cookies = read_cookies(args.cookies_path)
    log("booting driver")
    driver = make_driver(args.executable_path, args.user_agent)
    log("ready")
    sys.stdout.write(json.dumps({"event": "ready"}) + "\n")
    sys.stdout.flush()

    consecutive_failures = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        t0 = time.time()
        try:
            req = json.loads(line)
            req_id = str(req.get("id", ""))
            username = str(req.get("username", "")).strip()
        except json.JSONDecodeError as e:
            sys.stdout.write(
                json.dumps({"id": "", "error": {"code": "SCRAPE_ERROR", "message": f"bad request: {e}"}}) + "\n"
            )
            sys.stdout.flush()
            continue

        try:
            result = scrape_once(driver, username, args.timeout)
            if cookies and result.get("error", {}).get("code") == "PROFILE_RESTRICTED":
                # Restricted profiles are invisible to guests; retry once with
                # the authenticated session, then drop back to anonymous so
                # regular traffic never burns the logged-in account.
                log(f"@{username} restricted; retrying authenticated")
                try:
                    apply_cookies(driver, cookies)
                    auth_result = scrape_once(driver, username, args.timeout)
                    if "profile" in auth_result:
                        result = auth_result
                finally:
                    clear_session(driver)
        except WebDriverException as e:
            log(f"webdriver error: {e}; recreating driver")
            try:
                driver.quit()
            except Exception:
                pass
            driver = make_driver(args.executable_path, args.user_agent)
            result = {"error": {"code": "SCRAPE_ERROR", "message": f"webdriver crashed: {e}"}}

        if "error" in result and result["error"]["code"] in ("SCRAPE_ERROR", "WAF_BLOCKED", "TIMEOUT"):
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        if consecutive_failures >= 5:
            log("5 consecutive failures; recycling driver")
            try:
                driver.quit()
            except Exception:
                pass
            driver = make_driver(args.executable_path, args.user_agent)
            consecutive_failures = 0

        respond(req_id, result, t0)

    log("stdin closed; shutting down")
    try:
        driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
