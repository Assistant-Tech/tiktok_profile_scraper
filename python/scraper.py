#!/usr/bin/env python3
"""TikTok profile scraper. HTTP-first with Selenium fallback. JSON to stdout."""
import argparse
import json
import re
import sys
import time
from typing import Any

import requests

REHYDRATE_RE = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)</script>',
    re.DOTALL,
)
WAF_MARKERS = ("_wafchallengeid", "captcha-verify", "tiktok-verify")
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_count(raw: str | None) -> int | None:
    if not raw:
        return None
    s = raw.strip().upper().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMB])?$", s)
    if not m:
        try:
            return int(s)
        except ValueError:
            return None
    num = float(m.group(1))
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(m.group(2) or "", 1)
    return int(round(num * mult))


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


def is_waf_html(html: str) -> bool:
    return any(m in html for m in WAF_MARKERS)


def scrape_http(username: str, timeout: int, user_agent: str) -> dict:
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    if r.status_code == 404:
        return {"error": {"code": "PROFILE_NOT_FOUND", "message": f"@{username} 404"}}
    if r.status_code in (403, 429) or is_waf_html(r.text):
        return {"error": {"code": "WAF_BLOCKED", "message": f"WAF status={r.status_code}"}}
    if r.status_code >= 400:
        return {
            "error": {"code": "SCRAPE_ERROR", "message": f"http {r.status_code}"}
        }

    m = REHYDRATE_RE.search(r.text)
    if not m:
        return {
            "error": {
                "code": "PROFILE_NOT_FOUND",
                "message": "rehydration script absent",
            }
        }
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return {"error": {"code": "SCRAPE_ERROR", "message": f"bad json: {e}"}}

    detail = payload.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    if detail.get("statusCode") not in (None, 0):
        return {
            "error": {"code": "PROFILE_NOT_FOUND", "message": f"statusCode={detail.get('statusCode')}"}
        }

    profile = extract_profile(payload, username)
    if not profile:
        return {"error": {"code": "PROFILE_NOT_FOUND", "message": "no userInfo"}}
    return {"profile": profile}


def scrape_selenium(
    username: str, timeout: int, executable_path: str | None, user_agent: str
) -> dict:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_argument("--window-size=1920,1080")
    o.add_argument("--log-level=3")
    o.page_load_strategy = "eager"
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    o.add_argument(f"user-agent={user_agent}")
    if executable_path:
        o.binary_location = executable_path

    d = webdriver.Chrome(service=Service(), options=o)
    try:
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
        except WebDriverException:
            pass

        d.set_page_load_timeout(timeout)
        try:
            d.get(f"https://www.tiktok.com/@{username}")
        except TimeoutException:
            pass

        try:
            WebDriverWait(d, timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "script#__UNIVERSAL_DATA_FOR_REHYDRATION__")
                )
            )
        except TimeoutException:
            return {"error": {"code": "PROFILE_NOT_FOUND", "message": "no rehydrate"}}

        if is_waf_html(d.page_source):
            return {"error": {"code": "WAF_BLOCKED", "message": "WAF in page"}}

        script_text = d.execute_script(
            "var el=document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
            "return el?el.textContent:null;"
        )
        if not script_text:
            return {"error": {"code": "PROFILE_NOT_FOUND", "message": "empty rehydrate"}}
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError as e:
            return {"error": {"code": "SCRAPE_ERROR", "message": f"bad json: {e}"}}

        profile = extract_profile(payload, username)
        if not profile:
            return {"error": {"code": "PROFILE_NOT_FOUND", "message": "no userInfo"}}
        return {"profile": profile}
    finally:
        try:
            d.quit()
        except Exception:
            pass


def scrape(
    username: str,
    timeout: int,
    executable_path: str | None,
    user_agent: str,
    mode: str,
) -> dict:
    if mode == "selenium":
        return scrape_selenium(username, timeout, executable_path, user_agent)

    result = scrape_http(username, timeout, user_agent)
    if "profile" in result:
        result["mode"] = "http"
        return result

    if mode == "http":
        return result

    code = result.get("error", {}).get("code")
    if code in ("WAF_BLOCKED", "PROFILE_NOT_FOUND", "SCRAPE_ERROR"):
        sel = scrape_selenium(username, timeout, executable_path, user_agent)
        if "profile" in sel:
            sel["mode"] = "selenium"
            return sel
        sel["mode"] = "selenium"
        return sel
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("username")
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--executable-path", default=None)
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument(
        "--mode",
        choices=["auto", "http", "selenium"],
        default="auto",
        help="auto: HTTP first, Selenium on failure",
    )
    args = p.parse_args()

    t0 = time.time()
    try:
        result = scrape(
            args.username,
            timeout=args.timeout,
            executable_path=args.executable_path,
            user_agent=args.user_agent,
            mode=args.mode,
        )
    except Exception as e:
        result = {"error": {"code": "SCRAPE_ERROR", "message": str(e)}}
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
