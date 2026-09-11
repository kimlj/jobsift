"""Pluggable LLM layer — pick your provider in config (deepseek | openai | anthropic).

Every provider exposes the same interface: complete_json(model, system, user) -> dict.
The SDK for a provider is imported lazily, so you only need to install the one you use.
"""

from __future__ import annotations

import logging
import os

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

    name = "OpenAI"

    def __init__(self, api_key: str, base_url: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    def _limits(self, model: str, max_tokens: int) -> dict:
        """The length and sampling settings this model accepts.

        OpenAI's current models all reason before they answer (gpt-5.6-luna,
        -terra, -sol and gpt-6-astra, per its model list on 2026-09-11). A
        reasoning model takes max_completion_tokens rather than max_tokens, spends
        its reasoning from that same budget, and is not given a temperature. The
        gpt-4 generation takes a temperature and needs no room for reasoning.
        """
        if model.startswith(("gpt-4", "gpt-3.5")):
            return {"max_completion_tokens": max_tokens, "temperature": 0}
        # Room for the reasoning ahead of a JSON answer that fits in max_tokens,
        # and "low": reading a posting and scoring it are not problems to deliberate.
        return {"max_completion_tokens": max(max_tokens, 8000), "reasoning_effort": "low"}

    def complete_json(
        self, model: str, system: str, user: str, max_tokens: int = 2000,
        cache_system: bool = False,
    ) -> dict:
        # `cache_system` is accepted and ignored: OpenAI caches long prompt
        # prefixes automatically with no parameter, so the providers stay
        # interchangeable from the caller's side.
        try:
            resp = self.client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **self._limits(model, max_tokens),
            )
        except Exception:
            logger.exception("%s call failed (model=%s)", self.name, model)
            return {}
        if resp.choices[0].finish_reason == "length":
            logger.warning(
                "Response hit its token limit and was cut off — the JSON is incomplete "
                "and will not parse. Raise max_tokens or shrink the input.",
            )
        return _parsed_or_warn(resp.choices[0].message.content or "", model)


class DeepSeekLLM(OpenAILLM):
    """DeepSeek, through its OpenAI-compatible API.

    Two differences from OpenAI, both from DeepSeek's JSON-mode docs as of
    2026-09-11: the prompt must contain the word "json", and the API "may
    occasionally return empty content". An empty reply is therefore asked for
    once more instead of being read as "found nothing", which for extract would
    silently drop every job in an email.
    """

    name = "DeepSeek"
    BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str):
        super().__init__(api_key, base_url=self.BASE_URL)

    def _limits(self, model: str, max_tokens: int) -> dict:
        return {"max_tokens": max_tokens, "temperature": 0}

    def complete_json(
        self, model: str, system: str, user: str, max_tokens: int = 2000,
        cache_system: bool = False,
    ) -> dict:
        if "json" not in (system + user).lower():
            system += "\n\nReply with one json object."
        data = super().complete_json(model, system, user, max_tokens, cache_system)
        if not data:
            logger.info("DeepSeek returned nothing usable (model=%s); asking once more", model)
            data = super().complete_json(model, system, user, max_tokens, cache_system)
        return data


# In the order --setup offers them. The value is the .env variable holding the key.
_PROVIDERS = {
    "deepseek": (DeepSeekLLM, "DEEPSEEK_API_KEY"),
    "openai": (OpenAILLM, "OPENAI_API_KEY"),
    "anthropic": (AnthropicLLM, "ANTHROPIC_API_KEY"),
}


def build_llm(provider: str, *, openai_api_key: str = "", anthropic_api_key: str = "",
              deepseek_api_key: str = ""):
    """Construct the LLM client for the configured provider.

    A key not passed in is read from the environment, where load_config has
    already put .env. That is how DEEPSEEK_API_KEY reaches the call sites
    written before DeepSeek was supported, without changing each of them.
    """
    provider = (provider or "anthropic").lower()
    entry = _PROVIDERS.get(provider)
    if not entry:
        raise SystemExit(
            f"Unknown llm provider: {provider!r}. Supported: {', '.join(_PROVIDERS)}"
        )
    cls, env_name = entry
    passed = {"deepseek": deepseek_api_key, "openai": openai_api_key,
              "anthropic": anthropic_api_key}[provider]
    return cls(passed or os.getenv(env_name, ""))
