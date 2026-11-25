"""Unified OpenAI-compatible AI providers with bounded retry and fallback."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

logger = logging.getLogger("ai_provider_manager")


class AIProviderError(RuntimeError):
    """Raised when no configured provider can complete a generation."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str
    max_requests: int = 6


@dataclass(frozen=True)
class GenerationResult:
    text: str
    provider: str
    model: str


class AIProviderManager:
    """Sequential provider router. Limits count every outbound API attempt."""

    PROVIDER_DEFINITIONS = {
        "gemini": {
            "key": "GEMINI_API_KEY",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "model_key": "GEMINI_MODEL",
            "default_model": "gemini-3.8-flash",
        },
        "groq": {
            "key": "GROQ_API_KEY",
            "url": "https://api.groq.com/openai/v1",
            "model_key": "GROQ_MODEL",
            "default_model": "openai/gpt-oss-120b",
        },
        "openrouter": {
            "key": "OPENROUTER_API_KEY",
            "url": "https://openrouter.ai/api/v1",
            "model_key": "OPENROUTER_MODEL",
            "default_model": "openrouter/free",
        },
        "cloudflare": {
            "key": "CLOUDFLARE_API_TOKEN",
            "url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            "model_key": "CLOUDFLARE_MODEL",
            "default_model": "@cf/meta/llama-3.1-8b-instruct",
        },
    }

    def __init__(self) -> None:
        self.max_generations = self._int_env(
            "AI_MAX_GENERATIONS_PER_RUN",
            self._int_env("MAX_GENERATIONS_PER_RUN", 6),
            minimum=0,
        )
        self.max_retries = self._int_env("AI_MAX_RETRIES", 1, minimum=0)
        self.timeout = self._float_env("AI_TIMEOUT_SECONDS", 90.0, minimum=1.0)
        self.retry_base = self._float_env("AI_RETRY_BASE_SECONDS", 2.0, minimum=0.0)
        self.max_retry_delay = self._float_env("AI_MAX_RETRY_DELAY_SECONDS", 30.0, minimum=0.0)
        self.success_count = 0
        self.request_counts: dict[str, int] = {}
        self.clients: dict[str, OpenAI] = {}
        self._lock = threading.Lock()
        self.providers = self._load_providers()
        logger.info(
            "AI manager initialized; providers=%s max_generations=%d retries=%d",
            [provider.name for provider in self.providers],
            self.max_generations,
            self.max_retries,
        )

    @staticmethod
    def _int_env(name: str, default: int, minimum: int = 0) -> int:
        value = os.getenv(name, "").strip()
        try:
            return max(minimum, int(value)) if value else default
        except ValueError:
            logger.warning("Invalid integer setting %s; using default", name)
            return default

    @staticmethod
    def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
        value = os.getenv(name, "").strip()
        try:
            return max(minimum, float(value)) if value else default
        except ValueError:
            logger.warning("Invalid numeric setting %s; using default", name)
            return default

    def _load_providers(self) -> list[ProviderConfig]:
        order = os.getenv("AI_PROVIDER_ORDER", "").strip()
        if not order:
            order = "gemini,groq,openrouter,cloudflare"
        providers: list[ProviderConfig] = []

        for raw_name in order.split(","):
            name = raw_name.strip().lower()
            if not name:
                continue
            definition = self.PROVIDER_DEFINITIONS.get(name)
            if definition is None:
                logger.warning("Unknown AI provider in AI_PROVIDER_ORDER: %s", name)
                continue

            api_key = os.getenv(definition["key"], "").strip()
            if not api_key:
                logger.info("Provider disabled; missing %s", definition["key"])
                continue

            if name == "cloudflare":
                account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
                if not account_id:
                    logger.warning("Cloudflare disabled; CLOUDFLARE_ACCOUNT_ID is missing")
                    continue
                base_url = definition["url"].format(account_id=account_id)
            else:
                base_url = definition["url"]

            model = os.getenv(definition["model_key"], "").strip()
            if not model:
                model = definition["default_model"]

            providers.append(
                ProviderConfig(
                    name=name,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    max_requests=self._int_env(
                        f"AI_{name.upper()}_MAX_REQUESTS", 6, minimum=0
                    ),
                )
            )

        return providers

    def _get_client(self, provider: ProviderConfig) -> OpenAI:
        if provider.name not in self.clients:
            headers: dict[str, str] = {}
            if provider.name == "openrouter":
                headers["X-Title"] = "AI Portal"
                referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
                if referer:
                    headers["HTTP-Referer"] = referer
            self.clients[provider.name] = OpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout=self.timeout,
                max_retries=0,  # retries are controlled here, not by the SDK
                default_headers=headers or None,
            )
        return self.clients[provider.name]

    def _can_use(self, provider: ProviderConfig) -> bool:
        return self.request_counts.get(provider.name, 0) < provider.max_requests

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(
            error,
            (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError),
        ):
            return True
        if isinstance(error, APIStatusError):
            return error.status_code in (408, 409, 425, 429) or error.status_code >= 500
        return False

    def _retry_delay(self, error: Exception, attempt: int) -> float:
        response = getattr(error, "response", None)
        retry_after = response.headers.get("retry-after") if response is not None else None
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.max_retry_delay)
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return min(max(seconds, 0.0), self.max_retry_delay)
                except (TypeError, ValueError, OverflowError):
                    pass
        delay = self.retry_base * (2 ** attempt)
        return min(delay + random.uniform(0, 1), self.max_retry_delay)

    def _reserve_request(self, provider: ProviderConfig) -> bool:
        with self._lock:
            if not self._can_use(provider):
                return False
            self.request_counts[provider.name] = self.request_counts.get(provider.name, 0) + 1
            return True

    def _generate_with_provider(
        self,
        provider: ProviderConfig,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        validator: Callable[[str], Any] | None,
    ) -> GenerationResult:
        client = self._get_client(provider)
        for attempt in range(self.max_retries + 1):
            # Every retry is a real API call and consumes the provider's request budget.
            if not self._reserve_request(provider):
                raise AIProviderError(f"Request limit reached for {provider.name}")
            try:
                logger.info(
                    "AI request provider=%s model=%s attempt=%d/%d request_count=%d/%d",
                    provider.name,
                    provider.model,
                    attempt + 1,
                    self.max_retries + 1,
                    self.request_counts[provider.name],
                    provider.max_requests,
                )
                response = client.chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                choices = getattr(response, "choices", None) or []
                content = choices[0].message.content if choices else None
                if not isinstance(content, str) or not content.strip():
                    raise AIProviderError("Provider returned an empty response")
                if validator is not None:
                    validator(content)
                return GenerationResult(content, provider.name, provider.model)
            except Exception as error:
                retryable = self._is_retryable(error)
                logger.warning(
                    "AI request failed provider=%s error_type=%s retryable=%s error=%s",
                    provider.name,
                    type(error).__name__,
                    retryable,
                )
                if not retryable or attempt >= self.max_retries or not self._can_use(provider):
                    raise
                delay = self._retry_delay(error, attempt)
                logger.info("Retry scheduled provider=%s delay_seconds=%.1f", provider.name, delay)
                time.sleep(delay)
        raise AIProviderError(f"Attempts exhausted for {provider.name}")

    def generate(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.4,
        max_tokens: int = 3000,
        validator: Callable[[str], Any] | None = None,
    ) -> GenerationResult:
        with self._lock:
            if self.success_count >= self.max_generations:
                raise AIProviderError("Maximum successful generations per run reached")

        if not self.providers:
            raise AIProviderError("No AI providers configured; check API keys and AI_PROVIDER_ORDER")

        errors: list[str] = []
        for provider in self.providers:
            if not self._can_use(provider):
                logger.warning("Provider request budget exhausted: %s", provider.name)
                continue
            try:
                result = self._generate_with_provider(
                    provider, messages, temperature, max_tokens, validator
                )
                with self._lock:
                    self.success_count += 1
                    successful_count = self.success_count
                logger.info(
                    "Generation successful provider=%s model=%s successful=%d/%d",
                    result.provider,
                    result.model,
                    successful_count,
                    self.max_generations,
                )
                return result
            except Exception as error:
                errors.append(f"{provider.name}:{type(error).__name__}: {str(error)[:240]}")
                logger.warning("Fallback: provider=%s failed; trying next configured provider", provider.name)

        raise AIProviderError("All AI providers failed: " + ", ".join(errors))

    def close(self) -> None:
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                logger.debug("Error while closing AI client", exc_info=True)
        self.clients.clear()
