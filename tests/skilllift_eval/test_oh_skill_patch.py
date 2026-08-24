from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH = ROOT / "scripts" / "oh_skill_patch.py"


def _load_patch(monkeypatch, skills_root: Path):
    class FakeSkill:
        def __init__(self, *, name, content, trigger):
            self.name = name
            self.content = content
            self.trigger = trigger

    setup = types.ModuleType("openhands_cli.setup")
    setup.load_agent_specs = lambda *args, **kwargs: kwargs["skills"]
    cli = types.ModuleType("openhands_cli")
    cli.setup = setup
    context = types.ModuleType("openhands.sdk.context")
    context.Skill = FakeSkill
    sdk = types.ModuleType("openhands.sdk")
    sdk.context = context
    openhands = types.ModuleType("openhands")
    openhands.sdk = sdk
    monkeypatch.setitem(sys.modules, "openhands_cli", cli)
    monkeypatch.setitem(sys.modules, "openhands_cli.setup", setup)
    monkeypatch.setitem(sys.modules, "openhands", openhands)
    monkeypatch.setitem(sys.modules, "openhands.sdk", sdk)
    monkeypatch.setitem(sys.modules, "openhands.sdk.context", context)
    monkeypatch.setenv("OH_SKILLS_ROOT", str(skills_root))
    spec = importlib.util.spec_from_file_location("test_oh_skill_patch_module", PATCH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, setup


def test_patch_loads_complete_portfolio_and_emits_both_receipts(tmp_path: Path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    alpha = skills_root / "alpha"
    beta = skills_root / "beta"
    (alpha / "scripts").mkdir(parents=True)
    (alpha / "references").mkdir()
    beta.mkdir(parents=True)
    alpha_md = "---\nname: alpha\ndescription: Alpha.\ntriggers: [alpha]\n---\n# Alpha\n"
    beta_md = "---\nname: beta\ndescription: Beta.\ntriggers: [beta]\n---\n# Beta\n"
    (alpha / "SKILL.md").write_text(alpha_md, encoding="utf-8")
    (alpha / "scripts" / "run.py").write_text("print('AUX_SENTINEL')\n", encoding="utf-8")
    (alpha / "references" / "notes.md").write_text("REFERENCE_SENTINEL\n", encoding="utf-8")
    (beta / "SKILL.md").write_text(beta_md, encoding="utf-8")
    (skills_root / "reference.md").write_text("SHARED_REFERENCE\n", encoding="utf-8")

    _, setup = _load_patch(monkeypatch, skills_root)
    loaded = setup.load_agent_specs(skills=[])

    assert [skill.name for skill in loaded] == ["alpha", "beta"]
    assert "/skills/alpha" in loaded[0].content
    assert "AUX_SENTINEL" not in loaded[0].content
    receipts = skills_root / ".skilllift-receipts"
    deployed = json.loads((receipts / "deployed_tree_receipt.json").read_text(encoding="utf-8"))
    merged = json.loads((receipts / "loaded_skill_receipt.json").read_text(encoding="utf-8"))
    assert {item["path"] for item in deployed["files"]} == {
        "alpha/SKILL.md",
        "alpha/scripts/run.py",
        "alpha/references/notes.md",
        "beta/SKILL.md",
        "reference.md",
    }
    assert merged["skills"] == [
        {
            "name": "alpha",
            "skill_md_sha256": hashlib.sha256(alpha_md.encode()).hexdigest(),
            "source_chars": len(alpha_md),
            "injected_chars": len(loaded[0].content),
        },
        {
            "name": "beta",
            "skill_md_sha256": hashlib.sha256(beta_md.encode()).hexdigest(),
            "source_chars": len(beta_md),
            "injected_chars": len(loaded[1].content),
        },
    ]


def test_patch_rejects_duplicate_canonical_skill_names(tmp_path: Path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "alpha").mkdir(parents=True)
    (skills_root / "alpha" / "SKILL.md").write_text("directory", encoding="utf-8")
    (skills_root / "alpha.md").write_text(
        "---\nname: alpha\ndescription: Duplicate.\n---\n# Flat\n",
        encoding="utf-8",
    )
    module, _ = _load_patch(monkeypatch, skills_root)
    module._CACHED_SKILLS = None

    try:
        module._load_custom_skills()
    except RuntimeError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate skill names must fail closed")
