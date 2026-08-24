from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from skilllift_eval.schemas import SkillBundle
from skilllift_eval.skills.wildclaw_loader import WildClawBenchSkillLoader


ROOT = Path(__file__).resolve().parents[2]
TASK_PARSER_PATH = ROOT / "WildClawBench" / "src" / "utils" / "task_parser.py"


def load_task_parser():
    spec = importlib.util.spec_from_file_location("wildclaw_task_parser_for_test", TASK_PARSER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_task(path: Path) -> None:
    path.write_text(
        """---
id: task-canary
timeout_seconds: 30
---
## Prompt

Use the visible prompt.

## Workspace Path

workspace

## Automated Checks

```bash
pytest
```

## Env

PUBLIC_FLAG

## Warmup

echo warm

## Skills

old-skill
""",
        encoding="utf-8",
    )


def test_wildclaw_loader_materializes_bundle_and_rewrites_skills(tmp_path: Path, monkeypatch) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    wildclaw_root.mkdir()
    (wildclaw_root / "skills").mkdir()
    original_task = tmp_path / "tasks" / "task.md"
    original_task.parent.mkdir()
    write_task(original_task)

    parser = load_task_parser()
    monkeypatch.setattr(parser, "ROOT_DIR", wildclaw_root)

    bundle = SkillBundle(
        skill_bundle_id="bundle-canary",
        baseline="autoskill",
        benchmark_target="wildclawbench",
        granularity="task",
        skills=[
            {
                "id": "phase2-canary-skill",
                "title": "Phase 2 Canary",
                "content": "CANARY_WILDCLAW_SKILL_42 must be mentioned by the agent.",
                "metadata": {"source": "test"},
            }
        ],
        created_at="2026-06-23T00:00:00Z",
    )

    context = WildClawBenchSkillLoader(
        wildclaw_root=wildclaw_root,
        artifact_root=tmp_path / "artifacts",
    ).prepare(original_task, bundle)

    parsed = parser.parse_task_md(Path(context.generated_task_path))
    assert parsed["skills"] == "phase2-canary-skill"
    assert parsed["skills_path"] == str((wildclaw_root / "skills").resolve())
    assert (Path(context.host_skills_parent_dir) / "phase2-canary-skill" / "SKILL.md").exists()
    assert list((wildclaw_root / "skills").iterdir()) == []
    assert context.extra_run_args == ["--task", context.generated_task_path]
    assert "--skill-dir" not in context.extra_run_args

    manifest = json.loads(Path(context.manifest_path).read_text(encoding="utf-8"))
    assert manifest["original_task_path"] == str(original_task.resolve())
    assert manifest["generated_task_path"] == context.generated_task_path
    assert manifest["host_skills_parent_dir"] == context.host_skills_parent_dir
    assert manifest["loaded_skill_names"] == ["phase2-canary-skill"]
    assert manifest["container_skill_dir"] == "/root/.agents/skills"
    generated = Path(context.generated_task_path).read_text(encoding="utf-8")
    assert "/root/.agents/skills/phase2-canary-skill/SKILL.md" in generated
    assert "/root/skills/phase2-canary-skill/SKILL.md" not in generated
    assert manifest["skill_hash"] == bundle.skill_hash


def test_wildclaw_loader_no_skill_generates_empty_skills_without_runtime_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    wildclaw_root.mkdir()
    (wildclaw_root / "skills").mkdir()
    original_task = tmp_path / "tasks" / "task.md"
    original_task.parent.mkdir()
    write_task(original_task)

    parser = load_task_parser()
    monkeypatch.setattr(parser, "ROOT_DIR", wildclaw_root)

    context = WildClawBenchSkillLoader(
        wildclaw_root=wildclaw_root,
        artifact_root=tmp_path / "artifacts",
    ).prepare_empty(original_task)

    parsed = parser.parse_task_md(Path(context.generated_task_path))
    assert parsed["skills"] == ""
    assert context.kind == "empty_context"
    assert context.loaded_skill_names == []
    assert context.runtime_skill_dirs == []
    assert context.loaded_files == []
    assert list((wildclaw_root / "skills").iterdir()) == []


def test_wildclaw_loader_prepares_complete_portfolio_without_mutating_benchmark_skills(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    (wildclaw_root / "skills").mkdir(parents=True)
    original_task = tmp_path / "tasks" / "task.md"
    original_task.parent.mkdir()
    write_task(original_task)
    portfolio = tmp_path / "portfolio"
    alpha = portfolio / "alpha"
    (alpha / "scripts").mkdir(parents=True)
    (alpha / "references").mkdir()
    (alpha / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")
    (alpha / "scripts" / "run.py").write_text("print('AUX')\n", encoding="utf-8")
    (alpha / "references" / "notes.md").write_text("details\n", encoding="utf-8")

    context = WildClawBenchSkillLoader(
        wildclaw_root=wildclaw_root,
        artifact_root=tmp_path / "artifacts",
    ).prepare_portfolio(original_task, portfolio, skill_hash="portfolio-hash")

    runtime = Path(context.host_skills_parent_dir)
    assert (runtime / "alpha" / "SKILL.md").is_file()
    assert (runtime / "alpha" / "scripts" / "run.py").read_text(encoding="utf-8") == "print('AUX')\n"
    assert (runtime / "alpha" / "references" / "notes.md").is_file()
    assert list((wildclaw_root / "skills").iterdir()) == []
    assert context.skill_hash == "portfolio-hash"
    manifest = json.loads(Path(context.manifest_path).read_text(encoding="utf-8"))
    assert "alpha/scripts/run.py" in manifest["loaded_files"]


def test_wildclaw_loader_preserves_multifile_skill_package(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    (wildclaw_root / "skills").mkdir(parents=True)
    original_task = tmp_path / "tasks" / "task.md"
    original_task.parent.mkdir()
    write_task(original_task)
    bundle = SkillBundle(
        skill_bundle_id="bundle-multifile",
        baseline="coevoskills",
        benchmark_target="wildclawbench",
        granularity="task",
        skills=[
            {
                "id": "evo-demo",
                "title": "Demo",
                "content": "fallback",
                "files": {
                    "SKILL.md": "# Exact Skill\n",
                    "scripts/utils.py": "def answer():\n    return 42\n",
                    "references/guide.md": "# Guide\n",
                },
                "entrypoint": "scripts/utils.py",
            }
        ],
        created_at="2026-07-13T00:00:00Z",
    )

    context = WildClawBenchSkillLoader(
        wildclaw_root=wildclaw_root,
        artifact_root=tmp_path / "artifacts",
    ).prepare(original_task, bundle)

    runtime = Path(context.host_skills_parent_dir) / "evo-demo"
    assert (runtime / "SKILL.md").read_text() == "# Exact Skill\n"
    assert "return 42" in (runtime / "scripts" / "utils.py").read_text()
    assert (runtime / "references" / "guide.md").exists()
    assert list((wildclaw_root / "skills").iterdir()) == []
    assert set(context.loaded_files) == {
        "evo-demo/SKILL.md",
        "evo-demo/scripts/utils.py",
        "evo-demo/references/guide.md",
    }
