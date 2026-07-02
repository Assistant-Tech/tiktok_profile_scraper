"""Shared parsing/classification for TikTok profile rehydration payloads.

Used by both scraper.py (one-shot CLI) and worker.py (persistent worker).
"""
from typing import Any

# webapp.user-detail statusCode values observed in live responses (2026-07)
STATUS_OK = 0
STATUS_BANNED = 10202  # deleted/banned: "couldn't find this account"
STATUS_NOT_FOUND = 10221  # account does not exist
STATUS_PRIVATE = 10222  # ErrBizUserSecret: private account, userInfo still present
STATUS_RESTRICTED = 209002  # audience controls: login required, no data for guests


def to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_profile(user_info: dict) -> dict | None:
    user = user_info.get("user") or {}
    stats = user_info.get("stats") or user_info.get("statsV2") or {}
    unique_id = user.get("uniqueId")
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
        "private": bool(user.get("privateAccount") or user.get("secret")),
        "follower_count": to_int(stats.get("followerCount")),
        "following_count": to_int(stats.get("followingCount")),
        "like_count": to_int(stats.get("heartCount") or stats.get("heart")),
        "region": user.get("region"),
    }


def classify_user_detail(payload: dict, username: str) -> dict:
    """Turn a parsed __UNIVERSAL_DATA_FOR_REHYDRATION__ payload into
    {"profile": ...} or {"error": {"code", "message"}}."""
    detail = payload.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    raw_sc = detail.get("statusCode")
    try:
        sc = STATUS_OK if raw_sc is None else int(raw_sc)
    except (TypeError, ValueError):
        sc = -1

    # Private accounts (10222) still ship full userInfo; trust data over code.
    profile = extract_profile(detail.get("userInfo") or {})
    if profile:
        return {"profile": profile}

    if sc in (STATUS_NOT_FOUND, STATUS_BANNED):
        return {
            "error": {
                "code": "PROFILE_NOT_FOUND",
                "message": f"@{username} statusCode={sc}",
            }
        }
    if sc == STATUS_RESTRICTED:
        return {
            "error": {
                "code": "PROFILE_RESTRICTED",
                "message": f"@{username} requires login (audience controls)",
            }
        }
    return {
        "error": {
            "code": "SCRAPE_ERROR",
            "message": f"statusCode={raw_sc!r} without userInfo",
        }
    }
