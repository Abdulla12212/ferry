"""Call a configured model for JSON, and append tokens, time, and cost to a log."""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ferry.config import ModelConfig, find_config, load_dotenv

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_SCHEMA_TYPES = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
}
_PYTHON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}

Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, dict[str, Any]]]


class GatewayError(Exception):
    """The model call failed, or the JSON did not match the schema."""


@dataclass(frozen=True)
class Usage:
    """What one call consumed."""

    input_tokens: int
    output_tokens: int
    elapsed_s: float
    cost_usd: float


@dataclass(frozen=True)
class Completion:
    """Structured answer plus the usage that produced it."""

    model: str
    provider: str
    model_id: str
    data: dict[str, Any]
    usage: Usage


def list_cost_usd(model: ModelConfig, input_tokens: int, output_tokens: int) -> float:
    """Published price for these tokens. The Gemini free tier bills $0."""
    return (
        input_tokens * model.input_usd_per_mtok + output_tokens * model.output_usd_per_mtok
    ) / 1_000_000


def default_log_path() -> Path:
    """`.ferry/calls.jsonl` next to ferry.toml."""
    return find_config().parent / ".ferry" / "calls.jsonl"


def complete(
    model: ModelConfig,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float = 0,
    api_key: str | None = None,
    log_path: Path | None = None,
    transport: Transport | None = None,
    max_output_tokens: int = 4096,
) -> Completion:
    """Ask `model` for JSON matching `schema`. Append one log line either way."""
    if model.provider != "gemini":
        raise GatewayError(f"unknown provider: {model.provider}")

    key = api_key or _gemini_key()
    log_file = log_path or default_log_path()
    started = time.perf_counter()
    usage: Usage | None = None
    error: str | None = None
    try:
        payload = _gemini_generate(
            model,
            prompt,
            schema,
            temperature=temperature,
            api_key=key,
            transport=transport or _post_json,
            max_output_tokens=max_output_tokens,
        )
        input_tokens, output_tokens = _token_counts(payload)
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_s=time.perf_counter() - started,
            cost_usd=list_cost_usd(model, input_tokens, output_tokens),
        )
        data = _parse_json(_answer_text(payload), schema)
        return Completion(
            model=model.name,
            provider=model.provider,
            model_id=model.id,
            data=data,
            usage=usage,
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        _append_log(log_file, model, usage, time.perf_counter() - started, error)


def _gemini_key() -> str:
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GatewayError("GEMINI_API_KEY is not set")
    return key


def _gemini_generate(
    model: ModelConfig,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float,
    api_key: str,
    transport: Transport,
    max_output_tokens: int,
    thinking: bool = True,
) -> dict[str, Any]:
    generation: dict[str, Any] = {
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
        "responseSchema": _to_gemini_schema(schema),
    }
    if model.supports_temperature:
        generation["temperature"] = temperature
    if thinking:
        generation["thinkingConfig"] = {"thinkingBudget": 0}

    url = f"{GEMINI_URL}/{model.id}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation,
    }
    status, payload = transport(
        url,
        {"Content-Type": "application/json", "x-goog-api-key": api_key},
        body,
    )
    if status >= 400:
        message = _error_message(status, payload)
        # 3.5 Flash-Lite rejects thinkingBudget with a generic 400, so retry once without it.
        if thinking and status == 400:
            return _gemini_generate(
                model,
                prompt,
                schema,
                temperature=temperature,
                api_key=api_key,
                transport=transport,
                max_output_tokens=max_output_tokens,
                thinking=False,
            )
        raise GatewayError(message)
    return payload


def _post_json(
    url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": {"message": raw[:500]}}
        return error.code, payload
    except urllib.error.URLError as error:
        raise GatewayError(f"could not reach Gemini: {error.reason}") from error


def _error_message(status: int, payload: dict[str, Any]) -> str:
    message = ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "")
    return message or f"Gemini request failed ({status})"


def _answer_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise GatewayError("Gemini returned no candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought")
    ).strip()
    if not text:
        raise GatewayError("Gemini returned no text")
    return text


def _token_counts(payload: dict[str, Any]) -> tuple[int, int]:
    meta = payload.get("usageMetadata") or {}
    input_tokens = int(meta.get("promptTokenCount") or 0)
    candidates = int(meta.get("candidatesTokenCount") or 0)
    thoughts = int(meta.get("thoughtsTokenCount") or 0)
    output_tokens = candidates + thoughts
    if output_tokens == 0 and meta.get("totalTokenCount") is not None:
        output_tokens = max(0, int(meta["totalTokenCount"]) - input_tokens)
    return input_tokens, output_tokens


def _parse_json(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GatewayError(f"model returned invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise GatewayError("model returned JSON that is not an object")
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in data:
            raise GatewayError(f"model JSON missing {key}")
        expected = properties.get(key) or {}
        _check_type(key, data[key], expected)
    return data


def _check_type(key: str, value: Any, schema: dict[str, Any]) -> None:
    raw = schema.get("type")
    if not isinstance(raw, str):
        return
    if raw == "integer" and isinstance(value, bool):
        raise GatewayError(f"model JSON field {key} is not an integer")
    if raw == "number" and isinstance(value, bool):
        raise GatewayError(f"model JSON field {key} is not a number")
    expected = _PYTHON_TYPES.get(raw)
    if expected is not None and not isinstance(value, expected):
        raise GatewayError(f"model JSON field {key} is not a {raw}")


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini responseSchema uses uppercase types and a subset of JSON Schema."""
    raw = schema.get("type")
    if not isinstance(raw, str) or raw not in _SCHEMA_TYPES:
        raise GatewayError(f"unsupported schema type: {raw!r}")
    converted: dict[str, Any] = {"type": _SCHEMA_TYPES[raw]}
    if description := schema.get("description"):
        converted["description"] = description
    if raw == "object":
        converted["properties"] = {
            name: _to_gemini_schema(child)
            for name, child in (schema.get("properties") or {}).items()
        }
        if schema.get("required"):
            converted["required"] = list(schema["required"])
    if raw == "array":
        converted["items"] = _to_gemini_schema(schema.get("items") or {"type": "string"})
    if "enum" in schema:
        converted["enum"] = list(schema["enum"])
    return converted


def _append_log(
    path: Path,
    model: ModelConfig,
    usage: Usage | None,
    elapsed_s: float,
    error: str | None,
) -> None:
    record = {
        "time": datetime.now(UTC).isoformat(),
        "model": model.name,
        "provider": model.provider,
        "model_id": model.id,
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "elapsed_s": round(usage.elapsed_s if usage else elapsed_s, 3),
        "cost_usd": usage.cost_usd if usage else 0,
        "ok": error is None,
    }
    if error:
        record["error"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")
