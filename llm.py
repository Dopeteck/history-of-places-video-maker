"""Bounded, validated JSON calls across Groq, Gemini, and OpenRouter."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import threading
import traceback
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


def _redact(value: Any) -> str:
    """Redact credentials without truncating diagnostics or tracebacks."""
    result = str(value)
    for key_name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(key_name)
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]", result)


def _safe(value: Any) -> str:
    """Redact and bound provider response bodies and messages."""
    return _redact(value)[:_BODY_LIMIT]


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


def _retry_after_seconds(message: str) -> float | None:
    """Extract a provider's stated retry delay, including decimal seconds."""
    match = re.search(r"try again in\s+(\d+(?:\.\d+)?)\s*(?:s|sec(?:onds?)?)", message, re.I)
    if not match:
        match = re.search(r"retry[- ]after\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:s|sec(?:onds?)?)?", message, re.I)
    if not match:
        return None
    try:
        return min(20.0, max(0.0, float(match.group(1)) + 1.0))
    except (TypeError, ValueError, OverflowError):
        return None


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


def _prompt_object(user: str) -> dict:
    try:
        value = extract_json(user)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _stage_from_user(user: str) -> str:
    task = _prompt_object(user).get("task", "unknown")
    stage = re.sub(r"[^a-z0-9_-]+", "_", str(task).lower()).strip("_-")
    return stage[:40] or "unknown"


def _json_schema_for_stage(stage: str) -> dict | None:
    """Strict JSON Schema for the two narrator-story generation stages."""
    string = {"type": "string"}
    def array(item: dict) -> dict:
        return {"type": "array", "items": item}
    def obj(properties: dict) -> dict:
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}
    if stage == "outline":
        character = obj({
            "id": string, "name": string, "role": string, "trait": string,
            "accessory": {"type": "string", "enum": [
                "crown", "glasses", "beard", "headwrap", "spear", "bow_tie", "sash", "hat", "none"]},
            "accent": {"type": "string", "enum": ["#1FA2A6", "#E0A526", "#C0392B", "#2B2B2B"]},
        })
        chapter = obj({"title": string, "purpose": string, "key_events": array(string)})
        return obj({
            "title": string, "hook": string, "logline": string,
            "cast": array(character), "chapters": array(chapter), "ending": string,
        })
    if stage in {"chapter", "chapter_extend"}:
        actor = obj({
            "id": string,
            "x": {"type": "number"},
            "pose": {"type": "string", "enum": sorted([
                "standing", "talking", "pointing", "shocked", "running", "kneeling",
                "fighting", "falling", "dead", "laughing", "thinking", "praying"])},
            "expression": {"type": "string", "enum": sorted([
                "neutral", "happy", "angry", "shocked", "sad", "sly", "dead"])},
        })
        action_state = obj({
            "id": string,
            "action": {"type": "string", "enum": sorted([
                "idle", "talk", "walk", "run", "point", "cheer", "shrug", "think",
                "crouch", "punch", "kick", "carry", "hold", "facepalm", "sit",
                "handshake", "sad", "shock", "fall", "kneel", "laugh", "dead"])},
            "x": {"type": "number", "minimum": 0.1, "maximum": 0.9},
            "emotion": {"type": "string", "enum": sorted([
                "neutral", "happy", "angry", "shocked", "sad", "sly", "dead"])},
        })
        action_beat = obj({
            "at": {"type": "number", "minimum": 0, "maximum": 1},
            "actions": array(action_state),
        })
        scene = obj({
            "narration": string,
            "characters_on_screen": array(actor),
            "action_beats": array(action_beat),
            "background": {"type": "string", "enum": sorted([
                "hut", "throne", "palace", "river", "market", "battlefield", "street", "room", "map"])},
            "camera": {"type": "string", "enum": sorted(["static", "push_in", "pan_left", "pan_right"])},
            "effects": array({"type": "string", "enum": sorted(["shake", "fade", "cut", "pop_in"])}),
            "props": array({"type": "string", "enum": sorted(["crown", "scroll", "torch", "fire", "coin", "sword", "spear"])}),
            "emphasis_words": array(string),
            "source_ids": array(string),
            "uncertainty": {"type": "string", "enum": [
                "documented", "disputed", "legend", "unverified", "medium"]},
        })
        return obj({"summary": string, "scenes": array(scene)})
    return None


def _to_gemini_schema(schema: dict) -> dict:
    """Convert conventional JSON Schema type names to google-genai Schema type names."""
    result: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            result[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            result[key] = {name: _to_gemini_schema(child) for name, child in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _to_gemini_schema(value)
        elif key in {"enum", "required", "description", "format", "nullable"}:
            result[key] = value
        # Gemini's schema endpoint does not need JSON Schema's additionalProperties.
    return result


def _redacted_debug_text(value: Any, limit: int = 12000) -> str:
    result = _redact(value)
    result = re.sub(
        r"(?i)([\"']?(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|secret)[\"']?\s*[:=]\s*[\"']?)([^\s,\"'}\]]+)",
        r"\1[REDACTED]", result)
    result = re.sub(
        r"(?i)\b(?:gsk_[A-Za-z0-9_-]{12,}|sk-or-v1-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{25,})\b",
        "[REDACTED]", result)
    return result[:limit] + ("\n[TRUNCATED]" if len(result) > limit else "")


_DEBUG_LOCK = threading.Lock()


def _write_debug_response(stage: str, provider: str, content: Any) -> str | None:
    """Persist a bounded, redacted failed response; debug write failures never mask the cause."""
    safe_stage = re.sub(r"[^a-z0-9_-]+", "_", str(stage).lower()).strip("_-")[:40] or "unknown"
    safe_provider = re.sub(r"[^a-z0-9_-]+", "_", str(provider).lower()).strip("_-")[:40] or "provider"
    if content is None or content == "":
        content = "<empty model response / provider error body>"
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            content = str(content)
    payload = _redacted_debug_text(content)
    try:
        debug_dir = Path("debug")
        with _DEBUG_LOCK:
            debug_dir.mkdir(parents=True, exist_ok=True)
            index = 1
            while True:
                path = debug_dir / f"{safe_stage}_{safe_provider}_{index}.txt"
                try:
                    with path.open("x", encoding="utf-8") as handle:
                        handle.write(payload)
                    return str(path)
                except FileExistsError:
                    index += 1
    except Exception as exc:
        log.warning("Could not write redacted model debug response: %s", _safe(exc))
        return None


def _response_content(response: Any, provider_type: str) -> str:
    if provider_type == "gemini":
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        candidates = getattr(response, "candidates", None) or []
        parts_text = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str):
                    parts_text.append(part_text)
        return "\n".join(parts_text)
    try:
        if isinstance(response, dict):
            return str(response.get("choices", [{}])[0].get("message", {}).get("content") or "")
        choices = getattr(response, "choices", None) or []
        return str(getattr(getattr(choices[0], "message", None), "content", "") or "") if choices else ""
    except (IndexError, AttributeError, TypeError):
        return ""


def parse_gemini_response(response: Any) -> str:
    """Safely extract Gemini text; SDK responses may have no text/candidates."""
    if response is None:
        raise ValueError("Gemini returned no response")
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
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


def provider_call(provider: dict, model: str, system: str, user: str) -> str:
    timeout = float(config.PROVIDER_TIMEOUT_SECONDS)
    stage = _stage_from_user(user)
    provider_name = str(provider.get("name", "provider"))
    if provider.get("type") == "gemini":
        from google import genai
        from google.genai import types
        schema = _json_schema_for_stage(stage)
        client = genai.Client(
            api_key=os.environ[provider["env_key"]],
            http_options=types.HttpOptions(timeout=int(float(config.PROVIDER_TIMEOUT_SECONDS) * 1000)),
        )
        try:
            config_args = {
                "system_instruction": system,
                "response_mime_type": "application/json",
                "temperature": 0.8,
            }
            if schema is not None:
                config_args["response_schema"] = _to_gemini_schema(schema)
            response = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(**config_args))
            try:
                return parse_gemini_response(response)
            except Exception as exc:
                setattr(exc, "raw_model_response", _response_content(response, "gemini"))
                raise
        except Exception as exc:
            if not getattr(exc, "raw_model_response", None):
                status, body = _error_details(exc)
                _write_debug_response(stage, provider_name, body or _safe(exc))
                setattr(exc, "debug_response_saved", True)
            raise
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    log.warning("Gemini client cleanup failed", exc_info=True)

    from openai import OpenAI
    with OpenAI(base_url=provider["base_url"], api_key=os.environ[provider["env_key"]],
                timeout=timeout, max_retries=0) as client:
        kwargs = dict(model=model, messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}], temperature=0.8)
        is_groq_gpt_oss = provider_name == "groq" and model in {
            "openai/gpt-oss-120b", "openai/gpt-oss-20b"}
        schema = _json_schema_for_stage(stage)
        if is_groq_gpt_oss:
            # GPT-OSS spends output budget on reasoning before the visible JSON.
            # Keep requested output reservations comfortably below the 8k TPM ceiling
            # once the compact prompt's input tokens are included.
            stage_token_limits = {
                "outline": 2200, "chapter": 3000, "chapter_extend": 2200,
                "claim_review": 1800, "suggest_topics": 1200,
            }
            kwargs["max_completion_tokens"] = stage_token_limits.get(stage, 1600)
            kwargs["reasoning_effort"] = "low"
            if schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": f"story_{stage}", "strict": True, "schema": schema},
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
        elif provider.get("json_mode"):
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            status, body = _error_details(exc)
            message = (_safe(exc) + " " + body).lower()
            json_validation_failed = is_groq_gpt_oss and status == 400 and (
                "json_validate_failed" in message or "failed_generation" in message)
            unsupported_format = status == 400 and (
                "response_format" in message or "json mode" in message or "json_object" in message)
            if json_validation_failed:
                _write_debug_response(stage, provider_name, body or _safe(exc))
            if json_validation_failed or (provider.get("json_mode") and unsupported_format):
                fallback = dict(kwargs)
                fallback.pop("response_format", None)
                fallback["messages"] = [
                    {"role": "system", "content": system + "\nReturn exactly one valid JSON object. No markdown."},
                    {"role": "user", "content": user + "\nReturn exactly one valid JSON object. No markdown."},
                ]
                response = client.chat.completions.create(**fallback)
            else:
                raise
        try:
            return parse_openai_response(response)
        except Exception as exc:
            raw_response = _response_content(response, "openai")
            setattr(exc, "raw_model_response", raw_response)
            raise


class JsonLLM:
    """Try configured providers in order and capture bounded diagnostics on failures."""
    def __init__(self, call: Callable = provider_call, sleep: Callable = time.sleep,
                 providers: list[dict] | None = None, min_interval: float = 10.0,
                 cache_dir: Path | None = None, draft_cache_dir: Path | None = None):
        self.cache_dir = cache_dir
        self.draft_cache_dir = draft_cache_dir
        self.call, self.sleep = call, sleep
        self.providers = config.PROVIDERS if providers is None else providers
        self.min_interval = min_interval
        self.last_call: float | None = None
        self.last_groq_call: float | None = None
        self.groq_stage_counter = 0

    def generate(self, system: str, prompt: dict, validate: Callable[[dict], Any]) -> Any:
        from story import digest
        from story_store import read_json, write_json
        cache = self.cache_dir / (digest({"system": system, "prompt": prompt}) + ".json") if self.cache_dir else None
        if cache and cache.exists():
            try:
                return validate(read_json(cache))
            except (ValueError, KeyError, TypeError):
                log.warning("Ignoring invalid cached model response")
        eligible = [p for p in self.providers if os.getenv(p["env_key"])]
        if not eligible:
            raise RuntimeError("No script API key: set GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY")
        stage = str(prompt.get("task", "unknown")) if isinstance(prompt, dict) else "unknown"
        errors: list[str] = []
        deadline = time.monotonic() + float(config.PROVIDER_TOTAL_BUDGET_SECONDS)
        for provider in eligible:
            models = list(provider["models"])
            if provider.get("name") == "groq" and len(models) > 1:
                offset = self.groq_stage_counter % len(models)
                models = models[offset:] + models[:offset]
                self.groq_stage_counter += 1
            for model in models:
                if time.monotonic() >= deadline:
                    raise RuntimeError("LLM stage exceeded its configured time budget")
                correction = ""
                corrected_prompt = False
                attempts = 0
                while attempts <= int(config.MAX_RETRIES_PER_MODEL):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("LLM stage exceeded its configured time budget")
                    if provider.get("name") == "groq" and self.last_groq_call is not None and self.min_interval:
                        wait_for = self.min_interval - (time.monotonic() - self.last_groq_call)
                        if wait_for > 0:
                            self.sleep(min(wait_for, max(0, deadline - time.monotonic())))
                    self.last_call = time.monotonic()
                    if provider.get("name") == "groq":
                        self.last_groq_call = self.last_call
                    raw_attempt: Any = None
                    try:
                        raw_attempt = self.call(
                            provider, model, system,
                            json.dumps(prompt, ensure_ascii=False) + correction)
                        data = extract_json(raw_attempt)
                        result = validate(data)
                        if cache:
                            write_json(cache, data)
                        return result
                    except Exception as exc:
                        status, body = _log_failure(provider["name"], model, exc)
                        raw_debug = getattr(exc, "raw_model_response", None)
                        if not raw_debug:
                            raw_debug = raw_attempt if raw_attempt is not None else (body or _safe(exc))
                        if not getattr(exc, "debug_response_saved", False):
                            _write_debug_response(stage, provider["name"], raw_debug)
                        errors.append(f"{provider['name']}/{model}: {type(exc).__name__} HTTP {status}")
                        if status in {400, 401, 403, 404}:
                            raise ProviderHTTPError(
                                status, body,
                                f"Non-retryable HTTP {status} from {provider['name']}/{model}; All script providers failed fast") from exc
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
                        if status == 429:
                            stated_delay = _retry_after_seconds(body + " " + _safe(exc))
                            delay = stated_delay if stated_delay is not None else min(2.0, 1.0 + random.uniform(0, 0.2))
                        else:
                            delay = min(2.0, 0.5 * (attempts + 1) + random.uniform(0, 0.2))
                        delay = min(float(delay), max(0.0, deadline - time.monotonic()))
                        if delay <= 0:
                            break
                        self.sleep(delay)
                        attempts += 1
        raise RuntimeError("All script providers failed: " + "; ".join(errors))


class DryRunLLM:
    """Deterministic, deliberately imperfect model substitute for offline end-to-end tests."""
    def __init__(self, draft_cache_dir: Path | None = None):
        self.draft_cache_dir = draft_cache_dir
        self.calls: list[dict] = []

    @staticmethod
    def _scene(narration: str) -> dict:
        return {
            "text": narration,
            "characters": {"character": "dry_narrator", "x": 0.5,
                           "pose": "talkng", "expression": "neutral"},
            "background": "palacc",
            "camera": "static",
            "effects": ["cut"],
            "props": [],
            "source_ids": [],
        }

    def generate(self, system: str, prompt: dict, validate: Callable[[dict], Any]) -> Any:
        task = str(prompt.get("task", "unknown"))
        self.calls.append({"task": task, "prompt": prompt})
        if task == "outline":
            count = int(prompt.get("count", 4))
            raw = {
                "title": "Offline Test: A Turning Point",
                "logline": "A compact, fictionalized dry-run demonstration of story structure.",
                "ending": "The lesson is to separate a memorable tale from verified history.",
                "characters": [{
                    "character_id": "dry_narrator", "character_name": "Test Narrator",
                    "function": "narrator", "personality": "curious",
                    "wearing": "headscarf", "colour": "teal",
                }],
                "chapter_list": [
                    {"heading": f"Offline chapter {number}",
                     "summary": "Follow one decision and its consequences.",
                     "events": ["A choice is made", "The consequences become visible"]}
                    for number in range(1, count + 1)
                ],
            }
        elif task == "chapter":
            raw = {
                "summary": "A choice creates consequences.",
                "scene_list": [
                    self._scene("The archive opens on a turning point, where ordinary people face a choice that gradually changes their community."),
                    self._scene("At first, the decision seems small, but its effects spread through the market and reach people far beyond it."),
                    self._scene("Witnesses remember different details, reminding the narrator that a confident story is not automatically a verified account."),
                    self._scene("The final scene pauses at the consequence, leaving room for the next chapter to explain what changed."),
                ],
            }
        elif task == "chapter_extend":
            # These are new scenes only; existing content remains untouched by the production code.
            raw = {
                "summary": "The decision's effects become clearer as the story widens.",
                "scenes": [
                    {"narration": "A messenger carries the news through crowded streets, while neighbors compare what they heard and begin to question the easy explanation offered at first. Small disagreements reveal how memory, interest, and incomplete evidence can shape a public account of the same event.", "characters_on_screen": [{"id": "dry_narrator", "x": 0.5, "pose": "talking", "expression": "thinking"}], "background": "street", "camera": "pan_left", "effects": ["cut"], "props": ["scroll"], "source_ids": [], "uncertainty": "medium"},
                    {"narration": "The next morning brings practical consequences: work is delayed, an agreement must be reconsidered, and people who were distant observers now have a reason to care about the outcome. The story advances through these visible changes rather than repeating the opening claim.", "characters_on_screen": [{"id": "dry_narrator", "x": 0.5, "pose": "talking", "expression": "neutral"}], "background": "market", "camera": "push_in", "effects": ["cut"], "props": ["coin"], "source_ids": [], "uncertainty": "medium"},
                    {"narration": "Later accounts preserve useful clues, but they do not settle every detail. A careful narrator names what is known, marks what remains uncertain, and resists adding invented dialogue simply to make the scene sound more dramatic.", "characters_on_screen": [{"id": "dry_narrator", "x": 0.5, "pose": "thinking", "expression": "neutral"}], "background": "room", "camera": "static", "effects": ["fade"], "props": ["scroll"], "source_ids": [], "uncertainty": "unverified"},
                    {"narration": "By the end, the added scenes connect the first decision to the wider consequences. The point is not that every question has an answer, but that a strong account can remain compelling while being honest about its limits.", "characters_on_screen": [{"id": "dry_narrator", "x": 0.5, "pose": "talking", "expression": "happy"}], "background": "map", "camera": "push_in", "effects": ["cut"], "props": [], "source_ids": [], "uncertainty": "medium"},
                ],
            }
        elif task == "claim_review":
            raw = {"scenes": [
                {"scene_id": scene["scene_id"], "claims": []}
                for scene in prompt.get("scenes", [])
            ]}
        elif task == "suggest_topics":
            count = int(prompt.get("count", 1))
            raw = {"topics": [
                {"topic": f"Offline sample topic {index}", "research_question": "Which primary sources support this topic?"}
                for index in range(1, count + 1)
            ]}
        else:
            raise ValueError(f"Dry-run has no canned reply for stage: {task}")
        return validate(raw)
