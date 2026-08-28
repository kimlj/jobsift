"""Pluggable LLM layer — pick your provider in config (openai | anthropic).

Every provider exposes the same interface: complete_json(model, system, user) -> dict.
The SDK for a provider is imported lazily, so you only need to install the one you use.
"""

from __future__ import annotations

import logging

from .utils import parse_json_object

logger = logging.getLogger(__name__)


def _parsed_or_warn(text: str, model: str) -> dict:
    """Parse the response, logging when a non-empty reply yields nothing.

    Without this, an unparseable response is indistinguishable from a genuine
    "found nothing" — the caller just sees {} either way.
    """
    data = parse_json_object(text)
    if not data and text.strip():
        logger.warning(
            "Could not parse JSON from %s response (%d chars). Starts: %.120s",
            model,
            len(text),
            text.strip(),
        )
    return data


class AnthropicLLM:
    """Claude via the Anthropic Messages API."""

    def __init__(self, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)

    def complete_json(self, model: str, system: str, user: str, max_tokens: int = 2000) -> dict:
        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            logger.exception("Claude call failed (model=%s)", model)
            return {}
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        if resp.stop_reason == "max_tokens":
            logger.warning(
                "Response hit max_tokens (%d) and was cut off — the JSON is incomplete "
                "and will not parse. Raise max_tokens or shrink the input.",
                max_tokens,
            )
        return _parsed_or_warn(text, model)


class OpenAILLM:
    """GPT via the OpenAI Chat Completions API (JSON mode)."""

    def __init__(self, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)

    def complete_json(self, model: str, system: str, user: str, max_tokens: int = 2000) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception:
            logger.exception("OpenAI call failed (model=%s)", model)
            return {}
        if resp.choices[0].finish_reason == "length":
            logger.warning(
                "Response hit max_tokens (%d) and was cut off — the JSON is incomplete "
                "and will not parse. Raise max_tokens or shrink the input.",
                max_tokens,
            )
        return _parsed_or_warn(resp.choices[0].message.content or "", model)


_PROVIDERS = {
    "anthropic": (AnthropicLLM, "anthropic_api_key"),
    "openai": (OpenAILLM, "openai_api_key"),
}


def build_llm(provider: str, *, openai_api_key: str = "", anthropic_api_key: str = ""):
    """Construct the LLM client for the configured provider."""
    provider = (provider or "anthropic").lower()
    keys = {"anthropic_api_key": anthropic_api_key, "openai_api_key": openai_api_key}
    entry = _PROVIDERS.get(provider)
    if not entry:
        raise SystemExit(
            f"Unknown llm provider: {provider!r}. Supported: {', '.join(_PROVIDERS)}"
        )
    cls, key_name = entry
    return cls(keys[key_name])
