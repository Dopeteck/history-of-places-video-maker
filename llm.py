"""Bounded, validated JSON calls across Groq, Gemini, and OpenRouter."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import config

log = logging.getLogger(__name__)
_BODY_LIMIT = 1200


class ProviderHTTPError(RuntimeError):
    def __init__(self, status: int | None, body: str = "", message: str = "provider request failed"):
        super().__init__(message)
        self.status_code = status
        self.response_body = body[:_BODY_LIMIT]


def _safe(value: Any) -> str:
    """Redact known API keys before writing provider diagnostics."""
    result = str(value)
    for key_name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(key_name)
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]", result)
    return result[:_BODY_LIMIT]


def _error_details(exc: Exception) -> tuple[int | None, str]:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    if response is not None:
        status = status or getattr(response, "status_code", None)
        try:
            body = response.text
        except Exception:
            body = ""
        if not body:
            body = getattr(exc, "body", "") or ""
    else:
        body = getattr(exc, "body", "") or getattr(exc, "response_body", "") or ""
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    return (int(status) if str(status).isdigit() else None, _safe(body))


def _log_failure(provider: str, model: str, exc: Exception) -> tuple[int | None, str]:
    status, body = _error_details(exc)
    # Avoid logging exception objects directly: SDK exception strings may contain secrets.
    trace = _redact(traceback.format_exc())
    log.error("Provider %s model %s failed: type=%s message=%s HTTP status=%s body=%s\n%s",
              provider, model, type(exc).__name__, _safe(exc), status, body or "<empty>", trace)
    return status, body


def extract_json(raw: str) -> dict:
    """Extract a JSON object and reject provider error envelopes even on HTTP 200."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if "error" in value and ("choices" not in value or len(value) == 1):
                raise ValueError("Provider returned an error object instead of generated JSON")
            return value
    raise ValueError("Model returned no valid JSON object")


def parse_gemini_response(response: Any) -> str:
    """Safely extract Gemini text; SDK responses may have no text/candidates."""
    if response is None:
        raise ValueError("Gemini returned no response")
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        # A JSON error envelope with status 200 is not a successful generation.
        if text.lstrip().startswith("{"):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "error" in obj:
                    raise ValueError("Gemini returned an error object")
            except json.JSONDecodeError:
                pass
        extract_json(text)
        return text
    candidates = getattr(response, "candidates", None)
    if candidates:
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                joined = "".join(str(getattr(part, "text", "") or "") for part in parts)
                if joined.strip():
                    extract_json(joined)
                    return joined
    raise ValueError("Gemini response had no text content")


def parse_openai_response(response: Any) -> str:
    """Safely extract OpenAI-compatible choices/content and detect error envelopes."""
    if response is None:
        raise ValueError("OpenAI-compatible provider returned no response")
    # Accommodate raw HTTP-style JSON dictionaries in tests and some adapters.
    if isinstance(response, dict):
        if response.get("error") is not None:
            raise ValueError("Provider returned an error object")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Provider response is missing choices")
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
    else:
        error = getattr(response, "error", None)
        if error:
            raise ValueError("Provider returned an error object")
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("Provider response is missing choices")
        message = getattr(choices[0], "message", None) if choices[0] is not None else None
        content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Provider response has empty or non-text content")
    if content.lstrip().startswith("{"):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict) and "error" in obj:
                raise ValueError("Provider returned an error object")
        except json.JSONDecodeError:
            pass
    extract_json(content)
    return content


def _groq_models(api_key: str, timeout: float) -> set[str]:
    """Check Groq's live model catalogue before trying a configured model."""
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, 6.0)) as response:
            payload = json.loads(response.read(_BODY_LIMIT * 8).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = _safe(exc.read(_BODY_LIMIT).decode("utf-8", errors="replace"))
        raise ProviderHTTPError(exc.code, body, "Groq model catalogue request failed") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Groq model catalogue response is malformed")
    return {item["id"] for item in payload["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)}


def provider_call(provider: dict, model: str, system: str, user: str) -> str:
    timeout = float(config.PROVIDER_TIMEOUT_SECONDS)
    if provider['type'] == 'gemini':
        from google import genai
        from google.genai import types
        # google-genai 1.x API: Client.models.generate_content(..., config=GenerateContentConfig).
        with genai.Client(api_key=os.environ[provider['env_key']],
                          http_options=types.HttpOptions(timeout=int(timeout * 1000))) as client:
            response = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system, response_mime_type='application/json',
                    temperature=0.8))
            return parse_gemini_response(response)

    from openai import OpenAI
    with OpenAI(base_url=provider['base_url'], api_key=os.environ[provider['env_key']],
                timeout=timeout, max_retries=0) as client:
        kwargs = dict(model=model, messages=[{'role':'system','content':system},
                      {'role':'user','content':user}], temperature=0.8)
        if provider.get('json_mode'):
            kwargs['response_format'] = {'type':'json_object'}
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            status, _ = _error_details(exc)
            # gpt-oss may reject OpenAI JSON mode. This one narrowly-scoped 400 retry
            # disables response_format and makes the JSON-only instruction explicit.
            message = _safe(exc).lower()
            if (provider.get('json_mode') and status == 400 and
                    ('response_format' in message or 'json mode' in message or 'json_object' in message)):
                fallback = dict(kwargs)
                fallback.pop('response_format', None)
                fallback['messages'] = [
                    {'role':'system','content':system + "\nReturn only one valid JSON object; no markdown."},
                    {'role':'user','content':user + "\nReturn only one valid JSON object; no markdown."},
                ]
                response = client.chat.completions.create(**fallback)
            else:
                raise
        return parse_openai_response(response)


class JsonLLM:
    """Validate responses; retry only rate limits/transient server errors."""
    def __init__(self, call: Callable = provider_call, sleep: Callable = time.sleep,
                 providers: list[dict] | None = None, min_interval: float = 0.0,
                 cache_dir: Path | None = None):
        self.cache_dir = cache_dir
        self.call, self.sleep = call, sleep
        self.providers = config.PROVIDERS if providers is None else providers
        self.min_interval = min_interval
        self.last_call: float | None = None
        self._groq_models_cache: set[str] | None = None

    def _model_available(self, provider: dict, model: str) -> bool:
        if provider.get("name") != "groq" or not provider.get("verify_models"):
            return True
        if self._groq_models_cache is None:
            self._groq_models_cache = _groq_models(
                os.environ[provider["env_key"]], float(config.PROVIDER_TIMEOUT_SECONDS))
        if model not in self._groq_models_cache:
            log.warning("Skipping Groq model %s: absent from live /models catalogue", model)
            return False
        return True

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
        errors: list[str] = []
        deadline = time.monotonic() + float(config.PROVIDER_TOTAL_BUDGET_SECONDS)
        for provider in eligible:
            for model in provider["models"]:
                if time.monotonic() >= deadline:
                    raise RuntimeError("LLM stage exceeded its configured time budget")
                try:
                    if not self._model_available(provider, model):
                        continue
                except Exception as exc:
                    status, body = _log_failure(provider["name"], "model-catalogue", exc)
                    errors.append(f"{provider['name']}/model-catalogue: {type(exc).__name__} HTTP {status}")
                    if status not in (429, 500, 502, 503, 504):
                        continue
                    continue
                correction = ""
                corrected_prompt = False
                attempts = 0
                while attempts <= int(config.MAX_RETRIES_PER_MODEL):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("LLM stage exceeded its 55-second time budget")
                    if self.last_call is not None and self.min_interval:
                        self.sleep(min(self.min_interval, max(0, deadline - time.monotonic())))
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
                        status, body = _log_failure(provider["name"], model, exc)
                        errors.append(f"{provider['name']}/{model}: {type(exc).__name__} HTTP {status}")
                        # Schema/JSON validation gets one prompt correction, not an API retry.
                        if isinstance(exc, (ValueError, KeyError, TypeError)) and status is None:
                            if not corrected_prompt:
                                correction = "\nReturn only a valid JSON object matching the requested schema. Correct the prior response."
                                corrected_prompt = True
                                continue
                            break
                        if status not in (429, 500, 502, 503, 504):
                            break
                        if attempts >= int(config.MAX_RETRIES_PER_MODEL):
                            break
                        delay = min(2.0, 0.5 * (attempts + 1) + random.uniform(0, 0.2),
                                    max(0, deadline - time.monotonic()))
                        if delay <= 0:
                            break
                        self.sleep(delay)
                        attempts += 1
        raise RuntimeError("All script providers failed: " + "; ".join(errors))
