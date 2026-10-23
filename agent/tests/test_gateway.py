import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ferry.cli import app
from ferry.config import ModelConfig, load_assess_config, load_models
from ferry.gateway import Completion, GatewayError, Usage, complete, list_cost_usd

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["answer", "note"],
}
LITE = ModelConfig(
    name="lite",
    provider="gemini",
    id="gemini-3.5-flash-lite",
    input_usd_per_mtok=0.30,
    output_usd_per_mtok=2.50,
    supports_temperature=True,
)


def _payload(text: str, *, thoughts: int = 0) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": thoughts,
        },
    }


def test_list_cost_uses_published_rates() -> None:
    assert list_cost_usd(LITE, 1000, 2000) == pytest.approx(0.0053)


def test_complete_parses_json_and_appends_a_log_line(tmp_path: Path) -> None:
    seen: dict = {}

    def transport(url: str, headers: dict, body: dict) -> tuple[int, dict]:
        seen["url"] = url
        seen["body"] = body
        seen["has_key"] = bool(headers.get("x-goog-api-key"))
        return 200, _payload('{"answer": 4, "note": "ok"}', thoughts=5)

    log_path = tmp_path / "calls.jsonl"
    result = complete(
        LITE,
        "What is 2 + 2?",
        SCHEMA,
        api_key="test-key",
        log_path=log_path,
        transport=transport,
    )

    assert result.data == {"answer": 4, "note": "ok"}
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 25
    assert result.usage.cost_usd == pytest.approx(list_cost_usd(LITE, 1000, 25))
    assert seen["url"].endswith("/gemini-3.5-flash-lite:generateContent")
    assert seen["has_key"] is True
    assert seen["body"]["generationConfig"]["responseSchema"]["type"] == "OBJECT"
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    record = json.loads(log_path.read_text().strip())
    assert record["ok"] is True
    assert record["model"] == "lite"
    assert record["input_tokens"] == 1000
    assert record["output_tokens"] == 25
    assert "test-key" not in log_path.read_text()


def test_complete_skips_thought_parts(tmp_path: Path) -> None:
    def transport(url: str, headers: dict, body: dict) -> tuple[int, dict]:
        return 200, {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "scratch", "thought": True},
                            {"text": '{"answer": 4, "note": "ok"}'},
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
        }

    result = complete(
        LITE, "q", SCHEMA, api_key="k", log_path=tmp_path / "log.jsonl", transport=transport
    )

    assert result.data["answer"] == 4


def test_complete_retries_without_thinking_config(tmp_path: Path) -> None:
    calls: list[dict] = []

    def transport(url: str, headers: dict, body: dict) -> tuple[int, dict]:
        calls.append(body)
        if "thinkingConfig" in body["generationConfig"]:
            return 400, {"error": {"message": "thinking is not supported"}}
        return 200, _payload('{"answer": 4, "note": "ok"}')

    result = complete(
        LITE, "q", SCHEMA, api_key="k", log_path=tmp_path / "log.jsonl", transport=transport
    )

    assert result.data["answer"] == 4
    assert len(calls) == 2
    assert "thinkingConfig" not in calls[1]["generationConfig"]


def test_complete_logs_http_errors(tmp_path: Path) -> None:
    def transport(url: str, headers: dict, body: dict) -> tuple[int, dict]:
        return 429, {"error": {"message": "quota exceeded"}}

    log_path = tmp_path / "calls.jsonl"
    with pytest.raises(GatewayError, match="quota exceeded"):
        complete(LITE, "q", SCHEMA, api_key="k", log_path=log_path, transport=transport)

    record = json.loads(log_path.read_text().strip())
    assert record["ok"] is False
    assert record["error"] == "quota exceeded"


def test_complete_rejects_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("ferry.gateway.load_dotenv", lambda *args, **kwargs: None)

    with pytest.raises(GatewayError, match="GEMINI_API_KEY"):
        complete(
            LITE,
            "q",
            SCHEMA,
            log_path=tmp_path / "log.jsonl",
            transport=lambda *args: (200, {}),
        )


def test_complete_rejects_bad_json(tmp_path: Path) -> None:
    def transport(url: str, headers: dict, body: dict) -> tuple[int, dict]:
        return 200, _payload("not json")

    with pytest.raises(GatewayError, match="invalid JSON"):
        complete(
            LITE, "q", SCHEMA, api_key="k", log_path=tmp_path / "log.jsonl", transport=transport
        )


def test_repo_models_are_free_gemini() -> None:
    models = load_models(REPO_ROOT / "ferry.toml")
    assess = load_assess_config(REPO_ROOT / "ferry.toml")

    assert set(models) == {"flash", "lite"}
    assert {model.provider for model in models.values()} == {"gemini"}
    assert assess.default_model == "lite"
    assert "sonnet" not in models
    assert "cheap" not in models


def test_probe_prints_cost_for_every_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_complete(model, prompt, schema, **kwargs):
        return Completion(
            model=model.name,
            provider=model.provider,
            model_id=model.id,
            data={"answer": 4, "note": "ok"},
            usage=Usage(input_tokens=10, output_tokens=4, elapsed_s=0.2, cost_usd=0.000001),
        )

    monkeypatch.setattr("ferry.cli.complete", fake_complete)
    result = CliRunner().invoke(app, ["probe", "--log", str(tmp_path / "calls.jsonl")])

    assert result.exit_code == 0
    assert "flash" in result.stdout
    assert "lite" in result.stdout
    assert "list_cost=$0.000001" in result.stdout
    assert "answer=4" in result.stdout
