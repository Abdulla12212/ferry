"""Turn repo facts into a checked migration report.

Order: shorten the facts to fit the input limit, ask the model for a report in a fixed
JSON shape, check the answer against the facts, and ask once more if the check fails.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ferry.config import ModelConfig
from ferry.gateway import Completion

SIZES = ("S", "M", "L", "XL")
MAX_ATTEMPTS = 2

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "size": {"type": "string", "enum": list(SIZES), "description": "Overall job size."},
        "size_reason": {"type": "string", "description": "One or two sentences on why."},
        "summary": {"type": "string", "description": "Short plain-language overview."},
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name from the facts."},
                    "current_framework": {"type": "string"},
                    "notes": {"type": "string", "description": "What must change here."},
                },
                "required": ["name", "current_framework", "notes"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name from the facts."},
                    "rule_id": {"type": "string", "description": "rule_id of the risk."},
                    "file": {"type": "string", "description": "file of the risk, copied exactly."},
                    "line": {"type": "integer", "description": "line of the risk, copied exactly."},
                    "issue": {"type": "string", "description": "What breaks on .NET 10."},
                    "fix": {"type": "string", "description": "What to do instead."},
                },
                "required": ["project", "rule_id", "file", "line", "issue", "fix"],
            },
        },
    },
    "required": ["size", "size_reason", "summary", "projects", "findings"],
}

# Each step keeps less of the risk list. The summary counts always stay.
TRIM_LEVELS: tuple[dict[str, Any], ...] = (
    {"per_rule": None, "text": True, "packages": None},
    {"per_rule": None, "text": False, "packages": None},
    {"per_rule": 20, "text": False, "packages": None},
    {"per_rule": 5, "text": False, "packages": None},
    {"per_rule": 1, "text": False, "packages": 30},
)

CompleteFn = Callable[..., Completion]


class AssessError(Exception):
    """The facts cannot be assessed (for example, they do not fit the input limit)."""


@dataclass(frozen=True)
class Trimmed:
    """The facts sent to the model, and how much was cut."""

    facts: dict[str, Any]
    level: int
    risks_total: int
    risks_shown: int


@dataclass
class AssessResult:
    repo: str
    model: str
    model_id: str
    report: dict[str, Any]
    errors: list[str]
    attempts: int
    trim_level: int
    risks_total: int
    risks_shown: int
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_s: float = 0.0
    cost_usd: float = 0.0
    calls: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def estimate_tokens(text: str) -> int:
    """Rough count (about 3 characters per token). Errs on the high side."""
    return len(text) // 3 + 1


def trim_facts(facts: dict[str, Any], max_input_tokens: int) -> Trimmed:
    """Cut the facts step by step until the prompt fits `max_input_tokens`."""
    names = {project["path"]: project["name"] for project in facts.get("projects", [])}
    hits = facts.get("risks", [])
    for level, step in enumerate(TRIM_LEVELS):
        shown = _cap_per_rule(hits, step["per_rule"])
        candidate = {
            "repo": facts.get("repo", ""),
            "projects": [_project_view(p, step["packages"]) for p in facts.get("projects", [])],
            "risks": [_hit_view(hit, names, step["text"]) for hit in shown],
            "risk_summary": facts.get("risk_summary", []),
            "risks_total": len(hits),
            "risks_shown": len(shown),
        }
        if estimate_tokens(build_prompt(candidate)) <= max_input_tokens:
            return Trimmed(candidate, level, len(hits), len(shown))
    raise AssessError(
        f"facts do not fit max_input_tokens={max_input_tokens} even after trimming; "
        "raise it in ferry.toml"
    )


def _cap_per_rule(hits: list[dict[str, Any]], cap: int | None) -> list[dict[str, Any]]:
    if cap is None:
        return list(hits)
    seen: dict[str, int] = {}
    kept = []
    for hit in hits:
        rule = hit["rule_id"]
        if seen.get(rule, 0) < cap:
            seen[rule] = seen.get(rule, 0) + 1
            kept.append(hit)
    return kept


def _project_view(project: dict[str, Any], package_cap: int | None) -> dict[str, Any]:
    packages = project.get("packages", [])
    view = {
        "name": project["name"],
        "sdk_style": project.get("sdk_style"),
        "output_type": project.get("output_type"),
        "target_frameworks": project.get("target_frameworks", []),
        "project_references": project.get("project_references", []),
        "source_file_count": project.get("source_file_count"),
        "packages": packages if package_cap is None else packages[:package_cap],
    }
    if package_cap is not None and len(packages) > package_cap:
        view["packages_omitted"] = len(packages) - package_cap
    return {key: value for key, value in view.items() if value is not None}


def _hit_view(hit: dict[str, Any], names: dict[str, str], keep_text: bool) -> dict[str, Any]:
    view = {
        "rule_id": hit["rule_id"],
        "project": names.get(hit["project"], hit["project"]),
        "file": hit["file"],
        "line": hit["line"],
    }
    if hit.get("count") is not None:
        view["count"] = hit["count"]
    if keep_text and hit.get("text"):
        view["text"] = hit["text"]
    return view


def build_prompt(facts: dict[str, Any]) -> str:
    return (
        "You assess an old .NET Framework repo for a move to .NET 10.\n"
        "Facts below come from a code scanner. Use only these facts.\n\n"
        "Rules:\n"
        "- Cite a risk only by copying its project, rule_id, file and line exactly from `risks`.\n"
        "- Name projects only as they appear in `projects`.\n"
        "- Group repeated hits in one file into one finding. At most 30 findings.\n"
        "- If risks_shown is below risks_total, the list was cut; use risk_summary for totals.\n"
        "- size: S = one small project, few risks. M = a few projects or some high risks. "
        "L = many projects or many high risks (System.Web, WCF). "
        "XL = large, mixed, or Windows-only UI plus web.\n\n"
        f"Facts:\n{json.dumps(facts, separators=(',', ':'))}"
    )


def check_report(report: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Return the mistakes in `report`. Empty list means it passed.

    `facts` is the full inventory, not the trimmed copy, so a real risk that was cut
    from the prompt is still a real file and line.
    """
    errors: list[str] = []
    if report.get("size") not in SIZES:
        errors.append(f"size {report.get('size')!r} is not one of {', '.join(SIZES)}")

    paths = {project["name"]: project["path"] for project in facts.get("projects", [])}
    real_hits = {
        (hit["project"], hit["file"], hit["line"], hit["rule_id"]) for hit in facts.get("risks", [])
    }

    for item in report.get("projects", []):
        name = item.get("name") if isinstance(item, dict) else None
        if name not in paths:
            errors.append(f"projects: {name!r} is not a project in the facts")

    for item in report.get("findings", []):
        if not isinstance(item, dict):
            errors.append("findings: an entry is not an object")
            continue
        project = item.get("project")
        if project not in paths:
            errors.append(f"finding cites project {project!r}, which is not in the facts")
            continue
        key = (paths[project], item.get("file"), item.get("line"), item.get("rule_id"))
        if key not in real_hits:
            errors.append(
                f"finding {item.get('rule_id')!r} cites {item.get('file')}:{item.get('line')} "
                f"in {project}, which is not in the risks list"
            )
    return errors


def run_assess(
    facts: dict[str, Any],
    model: ModelConfig,
    *,
    max_input_tokens: int,
    complete_fn: CompleteFn,
    temperature: float = 0,
    log_path: Path | None = None,
) -> AssessResult:
    """Trim, ask, check, and ask once more if the check fails."""
    trimmed = trim_facts(facts, max_input_tokens)
    base_prompt = build_prompt(trimmed.facts)
    prompt = base_prompt
    result = AssessResult(
        repo=str(facts.get("repo", "")),
        model=model.name,
        model_id=model.id,
        report={},
        errors=[],
        attempts=0,
        trim_level=trimmed.level,
        risks_total=trimmed.risks_total,
        risks_shown=trimmed.risks_shown,
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        completion = complete_fn(
            model,
            prompt,
            REPORT_SCHEMA,
            temperature=temperature,
            log_path=log_path,
            max_output_tokens=8192,
        )
        result.attempts = attempt
        result.report = completion.data
        result.input_tokens += completion.usage.input_tokens
        result.output_tokens += completion.usage.output_tokens
        result.elapsed_s += completion.usage.elapsed_s
        result.cost_usd += completion.usage.cost_usd
        result.errors = check_report(completion.data, facts)
        if not result.errors:
            break
        listed = "\n".join(f"- {error}" for error in result.errors[:20])
        prompt = (
            f"{base_prompt}\n\nYour previous answer had these mistakes:\n{listed}\n"
            "Answer again. Fix every mistake. Use only projects, files and lines from the facts."
        )
    return result


def render_markdown(result: AssessResult) -> str:
    report = result.report
    lines = [
        f"# Migration assessment: {result.repo}",
        "",
        f"**Size: {report.get('size', '?')}**. {report.get('size_reason', '')}",
        "",
        str(report.get("summary", "")),
        "",
        "## Projects",
        "",
    ]
    for project in report.get("projects", []):
        lines.append(
            f"- **{project.get('name')}** ({project.get('current_framework')}): "
            f"{project.get('notes')}"
        )
    lines += ["", "## Findings", ""]
    for item in report.get("findings", []):
        lines.append(
            f"- `{item.get('file')}:{item.get('line')}` [{item.get('rule_id')}] "
            f"in {item.get('project')}: {item.get('issue')} Fix: {item.get('fix')}"
        )
    lines += ["", "## Checks", ""]
    if result.passed:
        lines.append(f"Passed after {result.attempts} attempt(s).")
    else:
        lines.append(f"FAILED after {result.attempts} attempt(s):")
        lines += [f"- {error}" for error in result.errors]
    if result.risks_shown < result.risks_total:
        lines.append(
            f"The model saw {result.risks_shown} of {result.risks_total} risk hits "
            f"(trim level {result.trim_level})."
        )
    lines += [
        "",
        "## Cost",
        "",
        f"Model {result.model} ({result.model_id}): {result.input_tokens} input tokens, "
        f"{result.output_tokens} output tokens, {result.elapsed_s:.2f}s, "
        f"list cost ${result.cost_usd:.6f}.",
        "",
    ]
    return "\n".join(lines)


def write_report(result: AssessResult, out_dir: Path) -> tuple[Path, Path]:
    """Save `<repo>-<model>.json` and `.md` in `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(result.repo).name or 'repo'}-{result.model}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    document = {
        "repo": result.repo,
        "model": result.model,
        "model_id": result.model_id,
        "checks": {"passed": result.passed, "attempts": result.attempts, "errors": result.errors},
        "trim": {
            "level": result.trim_level,
            "risks_total": result.risks_total,
            "risks_shown": result.risks_shown,
        },
        "usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "elapsed_s": round(result.elapsed_s, 3),
            "list_cost_usd": result.cost_usd,
        },
        "report": result.report,
    }
    json_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path
