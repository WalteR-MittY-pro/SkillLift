from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "skillsbench_domain.sh"
DOMAINS = (
    "cybersecurity",
    "finance-economics",
    "industrial-physical-systems",
    "mathematics-or-formal-reasoning",
    "media-content-production",
    "natural-science",
    "office-white-collar",
    "software-engineering",
)


def test_domain_script_exposes_category_phase_and_run_root_contract() -> None:
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "<category|all> <check|train|test> <run-root>" in completed.stdout
    assert "SKILLLIFT_SKILLSBENCH_CONFIG" in completed.stdout


def test_each_domain_has_a_launcher_and_dedicated_config() -> None:
    for domain in DOMAINS:
        script = ROOT / "scripts" / "skillsbench_domains" / f"{domain}.sh"
        config_path = ROOT / "configs" / "skillsbench" / "domains" / f"{domain}.yaml"

        completed = subprocess.run(
            ["bash", str(script), "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0, completed.stderr
        assert domain in script.read_text(encoding="utf-8")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config["skillsbench"]["model"] == "vllm/gpt-5.4-mini"
        assert config["run_root"] == f"runs/skilllift_skillsbench_{domain}_gpt54mini_v1"
