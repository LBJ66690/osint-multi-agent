"""Verify quota decisions at the HTTP boundary without calling Bailian."""
import json
import os
import unittest
from datetime import date
from unittest.mock import patch

import httpx

import agents
import bailian_models as quota
import config


class ModelQuotaTests(unittest.IsolatedAsyncioTestCase):
    def test_screenshot_list_cutoff_and_fallback(self):
        self.assertEqual(set(quota.BAILIAN_MODEL_IDS), {
            "qwen4-max", "qwen3.8-flash", "kimi-k3.1-pro",
            "deepseek-v4.1-flash-1017", "deepseek-v4.1-flash", "glm-5.3",
        })
        self.assertIsNone(quota.quota_unavailable("glm-5.3", date(2026, 11, 22)))
        self.assertIn("2026-11-23", quota.quota_unavailable("glm-5.3", date(2026, 11, 23)))
        with patch.dict(os.environ, {"OSINT_DISABLED_MODELS": ",".join(quota.BAILIAN_MODEL_IDS)}):
            self.assertIsNone(quota.available_model())
            with self.assertRaisesRegex(ValueError, "没有可用"):
                config.resolve_model()

    async def test_transport_blocks_other_model_and_exhaustion_before_send(self):
        seen = []

        def serve(request):
            seen.append(json.loads(request.content)["model"])
            return httpx.Response(403, json={"error": {"code": "AllocationQuota.FreeTierOnly"}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(serve),
            event_hooks={"request": [agents._guard_model_request], "response": [agents._observe_model_response]},
        ) as client:
            url = config.BASE_URL + "/responses"
            with patch.dict(os.environ, {"OSINT_BAILIAN_FREE_ONLY_CONFIRMED": "0"}):
                with self.assertRaisesRegex(RuntimeError, "用完即停"):
                    await client.post(url, json={"model": "qwen4-max"})
            with patch.dict(os.environ, {"OSINT_BAILIAN_FREE_ONLY_CONFIRMED": "1"}), patch.object(quota, "_exhausted", set()):
                with self.assertRaises(ValueError):
                    await client.post(url, json={"model": "third-party"})
                with patch.object(config, "EXPERIMENT_MODE", True):
                    with self.assertRaises(RuntimeError):
                        await client.post(url, json={"model": config.RADEON_MODEL})
                self.assertEqual(seen, [])
                self.assertEqual((await client.post(url, json={"model": "qwen4-max"})).status_code, 403)
                self.assertIn("额度不可用", quota.quota_unavailable("qwen4-max"))
                with self.assertRaises(ValueError):
                    await client.post(url, json={"model": "qwen4-max"})
                self.assertEqual(seen, ["qwen4-max"])
