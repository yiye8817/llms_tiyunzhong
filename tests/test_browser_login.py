"""Synthetic profiles only: never touch the test runner's browser credentials."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import browser_login as login


NOW = 2_000_000_000
FUTURE = NOW + 3600


def firefox_db(path, cookies):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE moz_cookies(host TEXT,name TEXT,value TEXT,path TEXT,expiry INTEGER,isSecure INTEGER,isHttpOnly INTEGER,sameSite INTEGER,isSession INTEGER,originAttributes TEXT,isPartitioned INTEGER)")
    for overrides in cookies:
        row = dict(host=".chatgpt.com", name="fixture", value="synthetic-cookie-value", path="/",
                   expiry=FUTURE, isSecure=1, isHttpOnly=1, sameSite=1, isSession=0,
                   originAttributes="", isPartitioned=0)
        row.update(overrides)
        connection.execute("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
    connection.commit()
    return connection


def chromium_db(path, cookies, version=24):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,path TEXT,expires_utc INTEGER,is_secure INTEGER,is_httponly INTEGER,samesite INTEGER,has_expires INTEGER,is_persistent INTEGER,top_frame_site_key TEXT)")
    connection.execute("CREATE TABLE meta(key TEXT,value TEXT)")
    connection.execute("INSERT INTO meta VALUES ('version', ?)", (str(version),))
    for overrides in cookies:
        row = dict(host_key=".chatgpt.com", name="fixture", value="synthetic-cookie-value", encrypted_value=b"", path="/",
                   expires_utc=(FUTURE + login.CHROMIUM_EPOCH) * 1_000_000, is_secure=1,
                   is_httponly=1, samesite=2, has_expires=1, is_persistent=1, top_frame_site_key="")
        row.update(overrides)
        connection.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
    connection.commit()
    return connection


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fusion-test-fake-home-")
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def profiles(self):
        return login.discover_profiles(self.home, self.home / ".config")[0]

    def extract(self, profile=None):
        profile = profile or self.profiles()[0]
        return login.extract(profile, "chatgpt", ["chatgpt.com", "openai.com"], now=NOW)

    def firefox(self, rows, relative=".mozilla/firefox/a.default/cookies.sqlite"):
        db = firefox_db(self.home / relative, rows)
        db.close()

    def chromium(self, rows, relative=".config/google-chrome/Default/Network/Cookies"):
        db = chromium_db(self.home / relative, rows)
        db.close()

    def test_discovery_reads_no_cookie_database_or_keyring(self):
        profile = self.home / ".config/google-chrome/Default"
        profile.mkdir(parents=True)
        (profile / "Cookies").write_bytes(b"this is deliberately not sqlite")
        with patch.object(login.sqlite3, "connect", side_effect=AssertionError("must not open cookies")), \
                patch.object(login.importlib, "import_module", side_effect=AssertionError("must not query keyring")):
            result = login.handle_request({"action": "list"}, home=self.home, config_home=self.home / ".config")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["profiles"]), 1)
        self.assertEqual(set(result["profiles"][0]), {"id", "browser", "family", "name", "path"})

    def test_firefox_profiles_ini_relative_absolute_and_stable_ids(self):
        root = self.home / ".mozilla/firefox"
        first, second = root / "a.default", self.home / "custom-profile"
        first.mkdir(parents=True)
        second.mkdir()
        (root / "profiles.ini").write_text(
            "[Profile0]\nName=工作 % 配置\nIsRelative=1\nPath=a.default\n"
            f"[Profile1]\nName=个人\nIsRelative=0\nPath={second}\n", encoding="utf-8")
        profiles = self.profiles()
        self.assertEqual({p["name"] for p in profiles}, {"工作 % 配置", "个人"})
        self.assertEqual([p["id"] for p in profiles], [p["id"] for p in self.profiles()])

    def test_common_native_snap_flatpak_and_multiple_profiles(self):
        paths = [
            ("Chrome", ".config/google-chrome/Default/Cookies"),
            ("Chrome", ".config/google-chrome/Profile 2/Network/Cookies"),
            ("Chromium", "snap/chromium/common/chromium/Default/Cookies"),
            ("Brave", ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Cookies"),
            ("Edge", ".config/microsoft-edge/Default/Cookies"),
            ("Vivaldi", ".config/vivaldi/Default/Cookies"),
            ("Opera", ".config/opera/Cookies"),
        ]
        for _, relative in paths:
            self.chromium([], relative)
        self.firefox([], "snap/firefox/common/.mozilla/firefox/a.default/cookies.sqlite")
        self.firefox([], ".var/app/org.mozilla.firefox/.mozilla/firefox/b.default/cookies.sqlite")
        profiles = self.profiles()
        self.assertEqual(len(profiles), 9)
        self.assertEqual({p["browser"] for p in profiles}, {"Chrome", "Chromium", "Brave", "Edge", "Vivaldi", "Opera", "Firefox"})

    def test_symlink_metadata_profiles_are_deduplicated(self):
        self.firefox([])
        real = self.home / ".mozilla/firefox"
        snap = self.home / "snap/firefox/common/.mozilla"
        snap.mkdir(parents=True)
        (snap / "firefox").symlink_to(real, target_is_directory=True)
        self.assertEqual(len(self.profiles()), 1)

    def test_firefox_scope_and_security_attributes(self):
        self.firefox([
            {"name": "domain", "sameSite": 2},
            {"name": "host", "host": "chatgpt.com", "isSession": 1, "expiry": 0},
            {"name": "subdomain", "host": "auth.openai.com", "sameSite": 0},
            {"name": "wrong", "host": "chatgpt.com.attacker.test"},
            {"name": "wrong2", "host": "evilchatgpt.com"},
            {"name": "other", "host": "google.com"},
        ])
        result = self.extract()
        self.assertTrue(result["ok"])
        cookies = {c["name"]: c for c in result["cookies"]}
        self.assertEqual(set(cookies), {"domain", "host", "subdomain"})
        self.assertFalse(cookies["domain"]["hostOnly"])
        self.assertTrue(cookies["domain"]["secure"])
        self.assertTrue(cookies["domain"]["httpOnly"])
        self.assertEqual(cookies["domain"]["sameSite"], "strict")
        self.assertEqual(cookies["domain"]["expirationDate"], FUTURE)
        self.assertTrue(cookies["host"]["session"])
        self.assertTrue(cookies["host"]["hostOnly"])
        self.assertNotIn("expirationDate", cookies["host"])
        self.assertEqual(cookies["subdomain"]["sameSite"], "no_restriction")

    def test_firefox_contexts_expired_and_invalid_not_collapsed(self):
        self.firefox([
            {"name": "okay"},
            {"name": "container", "originAttributes": "^userContextId=1"},
            {"name": "private", "originAttributes": "^privateBrowsingId=1"},
            {"name": "partition", "originAttributes": "^partitionKey=%28https%2Cchatgpt.com%29"},
            {"name": "oversized-isolation", "originAttributes": "x" * 70000},
            {"name": "partitioned", "isPartitioned": 1},
            {"name": "expired", "expiry": NOW - 1},
            {"name": "invalid", "value": "line\nbreak"},
        ])
        result = self.extract()
        self.assertEqual([c["name"] for c in result["cookies"]], ["okay"])
        self.assertEqual(result["skipped"], 7)
        codes = {w["code"] for w in result["warnings"]}
        self.assertTrue({"ISOLATED_COOKIES_SKIPPED", "PARTITIONED_COOKIES_SKIPPED", "EXPIRED_COOKIES_SKIPPED", "INVALID_COOKIES_SKIPPED"} <= codes)

    def test_snapshot_observes_wal_without_modifying_source(self):
        path = self.home / ".mozilla/firefox/a.default/cookies.sqlite"
        writer = firefox_db(path, [])
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO moz_cookies VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           ("chatgpt.com", "wal-new", "synthetic-wal", "/", FUTURE, 1, 1, 1, 0, "", 0))
            writer.commit()
            before = path.read_bytes()
            self.assertTrue(Path(str(path) + "-wal").exists())
            result = self.extract()
            self.assertEqual(result["cookies"][0]["name"], "wal-new")
            self.assertEqual(path.read_bytes(), before)
        finally:
            writer.close()

    def test_locked_database_returns_bounded_actionable_error(self):
        path = self.home / ".mozilla/firefox/a.default/cookies.sqlite"
        writer = firefox_db(path, [{}])
        try:
            writer.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            with self.assertRaises(login.LoginError) as error:
                self.extract()
            self.assertEqual(error.exception.code, "DATABASE_BUSY")
            self.assertLess(time.monotonic() - start, 3)
        finally:
            writer.close()

    def test_chromium_epoch_session_and_partition(self):
        self.chromium([
            {"name": "persistent"},
            {"name": "session", "expires_utc": 0, "has_expires": 0, "is_persistent": 0, "host_key": "chatgpt.com", "samesite": -1},
            {"name": "partition", "top_frame_site_key": "https://other.test"},
        ])
        result = self.extract()
        cookies = {c["name"]: c for c in result["cookies"]}
        self.assertEqual(cookies["persistent"]["expirationDate"], FUTURE)
        self.assertEqual(cookies["persistent"]["sameSite"], "strict")
        self.assertTrue(cookies["session"]["session"])
        self.assertNotIn("expirationDate", cookies["session"])
        self.assertEqual(result["skipped"], 1)

    def test_encrypted_loader_sees_only_scoped_unpartitioned_rows_and_keeps_metadata(self):
        self.chromium([
            {"name": "encrypted", "value": "", "encrypted_value": b"v11-synthetic-encrypted"},
            {"name": "unrelated", "host_key": "unrelated.test", "value": "", "encrypted_value": b"v11-do-not-read"},
            {"name": "lookalike", "host_key": "chatgpt.com.attacker.test", "value": "", "encrypted_value": b"v11-do-not-read"},
            {"name": "partition", "top_frame_site_key": "https://other.test", "value": "", "encrypted_value": b"v11-do-not-read"},
        ])
        temporary_paths = []
        seen = []

        class FakeChrome:
            def __init__(self, cookie_file, domain_name):
                temporary_paths.append(Path(cookie_file))
                self.cookie_file = cookie_file
                self.assert_domain = domain_name

            def load(self):
                self_outer.assertEqual(os.stat(self.cookie_file).st_mode & 0o777, 0o600)
                with contextlib.closing(sqlite3.connect(self.cookie_file)) as db:
                    rows = db.execute("SELECT host_key,path,name,value,encrypted_value FROM cookies").fetchall()
                    self_outer.assertEqual(db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0], "24")
                seen.extend(rows)
                print("synthetic-secret-must-be-suppressed")
                print("synthetic-secret-must-be-suppressed", file=sys.stderr)
                return [SimpleNamespace(domain=r[0], path=r[1], name=r[2], value="mock-decrypted") for r in rows]

        self_outer = self
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(login.importlib, "import_module", return_value=SimpleNamespace(Chrome=FakeChrome)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.extract()
        self.assertEqual([row[2] for row in seen], ["encrypted"])
        self.assertTrue(all(row[3] == "" for row in seen))
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        self.assertTrue(all(not path.exists() for path in temporary_paths))
        cookie = result["cookies"][0]
        self.assertEqual(cookie["value"], "mock-decrypted")
        self.assertTrue(cookie["httpOnly"])
        self.assertEqual(cookie["sameSite"], "strict")
        self.assertEqual(cookie["expirationDate"], FUTURE)

    def test_missing_crypto_preserves_plaintext_successes(self):
        self.chromium([{"name": "plain"}, {"name": "encrypted", "value": "", "encrypted_value": b"v11-synthetic"}])
        with patch.object(login.importlib, "import_module", side_effect=ImportError("secret must not leak")):
            result = self.extract()
        self.assertTrue(result["ok"])
        self.assertEqual([c["name"] for c in result["cookies"]], ["plain"])
        self.assertEqual(result["skipped"], 1)
        self.assertIn("DEPENDENCY_MISSING", {w["code"] for w in result["warnings"]})
        self.assertNotIn("secret must not leak", json.dumps(result))

    def test_all_encrypted_fail_returns_actionable_error_and_cleans_temp(self):
        self.chromium([{"name": "encrypted", "value": "", "encrypted_value": b"v11-synthetic"}])
        paths = []

        class FakeChrome:
            def __init__(self, cookie_file, domain_name):
                paths.append(Path(cookie_file))
                raise RuntimeError("sensitive exception details")

        with patch.object(login.importlib, "import_module", return_value=SimpleNamespace(Chrome=FakeChrome)):
            result = self.extract()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "DECRYPTION_FAILED")
        self.assertEqual(result["skipped"], 1)
        self.assertNotIn("sensitive", json.dumps(result))
        self.assertTrue(all(not path.exists() for path in paths))

    def test_unknown_encryption_is_not_imported_as_empty_cookie(self):
        self.chromium([{"name": "new-encryption", "value": "", "encrypted_value": b"v20-synthetic"}])
        with patch.object(login, "_chromium_values", side_effect=AssertionError("unsupported must not decrypt")):
            result = self.extract()
        self.assertEqual(result["cookies"], [])
        self.assertEqual(result["skipped"], 1)
        self.assertIn("UNSUPPORTED_ENCRYPTION", {w["code"] for w in result["warnings"]})

    def test_untrusted_paths_cannot_replace_profile_identifier(self):
        self.firefox([])
        result = login.handle_request({"action": "extract", "profile_id": str(self.home), "path": "/etc/passwd", "provider_id": "chatgpt"}, home=self.home)
        self.assertEqual(result["error"]["code"], "PROFILE_NOT_FOUND")
        result = login.handle_request({"action": "extract", "profile_id": self.profiles()[0]["id"], "provider_id": "google"}, home=self.home)
        self.assertEqual(result["error"]["code"], "INVALID_PROVIDER")

    def test_invalid_schema_and_missing_source_are_sanitized(self):
        self.chromium([])
        profile = self.profiles()[0]
        source = login._cookie_path(profile)
        source.write_bytes(b"not a database containing sensitive data")
        result = login.handle_request({"action": "extract", "profile_id": profile["id"], "provider_id": "chatgpt"}, home=self.home)
        self.assertEqual(result["error"]["code"], "DATABASE_FORMAT")
        source.unlink()
        result = login.handle_request({"action": "extract", "profile_id": profile["id"], "provider_id": "chatgpt"}, home=self.home)
        self.assertEqual(result["error"]["code"], "SOURCE_UNAVAILABLE")
        self.assertNotIn("sensitive", json.dumps(result))

    def test_cli_accepts_bounded_json_and_emits_no_traceback(self):
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        for request in (b"not-json-sensitive", b"x" * 17000, b'{"action":"list"}'):
            process = subprocess.run([sys.executable, "-m", "backend.browser_login"], input=request,
                                     env=environment, capture_output=True, timeout=5,
                                     cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stderr, b"")
            result = json.loads(process.stdout)
            self.assertEqual(result["ok"], request.startswith(b'{'))
            self.assertNotIn("sensitive", process.stdout.decode())

    def test_domain_normalization_never_uses_substring_scope(self):
        for domain in ("chatgpt.com", ".chatgpt.com", "AUTH.CHATGPT.COM", ".auth.openai.com"):
            self.assertTrue(login.domain_in_scope(domain, ["chatgpt.com", "openai.com"]))
        for domain in ("chatgpt.com.evil.test", "evilchatgpt.com", "https://chatgpt.com", "..chatgpt.com", "google.com", None):
            self.assertFalse(login.domain_in_scope(domain, ["chatgpt.com", "openai.com"]))

    def test_glm_and_kimi_login_scopes_are_fixed_to_their_current_sites(self):
        sites = json.loads(login.SITES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(sites["glm"]["domains"], ["chatglm.cn", "z.ai"])
        self.assertEqual(sites["glm"]["origins"], ["https://chatglm.cn", "https://www.chatglm.cn", "https://z.ai", "https://chat.z.ai"])
        self.assertEqual(sites["kimi"]["domains"], ["kimi.com"])
        self.assertEqual(sites["kimi"]["origins"], ["https://kimi.com", "https://www.kimi.com"])
        self.assertTrue(login.domain_in_scope("www.kimi.com", sites["kimi"]["domains"]))
        self.assertFalse(login.domain_in_scope("evilkimi.com", sites["kimi"]["domains"]))
        self.assertFalse(login.domain_in_scope("chatglm.cn.evil.test", sites["glm"]["domains"]))
        self.assertFalse(login.domain_in_scope("z.ai.evil.test", sites["glm"]["domains"]))


if __name__ == "__main__":
    unittest.main()
