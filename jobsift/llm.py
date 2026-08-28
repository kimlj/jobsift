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

    def complete_json(
        self, model: str, system: str, user: str, max_tokens: int = 2000,
        cache_system: bool = False,
    ) -> dict:
        """`cache_system` marks the system prompt for prompt caching.

        Worth it where the same system prompt goes out many times in a row,
        which is exactly the scoring stage: one email batch scores every job it
        found against the same rules and the same resume, and the resume is
        nearly all of the request - 2,780 input tokens, of which the job itself
        is about 90.

        A cache read costs a tenth of the input price and a write costs 1.25x,
        so it pays from the second call onward and is a small loss on a batch of
        one. Entries live five minutes from the start of the request that writes
        them: that covers a run, not the gap between runs, so the first job of
        each run pays the write.

        Only set this where the system prompt is byte-identical across calls.
        Caching is a PREFIX match - one varying character anywhere in it (a
        timestamp, a per-job instruction) and every call writes a fresh entry
        instead of reading one, which costs more than not caching at all.
        """
        system_param = system
        if cache_system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            logger.exception("Claude call failed (model=%s)", model)
            return {}

        if cache_system:
            # A silent zero here is the failure mode: no error, just full price
            # on every call. Worth being able to see in the log.
            usage = resp.usage
            logger.debug(
                "cache: %s written, %s read, %s uncached",
                getattr(usage, "cache_creation_input_tokens", 0),
                getattr(usage, "cache_read_input_tokens", 0),
                usage.input_tokens,
            )

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

    def complete_json(
        self, model: str, system: str, user: str, max_tokens: int = 2000,
        cache_system: bool = False,
    ) -> dict:
        # `cache_system` is accepted and ignored: OpenAI caches long prompt
        # prefixes automatically with no parameter, so the two providers stay
        # interchangeable from the caller's side.
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
