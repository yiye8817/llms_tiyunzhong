"""Actual persistence/config checks; no HTTP stack or external website needed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from backend.models import AppConfig, ChatRequest
from backend.storage import Store

ROOT = Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict('os.environ', {'FUSION_TOKEN': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = Store(Path(self.directory.name))
        self.addCleanup(self.store.close)

    def test_token_persists_and_permissions_are_private(self):
        second = Store(Path(self.directory.name))
        try:
            self.assertEqual(self.store.token, second.token)
            self.assertGreaterEqual(len(second.token), 16)
            for filename in ('api-key.txt', 'config.json', 'history.sqlite3'):
                self.assertEqual((Path(self.directory.name) / filename).stat().st_mode & 0o777, 0o600)
        finally:
            second.close()

    def test_unicode_markdown_history_and_source_survive_restart(self):
        conversation_id, request_id = str(uuid4()), str(uuid4())
        markdown = '# 分析\n\n```python\nprint("中文")\n```\n\n|指标|值|\n|---|---|\n|PSS|42|'
        source_path = self.store.write_markdown(request_id, 'chatgpt.md', markdown)
        merged_path = self.store.write_markdown(request_id, 'merged.md', markdown)
        messages = [{'role': 'user', 'content': '如何定位内存泄漏？'}]
        fusion = {'request_id': request_id, 'conversation_id': conversation_id, 'mode': 'web',
                  'sources': [{'provider': 'chatgpt', 'markdown': markdown, 'path': source_path}],
                  'errors': [], 'merged_path': merged_path}
        self.store.save_turn(conversation_id, messages, markdown, fusion)
        second = Store(Path(self.directory.name))
        try:
            result = second.conversation(conversation_id)
            self.assertEqual(result['messages'], messages + [{'role': 'assistant', 'content': markdown}])
            self.assertEqual(result['runs'][0]['sources'][0]['markdown'], markdown)
            self.assertEqual(Path(source_path).read_text(), markdown)
            self.assertEqual(second.history()[0]['id'], conversation_id)
            self.assertIsNone(second.conversation(str(uuid4())))
        finally:
            second.close()

    def test_saved_config_is_not_overwritten_by_bootstrap(self):
        config = self.store.config.model_copy(deep=True)
        config.providers[0].name = '我的 ChatGPT'
        self.store.write_config(config)
        second = Store(Path(self.directory.name))
        try:
            self.assertEqual(second.config.providers[0].name, '我的 ChatGPT')
        finally:
            second.close()


    def test_version_one_defaults_migrate_once_and_preserve_provider_settings(self):
        legacy = self.store.config.model_dump()
        legacy["schema_version"] = 1
        legacy["allow_partial"] = True
        legacy["providers"] = [p for p in legacy["providers"] if p["id"] not in ("glm", "kimi")]
        legacy["generation"].pop("submission_timeout_seconds")
        legacy["generation"]["timeout_seconds"] = 180
        legacy["fusion"]["timeout_seconds"] = 180
        legacy["providers"][0]["name"] = "Custom provider"
        legacy["providers"][0]["proxy"] = "http://127.0.0.1:10822"
        self.store.config_path.write_text(json.dumps(legacy))
        second = Store(Path(self.directory.name))
        try:
            self.assertEqual(second.config.schema_version, 3)
            self.assertFalse(second.config.allow_partial)
            self.assertEqual(second.config.generation.timeout_seconds, 600)
            self.assertEqual(second.config.fusion.timeout_seconds, 600)
            self.assertEqual(second.config.generation.submission_timeout_seconds, 120)
            migrated = [p.model_dump() for p in second.config.providers]
            self.assertEqual(migrated[:-2], legacy["providers"])
            self.assertEqual([p["id"] for p in migrated[-2:]], ["glm", "kimi"])
            self.assertTrue(all(not p["enabled"] for p in migrated[-2:]))
            second.config.allow_partial = True
            second.write_config(second.config)
        finally:
            second.close()
        third = Store(Path(self.directory.name))
        try:
            self.assertTrue(third.config.allow_partial, "A new schema explicit partial opt-in survives restart")
            self.assertEqual(json.loads(third.config_path.read_text())["schema_version"], 3)
        finally:
            third.close()

    def test_migration_preserves_custom_deadlines(self):
        legacy = self.store.config.model_dump()
        legacy.update(schema_version=1, allow_partial=True)
        legacy["generation"]["timeout_seconds"] = 90
        legacy["fusion"]["timeout_seconds"] = 900
        self.store.config_path.write_text(json.dumps(legacy))
        second = Store(Path(self.directory.name))
        try:
            self.assertEqual(second.config.generation.timeout_seconds, 90)
            self.assertEqual(second.config.fusion.timeout_seconds, 900)
            self.assertFalse(second.config.allow_partial)
        finally:
            second.close()

    def test_version_two_appends_only_missing_optional_sites_and_is_idempotent(self):
        legacy = self.store.config.model_dump()
        legacy["schema_version"] = 2
        legacy["providers"] = [p for p in legacy["providers"] if p["id"] != "kimi"]
        glm = next(p for p in legacy["providers"] if p["id"] == "glm")
        glm.update(name="我的 GLM", url="https://chatglm.cn/custom", proxy="http://127.0.0.1:10822")
        glm["selectors"]["send"] = ["button[data-custom='glm-send']"]
        before = json.loads(json.dumps(legacy["providers"]))
        self.store.config_path.write_text(json.dumps(legacy), encoding="utf-8")
        second = Store(Path(self.directory.name))
        try:
            providers = [p.model_dump() for p in second.config.providers]
            self.assertEqual(second.config.schema_version, 3)
            self.assertEqual(providers[:-1], before)
            self.assertEqual(providers[-1]["id"], "kimi")
            self.assertFalse(providers[-1]["enabled"])
            self.assertEqual(next(p for p in providers if p["id"] == "glm")["proxy"], "http://127.0.0.1:10822")
        finally:
            second.close()
        on_disk = self.store.config_path.read_text(encoding="utf-8")
        third = Store(Path(self.directory.name))
        try:
            self.assertEqual(self.store.config_path.read_text(encoding="utf-8"), on_disk)
            self.assertEqual([p.id for p in third.config.providers].count("kimi"), 1)
            self.assertEqual([p.id for p in third.config.providers].count("glm"), 1)
        finally:
            third.close()

    def test_exact_legacy_glm_default_url_migrates_but_custom_url_does_not(self):
        raw = self.store.config.model_dump()
        glm = next(p for p in raw["providers"] if p["id"] == "glm")
        glm["url"] = "https://chatglm.cn/"
        self.store.config_path.write_text(json.dumps(raw), encoding="utf-8")
        migrated = Store(Path(self.directory.name))
        try:
            self.assertEqual(next(p.url for p in migrated.config.providers if p.id == "glm"),
                             "https://chat.z.ai/")
        finally:
            migrated.close()
        raw = self.store.config.model_dump()
        next(p for p in raw["providers"] if p["id"] == "glm")["url"] = "https://chatglm.cn/custom"
        self.store.config_path.write_text(json.dumps(raw), encoding="utf-8")
        custom = Store(Path(self.directory.name))
        try:
            self.assertEqual(next(p.url for p in custom.config.providers if p.id == "glm"),
                             "https://chatglm.cn/custom")
        finally:
            custom.close()

    def test_version_two_maximum_catalog_keeps_all_twenty_entries_before_append(self):
        legacy = self.store.config.model_dump()
        legacy["schema_version"] = 2
        legacy["providers"] = [p for p in legacy["providers"] if p["id"] not in ("glm", "kimi")]
        template = json.loads(json.dumps(legacy["providers"][-1]))
        while len(legacy["providers"]) < 20:
            provider = json.loads(json.dumps(template))
            provider.update(id=f"custom-{len(legacy['providers']):02d}", name=f"Custom {len(legacy['providers']):02d}", enabled=False)
            legacy["providers"].append(provider)
        original = json.loads(json.dumps(legacy["providers"]))
        self.store.config_path.write_text(json.dumps(legacy), encoding="utf-8")
        second = Store(Path(self.directory.name))
        try:
            migrated = [p.model_dump() for p in second.config.providers]
            self.assertEqual(migrated[:20], original)
            self.assertEqual([p["id"] for p in migrated[20:]], ["glm", "kimi"])
            self.assertTrue(all(not p["enabled"] for p in migrated[20:]))
            self.assertEqual(len(migrated), 22)
        finally:
            second.close()

    def test_invalid_legacy_configuration_is_not_overwritten(self):
        legacy = self.store.config.model_dump()
        legacy["schema_version"] = 1
        legacy["providers"][0]["url"] = "file:///private"
        original = json.dumps(legacy)
        self.store.config_path.write_text(original)
        with self.assertRaises(ValidationError):
            Store(Path(self.directory.name))
        self.assertEqual(self.store.config_path.read_text(), original)


class ConfigTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT / 'config.example.json').read_text())

    def test_default_pair_and_unsupported_requests(self):
        config = AppConfig.model_validate(self.config())
        self.assertEqual(config.schema_version, 3)
        self.assertEqual([p.id for p in config.providers if p.enabled], ['chatgpt', 'deepseek'])
        self.assertEqual([p.id for p in config.providers][-2:], ['glm', 'kimi'])
        self.assertFalse(config.allow_partial)
        self.assertEqual(config.generation.submission_timeout_seconds, 120)
        self.assertTrue(all(p.access_interval_seconds == 0 for p in config.providers))
        invalid = self.config()
        invalid['providers'][0]['access_interval_seconds'] = 3601
        with self.assertRaises(ValidationError):
            AppConfig.model_validate(invalid)
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate({'messages': [{'role': 'user', 'content': [{'type': 'image_url'}]}]})
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate({'messages': [{'role': 'user', 'content': 'hello'}], 'tools': []})

    def test_disabled_synthesizer_duplicate_ids_and_bad_urls_rejected(self):
        for mutate in (
            lambda c: c['fusion'].update(provider='claude'),
            lambda c: c['providers'][1].update(id='chatgpt'),
            lambda c: c['providers'][0].update(url='file:///etc/passwd'),
            lambda c: c['providers'][0].update(url='https://user:password@example.com/'),
            lambda c: c['generation'].update(timeout_seconds=15, min_wait_seconds=20),
            lambda c: c['generation'].update(submission_timeout_seconds=601),
            lambda c: c['generation'].update(submission_timeout_seconds=0),
        ):
            with self.subTest(mutate=mutate):
                config = self.config()
                mutate(config)
                with self.assertRaises(ValidationError):
                    AppConfig.model_validate(config)


if __name__ == '__main__':
    unittest.main()
