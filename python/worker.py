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
from typing import Any

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


def to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_profile(payload: dict, fallback_username: str) -> dict | None:
    detail = payload.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    user_info = detail.get("userInfo") or {}
    user = user_info.get("user") or {}
    stats = user_info.get("stats") or user_info.get("statsV2") or {}
    unique_id = user.get("uniqueId") or fallback_username
    if not unique_id:
        return None
    return {
        "username": unique_id,
        "name": (user.get("nickname") or unique_id).strip(),
        "avatar_url": user.get("avatarLarger")
        or user.get("avatarMedium")
        or user.get("avatarThumb"),
        "bio": (user.get("signature") or "").strip() or None,
        "verified": bool(user.get("verified")),
        "follower_count": to_int(stats.get("followerCount")),
        "following_count": to_int(stats.get("followingCount")),
        "like_count": to_int(stats.get("heartCount") or stats.get("heart")),
        "region": user.get("region"),
    }


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
        return {"error": {"code": "PROFILE_NOT_FOUND", "message": "no rehydrate script"}}

    if is_waf(driver.page_source):
        return {"error": {"code": "WAF_BLOCKED", "message": "WAF challenge"}}

    script_text = driver.execute_script(
        "var el=document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
        "return el?el.textContent:null;"
    )
    if not script_text:
        return {"error": {"code": "PROFILE_NOT_FOUND", "message": "empty rehydrate"}}
    try:
        payload = json.loads(script_text)
    except json.JSONDecodeError as e:
        return {"error": {"code": "SCRAPE_ERROR", "message": f"bad json: {e}"}}

    detail = payload.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    if detail.get("statusCode") not in (None, 0):
        return {
            "error": {
                "code": "PROFILE_NOT_FOUND",
                "message": f"statusCode={detail.get('statusCode')}",
            }
        }

    profile = extract_profile(payload, username)
    if not profile:
        return {"error": {"code": "PROFILE_NOT_FOUND", "message": "no userInfo"}}
    return {"profile": profile}


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
    args = p.parse_args()

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
        except WebDriverException as e:
            log(f"webdriver error: {e}; recreating driver")
            try:
                driver.quit()
            except Exception:
                pass
            driver = make_driver(args.executable_path, args.user_agent)
            result = {"error": {"code": "SCRAPE_ERROR", "message": f"webdriver crashed: {e}"}}

        if "error" in result and result["error"]["code"] in ("SCRAPE_ERROR", "WAF_BLOCKED"):
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
