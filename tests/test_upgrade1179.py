import unittest
from pydantic import ValidationError
from backend.models import GenerationSettings

class RetrySettings1179(unittest.TestCase):
    def test_defaults_enable_staged_local_recovery_and_twenty_second_manual_wait(self):
        s=GenerationSettings()
        self.assertTrue(s.qwen_retry_stages)
        self.assertTrue(s.qwen_retry_screenshot)
        self.assertTrue(s.qwen_retry_learning)
        self.assertEqual(s.qwen_manual_retry_wait_seconds,20)
        self.assertEqual(s.qwen_retry_trigger_wait_seconds,3)

    def test_values_roundtrip_without_changing_other_settings(self):
        s=GenerationSettings(qwen_manual_retry_wait_seconds=25,qwen_retry_learning=False)
        clone=GenerationSettings.model_validate(s.model_dump())
        self.assertEqual(clone.qwen_manual_retry_wait_seconds,25)
        self.assertFalse(clone.qwen_retry_learning)
        self.assertEqual(clone.timeout_seconds,600)

    def test_invalid_delays_rejected(self):
        for v in [0,-1,121,float('inf')]:
            with self.subTest(v=v),self.assertRaises(ValidationError):GenerationSettings(qwen_manual_retry_wait_seconds=v)
        for v in [0,16]:
            with self.subTest(v=v),self.assertRaises(ValidationError):GenerationSettings(qwen_retry_trigger_wait_seconds=v)

    def test_flags_do_not_accept_string_booleans(self):
        for key in ['qwen_retry_stages','qwen_retry_screenshot','qwen_retry_learning']:
            with self.subTest(key=key),self.assertRaises(ValidationError):GenerationSettings(**{key:'false'})
