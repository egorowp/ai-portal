"""Unit tests for provider configuration, limits and fallback."""

import os
import unittest
from unittest.mock import patch

from agent.ai_provider_manager import (
    AIProviderError,
    AIProviderManager,
    GenerationResult,
)


class AIProviderManagerTests(unittest.TestCase):
    def make_manager(self):
        env = {
            "AI_PROVIDER_ORDER": "gemini,groq,openrouter,cloudflare",
            "GEMINI_API_KEY": "test-gemini",
            "GROQ_API_KEY": "test-groq",
            "OPENROUTER_API_KEY": "test-openrouter",
            "CLOUDFLARE_API_TOKEN": "test-cloudflare",
            "CLOUDFLARE_ACCOUNT_ID": "test-account",
            "AI_MAX_GENERATIONS_PER_RUN": "2",
            "AI_MAX_RETRIES": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            return AIProviderManager()

    def test_provider_order_and_cloudflare_url(self):
        manager = self.make_manager()
        self.assertEqual(
            [provider.name for provider in manager.providers],
            ["gemini", "groq", "openrouter", "cloudflare"],
        )
        self.assertIn("test-account", manager.providers[-1].base_url)
        manager.close()

    def test_missing_credentials_disable_provider(self):
        with patch.dict(os.environ, {"AI_PROVIDER_ORDER": "gemini,groq"}, clear=True):
            manager = AIProviderManager()
        self.assertEqual(manager.providers, [])
        manager.close()

    def test_fallback_to_next_provider(self):
        manager = self.make_manager()
        calls = []

        def fake_generate(provider, messages, temperature, max_tokens, validator):
            calls.append(provider.name)
            if provider.name == "gemini":
                raise RuntimeError("simulated provider failure")
            return GenerationResult("{\"ok\": true}", provider.name, provider.model)

        manager._generate_with_provider = fake_generate
        result = manager.generate([{"role": "user", "content": "test"}])
        self.assertEqual(result.provider, "groq")
        self.assertEqual(calls, ["gemini", "groq"])
        self.assertEqual(manager.success_count, 1)
        manager.close()

    def test_success_limit_is_enforced(self):
        manager = self.make_manager()
        manager.success_count = manager.max_generations
        with self.assertRaises(AIProviderError):
            manager.generate([{"role": "user", "content": "test"}])
        manager.close()

    def test_zero_generation_limit_is_respected(self):
        with patch.dict(os.environ, {"AI_MAX_GENERATIONS_PER_RUN": "0"}, clear=False):
            manager = AIProviderManager()
        self.assertEqual(manager.max_generations, 0)
        manager.close()

    def test_zero_provider_request_budget_disables_provider(self):
        env = {
            "AI_PROVIDER_ORDER": "gemini",
            "GEMINI_API_KEY": "test-gemini",
            "AI_GEMINI_MAX_REQUESTS": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            manager = AIProviderManager()
        self.assertEqual(manager.providers[0].max_requests, 0)
        self.assertFalse(manager._can_use(manager.providers[0]))
        manager.close()

    def test_request_budget_counts_each_attempt(self):
        manager = self.make_manager()
        provider = manager.providers[0]
        provider = type(provider)(provider.name, provider.api_key, provider.base_url, provider.model, 1)
        self.assertTrue(manager._reserve_request(provider))
        self.assertFalse(manager._reserve_request(provider))
        self.assertEqual(manager.request_counts[provider.name], 1)
        manager.close()


if __name__ == "__main__":
    unittest.main()
