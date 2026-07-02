"""Unit tests for python/profile_parse.py classification logic.

Payload shapes captured from live TikTok responses (2026-07):
  - statusCode 0       -> public profile, full userInfo
  - statusCode 10222   -> private account (ErrBizUserSecret), userInfo present
  - statusCode 209002  -> audience controls, login required, no userInfo
  - statusCode 10221   -> account does not exist
  - statusCode 10202   -> account banned/deleted ("couldn't find this account")
"""
import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python")
)

from profile_parse import classify_user_detail, extract_profile  # noqa: E402


def wrap(detail: dict) -> dict:
    return {"__DEFAULT_SCOPE__": {"webapp.user-detail": detail}}


PUBLIC_USER_INFO = {
    "user": {
        "uniqueId": "tiktok",
        "nickname": "TikTok",
        "signature": "One TikTok can make a big impact",
        "verified": True,
        "avatarLarger": "https://example.com/a.jpeg",
        "privateAccount": False,
        "secret": False,
        "region": "US",
    },
    "stats": {"followerCount": 94700000, "followingCount": 1, "heartCount": 461200000},
}

PRIVATE_USER_INFO = {
    "user": {
        "uniqueId": "sani__6349",
        "nickname": "sani__6",
        "signature": "",
        "verified": False,
        "avatarLarger": "https://example.com/b.jpeg",
        "privateAccount": True,
        "secret": True,
        "region": None,
    },
    "statsV2": {"followerCount": "8", "followingCount": "15", "heartCount": "16"},
}


class TestClassifyUserDetail(unittest.TestCase):
    def test_public_profile_ok(self):
        r = classify_user_detail(
            wrap({"statusCode": 0, "userInfo": PUBLIC_USER_INFO}), "tiktok"
        )
        self.assertIn("profile", r)
        p = r["profile"]
        self.assertEqual(p["username"], "tiktok")
        self.assertEqual(p["follower_count"], 94700000)
        self.assertFalse(p["private"])

    def test_status_code_as_string_zero(self):
        r = classify_user_detail(
            wrap({"statusCode": "0", "userInfo": PUBLIC_USER_INFO}), "tiktok"
        )
        self.assertIn("profile", r)

    def test_private_account_returns_profile(self):
        r = classify_user_detail(
            wrap(
                {
                    "statusCode": 10222,
                    "statusMsg": "ErrBizUserSecret",
                    "userInfo": PRIVATE_USER_INFO,
                }
            ),
            "sani__6349",
        )
        self.assertIn("profile", r)
        p = r["profile"]
        self.assertEqual(p["username"], "sani__6349")
        self.assertEqual(p["follower_count"], 8)  # statsV2 strings coerced
        self.assertTrue(p["private"])

    def test_restricted_account(self):
        r = classify_user_detail(wrap({"statusCode": 209002, "statusMsg": ""}), "x")
        self.assertEqual(r["error"]["code"], "PROFILE_RESTRICTED")

    def test_not_found(self):
        r = classify_user_detail(wrap({"statusCode": 10221}), "x")
        self.assertEqual(r["error"]["code"], "PROFILE_NOT_FOUND")

    def test_banned(self):
        r = classify_user_detail(wrap({"statusCode": 10202}), "x")
        self.assertEqual(r["error"]["code"], "PROFILE_NOT_FOUND")

    def test_unknown_status_code_is_scrape_error_not_not_found(self):
        r = classify_user_detail(wrap({"statusCode": 12345}), "x")
        self.assertEqual(r["error"]["code"], "SCRAPE_ERROR")

    def test_ok_status_without_user_info_is_scrape_error(self):
        # SSR shell without data must not fabricate a garbage profile
        r = classify_user_detail(wrap({"statusCode": 0}), "x")
        self.assertEqual(r["error"]["code"], "SCRAPE_ERROR")

    def test_missing_scope_is_scrape_error(self):
        r = classify_user_detail({}, "x")
        self.assertEqual(r["error"]["code"], "SCRAPE_ERROR")


class TestExtractProfile(unittest.TestCase):
    def test_requires_unique_id(self):
        self.assertIsNone(extract_profile({}))
        self.assertIsNone(extract_profile({"user": {"nickname": "no-id"}}))

    def test_full_extraction(self):
        p = extract_profile(PUBLIC_USER_INFO)
        self.assertEqual(
            p,
            {
                "username": "tiktok",
                "name": "TikTok",
                "avatar_url": "https://example.com/a.jpeg",
                "bio": "One TikTok can make a big impact",
                "verified": True,
                "private": False,
                "follower_count": 94700000,
                "following_count": 1,
                "like_count": 461200000,
                "region": "US",
            },
        )


if __name__ == "__main__":
    unittest.main()
