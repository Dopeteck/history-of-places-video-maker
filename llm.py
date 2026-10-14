"""Bounded, validated JSON calls using the project's existing provider adapters."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

import config

log = logging.getLogger(__name__)


def extract_json(raw: str) -> dict:
    """Find an object without joining unrelated JSON objects or trailing prose."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Model returned no valid JSON object")


def provider_call(provider: dict, model: str, system: str, user: str) -> str:
    # Reuse the already integrated SDK adapters; no credentials in prompts/logs.
    from main import _call_gemini, _call_openai_compatible
    call = _call_gemini if provider["type"] == "gemini" else _call_openai_compatible
    return call(provider, model, system, user)


class JsonLLM:
    """Validate every response and back off before trying another provider."""

    def __init__(self, call: Callable = provider_call, sleep: Callable = time.sleep,
                 providers: list[dict] | None = None, min_interval: float = 5.0,
                 cache_dir: Path | None = None):
        self.cache_dir = cache_dir
        self.call, self.sleep = call, sleep
        self.providers = config.PROVIDERS if providers is None else providers
        self.min_interval = min_interval
        self.last_call: float | None = None

    def generate(self, system: str, prompt: dict, validate: Callable[[dict], Any]) -> Any:
        from story import digest
        from story_store import read_json, write_json
        cache = self.cache_dir / (digest({'system': system, 'prompt': prompt}) + '.json') if self.cache_dir else None
        if cache and cache.exists():
            try:
                return validate(read_json(cache))
            except (ValueError, KeyError, TypeError):
                log.warning('Ignoring invalid cached model response')
        eligible = [p for p in self.providers if os.getenv(p["env_key"])]
        if not eligible:
            raise RuntimeError("No script API key: set GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY")
        errors = []
        for provider in eligible:
            for model in provider["models"]:
                correction = ""
                for attempt in range(2):
                    if self.last_call is not None:
                        self.sleep(max(0, self.min_interval - (time.monotonic() - self.last_call)))
                    self.last_call = time.monotonic()
                    try:
                        raw = self.call(provider, model, system,
                                        json.dumps(prompt, ensure_ascii=False) + correction)
                        data = extract_json(raw)
                        result = validate(data)
                        if cache:
                            write_json(cache, data)
                        return result
                    except Exception as exc:
                        # Log exception type only: SDK errors can include headers or keys.
                        errors.append(f"{provider['name']}/{model}: {type(exc).__name__}")
                        log.warning("Script attempt failed: %s", errors[-1])
                        if isinstance(exc, (ValueError, KeyError, TypeError)):
                            correction = "\nReturn the entire corrected JSON. Validation failed: " + str(exc)[:500]
                        else:
                            self.sleep(min(60, 8 * (2 ** attempt) + random.uniform(0, 1)))
        raise RuntimeError("All script providers failed validation or availability: " + "; ".join(errors))
