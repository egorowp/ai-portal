
import os
import time
import random
import logging

from dataclasses import dataclass
from typing import Optional

from openai import (
    OpenAI,
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


logger = logging.getLogger("ai_provider_manager")


@dataclass
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str
    max_requests: int = 6
    enabled: bool = True


@dataclass
class GenerationResult:
    text: str
    provider: str
    model: str


class AIProviderError(Exception):
    pass


class AIProviderManager:

    def __init__(self):

        self.max_generations = int(
            os.getenv("AI_MAX_GENERATIONS_PER_RUN", "6")
        )

        self.max_retries = int(
            os.getenv("AI_MAX_RETRIES", "2")
        )

        self.timeout = float(
            os.getenv("AI_TIMEOUT_SECONDS", "60")
        )

        self.retry_base = float(
            os.getenv("AI_RETRY_BASE_SECONDS", "2")
        )

        self.success_count = 0
        self.request_counts = {}
        self.clients = {}

        self.providers = self._load_providers()

        logger.info(
            "AI Manager initialized. Providers: %s",
            [p.name for p in self.providers]
        )

    def _load_providers(self):

        definitions = {
            "gemini": {
                "key": "GEMINI_API_KEY",
                "url": (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/openai/"
                ),
                "model_key": "GEMINI_MODEL",
                "default_model": "gemini-2.5-flash",
            },

            "groq": {
                "key": "GROQ_API_KEY",
                "url": "https://api.groq.com/openai/v1",
                "model_key": "GROQ_MODEL",
                "default_model": "llama-3.3-70b-versatile",
            },

            "openrouter": {
                "key": "OPENROUTER_API_KEY",
                "url": "https://openrouter.ai/api/v1",
                "model_key": "OPENROUTER_MODEL",
                "default_model": (
                    "meta-llama/llama-3.3-70b-instruct:free"
                ),
            },

            "cloudflare": {
                "key": "CLOUDFLARE_API_TOKEN",
                "url": (
                    "https://api.cloudflare.com/client/v4/"
                    "accounts/"
                    + os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
                    + "/ai/v1"
                ),
                "model_key": "CLOUDFLARE_MODEL",
                "default_model": "@cf/meta/llama-3.1-8b-instruct",
            },
        }

        order = os.getenv(
            "AI_PROVIDER_ORDER",
            "gemini,groq,openrouter,cloudflare"
        ).split(",")

        providers = []

        for name in order:

            name = name.strip().lower()

            definition = definitions.get(name)

            if not definition:
                logger.warning("Unknown provider: %s", name)
                continue

            api_key = os.getenv(definition["key"])

            if not api_key:
                logger.warning(
                    "Provider %s disabled: missing API key", name
                )
                continue

            if name == "cloudflare" and not os.getenv(
                "CLOUDFLARE_ACCOUNT_ID"
            ):
                logger.warning(
                    "Cloudflare disabled: missing account ID"
                )
                continue

            providers.append(
                ProviderConfig(
                    name=name,
                    api_key=api_key,
                    base_url=definition["url"],
                    model=os.getenv(
                        definition["model_key"],
                        definition["default_model"]
                    ),
                    max_requests=int(
                        os.getenv(
                            f"AI_{name.upper()}_MAX_REQUESTS",
                            "6"
                        )
                    ),
                )
            )

        return providers

    def _get_client(self, provider):

        if provider.name not in self.clients:

            self.clients[provider.name] = OpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout=self.timeout,
                max_retries=0,
            )

        return self.clients[provider.name]

    def _can_use(self, provider):

        count = self.request_counts.get(provider.name, 0)

        return count < provider.max_requests

    def _is_retryable(self, error):

        if isinstance(
            error,
            (APIConnectionError, APITimeoutError,
             InternalServerError, RateLimitError)
        ):
            return True

        if isinstance(error, APIStatusError):
            return error.status_code in (408, 409, 429) or (
                error.status_code >= 500
            )

        return False

    def _retry_delay(self, error, attempt):

        retry_after = None

        response = getattr(error, "response", None)

        if response is not None:
            retry_after = response.headers.get("retry-after")

        if retry_after:
            try:
                return min(float(retry_after), 30)
            except ValueError:
                pass

        delay = self.retry_base * (2 ** attempt)

        return min(delay + random.uniform(0, 1), 30)

    def _generate_with_provider(
        self,
        provider,
        messages,
        temperature,
        max_tokens,
    ):

        client = self._get_client(provider)

        for attempt in range(self.max_retries + 1):

            self.request_counts[provider.name] = (
                self.request_counts.get(provider.name, 0) + 1
            )

            try:

                logger.info(
                    "Request provider=%s model=%s attempt=%s",
                    provider.name,
                    provider.model,
                    attempt + 1,
                )

                response = client.chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                text = response.choices[0].message.content

                if not text:
                    raise AIProviderError(
                        "Provider returned empty response"
                    )

                logger.info(
                    "Generation successful: provider=%s",
                    provider.name,
                )

                return GenerationResult(
                    text=text,
                    provider=provider.name,
                    model=provider.model,
                )

            except Exception as error:

                retryable = self._is_retryable(error)

                logger.warning(
                    "Provider error: provider=%s type=%s "
                    "retryable=%s",
                    provider.name,
                    type(error).__name__,
                    retryable,
                )

                if not retryable:
                    raise

                if attempt >= self.max_retries:
                    raise

                delay = self._retry_delay(error, attempt)

                logger.info(
                    "Retry in %.1f seconds: provider=%s",
                    delay,
                    provider.name,
                )

                time.sleep(delay)

        raise AIProviderError("Provider attempts exhausted")

    def generate(
        self,
        messages,
        temperature=0.4,
        max_tokens=2000,
    ):

        if self.success_count >= self.max_generations:
            raise AIProviderError(
                "Maximum generations per run reached"
            )

        errors = []

        for provider in self.providers:

            if not self._can_use(provider):

                logger.warning(
                    "Provider request limit reached: %s",
                    provider.name,
                )
                continue

            try:

                result = self._generate_with_provider(
                    provider=provider,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                self.success_count += 1

                logger.info(
                    "Successful generations: %s/%s",
                    self.success_count,
                    self.max_generations,
                )

                return result

            except Exception as error:

                errors.append(
                    f"{provider.name}: {type(error).__name__}"
                )

                logger.warning(
                    "Fallback activated after %s",
                    provider.name,
                )

                continue

        raise AIProviderError(
            "All providers unavailable: " + "; ".join(errors)
        )

    def close(self):

        for client in self.clients.values():
            client.close()

        logger.info("AI Provider Manager closed")
