import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ferry.assess import (
    AssessError,
    check_report,
    estimate_tokens,
    render_markdown,
    run_assess,
    trim_facts,
    write_report,
)
from ferry.cli import app
from ferry.config import ModelConfig
from ferry.gateway import Completion, Usage

MODEL = ModelConfig(
    name="lite",
    provider="gemini",
    id="gemini-3.5-flash-lite",
    input_usd_per_mtok=0.30,
    output_usd_per_mtok=2.50,
    supports_temperature=True,
)


def make_facts(extra_hits: int = 0) -> dict:
    risks = [
        {
            "rule_id": "system-web",
            "kind": "using",
            "project": "Web/Web.csproj",
            "file": "Web/Home.cs",
            "line": 3,
            "match": "System.Web",
            "text": "using System.Web;",
        },
        {
            "rule_id": "wpf",
            "kind": "using",
            "project": "Desktop/Desktop.csproj",
            "file": "Desktop/Main.cs",
            "line": 2,
            "match": "System.Windows",
            "text": "using System.Windows;",
        },
    ]
    for index in range(extra_hits):
        risks.append(
            {
                "rule_id": "system-web",
                "kind": "using",
                "project": "Web/Web.csproj",
                "file": f"Web/File{index}.cs",
                "line": 10 + index,
                "match": "System.Web",
                "text": "using System.Web; // " + "x" * 80,
            }
        )
    return {
        "repo": "sample",
        "projects": [
            {
                "name": "Web",
                "path": "Web/Web.csproj",
                "sdk_style": False,
                "target_frameworks": ["net48"],
                "packages": [{"id": "Newtonsoft.Json", "version": "13.0.1"}],
                "project_references": [],
                "source_file_count": 4,
            },
            {
                "name": "Desktop",
                "path": "Desktop/Desktop.csproj",
                "sdk_style": True,
                "output_type": "WinExe",
                "target_frameworks": ["net48"],
                "packages": [],
                "project_references": [],
                "source_file_count": 1,
            },
        ],
        "risks": risks,
        "risk_summary": [
            {
                "rule_id": "system-web",
                "category": "asp.net",
                "severity": "high",
                "description": "System.Web is gone.",
                "count": 1 + extra_hits,
            }
        ],
    }


def good_report() -> dict:
    return {
        "size": "M",
        "size_reason": "Two projects, one high risk.",
        "summary": "Web needs ASP.NET Core.",
        "projects": [{"name": "Web", "current_framework": "net48", "notes": "Port to Core."}],
        "findings": [
            {
                "project": "Web",
                "rule_id": "system-web",
                "file": "Web/Home.cs",
                "line": 3,
                "issue": "System.Web does not exist.",
                "fix": "Use Microsoft.AspNetCore.Http.",
            }
        ],
    }


def bad_report() -> dict:
    report = good_report()
    report["findings"][0]["line"] = 999
    return report


def completion(data: dict, *, cost: float = 0.001) -> Completion:
    return Completion(
        model="lite",
        provider="gemini",
        model_id=MODEL.id,
        data=data,
        usage=Usage(input_tokens=100, output_tokens=50, elapsed_s=0.5, cost_usd=cost),
    )


class FakeModel:
    """Free, repeatable stand-in for the gateway. Replays scripted answers."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, model, prompt, schema, **kwargs) -> Completion:
        self.prompts.append(prompt)
        return completion(self.answers.pop(0))


def test_trim_keeps_everything_when_it_fits() -> None:
    trimmed = trim_facts(make_facts(), max_input_tokens=30000)

    assert trimmed.level == 0
    assert trimmed.risks_shown == trimmed.risks_total == 2
    assert trimmed.facts["risks"][0]["project"] == "Web"
    assert trimmed.facts["risks"][0]["text"] == "using System.Web;"


def test_trim_cuts_text_then_hits_until_it_fits() -> None:
    facts = make_facts(extra_hits=200)

    trimmed = trim_facts(facts, max_input_tokens=1500)

    assert trimmed.level > 0
    assert trimmed.risks_shown < trimmed.risks_total
    assert trimmed.facts["risk_summary"] == facts["risk_summary"]
    assert all("text" not in hit for hit in trimmed.facts["risks"])


def test_trim_raises_when_nothing_fits() -> None:
    with pytest.raises(AssessError, match="max_input_tokens"):
        trim_facts(make_facts(), max_input_tokens=10)


def test_estimate_tokens_grows_with_text() -> None:
    assert estimate_tokens("a" * 300) > estimate_tokens("a" * 30)


def test_check_accepts_a_report_that_matches_the_facts() -> None:
    assert check_report(good_report(), make_facts()) == []


def test_check_rejects_invented_line_project_and_size() -> None:
    report = good_report()
    report["size"] = "HUGE"
    report["projects"].append({"name": "Ghost", "current_framework": "net48", "notes": ""})
    report["findings"].append({**report["findings"][0], "project": "Phantom"})
    report["findings"].append({**report["findings"][0], "line": 999})

    errors = check_report(report, make_facts())

    assert any("HUGE" in error for error in errors)
    assert any("Ghost" in error for error in errors)
    assert any("Phantom" in error for error in errors)
    assert any("Home.cs:999" in error for error in errors)


def test_check_accepts_a_real_hit_that_was_trimmed_from_the_prompt() -> None:
    facts = make_facts(extra_hits=5)
    report = good_report()
    report["findings"][0]["file"] = "Web/File4.cs"
    report["findings"][0]["line"] = 14

    assert check_report(report, facts) == []


def test_assess_passes_first_time_with_one_call() -> None:
    fake = FakeModel(good_report())

    result = run_assess(
        make_facts(), MODEL, max_input_tokens=30000, complete_fn=fake, log_path=None
    )

    assert result.passed
    assert result.attempts == 1
    assert len(fake.prompts) == 1
    assert "Web/Home.cs" in fake.prompts[0]


def test_assess_shows_mistakes_to_the_model_and_asks_once_more() -> None:
    fake = FakeModel(bad_report(), good_report())

    result = run_assess(make_facts(), MODEL, max_input_tokens=30000, complete_fn=fake)

    assert result.passed
    assert result.attempts == 2
    assert "Home.cs:999" in fake.prompts[1]
    assert "previous answer had these mistakes" in fake.prompts[1]
    assert result.input_tokens == 200
    assert result.output_tokens == 100
    assert result.cost_usd == pytest.approx(0.002)


def test_assess_gives_up_after_two_attempts_and_keeps_the_errors() -> None:
    fake = FakeModel(bad_report(), bad_report())

    result = run_assess(make_facts(), MODEL, max_input_tokens=30000, complete_fn=fake)

    assert not result.passed
    assert result.attempts == 2
    assert result.errors


def test_write_report_saves_json_and_markdown(tmp_path: Path) -> None:
    fake = FakeModel(good_report())
    result = run_assess(make_facts(), MODEL, max_input_tokens=30000, complete_fn=fake)

    json_path, md_path = write_report(result, tmp_path)

    saved = json.loads(json_path.read_text())
    assert saved["checks"]["passed"] is True
    assert saved["usage"]["input_tokens"] == 100
    assert saved["report"]["size"] == "M"
    markdown = md_path.read_text()
    assert markdown == render_markdown(result)
    assert "**Size: M**" in markdown
    assert "`Web/Home.cs:3`" in markdown
    assert "list cost $0.001000" in markdown


def _patch_cli(monkeypatch: pytest.MonkeyPatch, fake: FakeModel) -> None:
    async def fake_inventory(repo, risks_path=None):
        return make_facts()

    monkeypatch.setattr("ferry.cli.get_inventory", fake_inventory)
    monkeypatch.setattr("ferry.cli.complete", fake)


def test_assess_command_saves_report_and_prints_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(monkeypatch, FakeModel(good_report()))

    result = CliRunner().invoke(app, ["assess", str(tmp_path), "--out", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    assert "size=M findings=1" in result.stdout
    assert "checks=passed" in result.stdout
    assert "list_cost=$0.001000" in result.stdout
    assert (tmp_path / "out" / "sample-lite.json").is_file()
    assert (tmp_path / "out" / "sample-lite.md").is_file()


def test_assess_command_exits_1_when_the_check_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(monkeypatch, FakeModel(bad_report(), bad_report()))

    result = CliRunner().invoke(app, ["assess", str(tmp_path), "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "checks=FAILED" in result.stdout
    assert (tmp_path / "out" / "sample-lite.json").is_file()


def test_assess_command_rejects_unknown_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(monkeypatch, FakeModel(good_report()))

    result = CliRunner().invoke(app, ["assess", str(tmp_path), "--model", "nope"])

    assert result.exit_code == 2
