import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

# SkillOpt installs its own regular `scripts` package, which can shadow this
# repository's CLI directory. Load the exact standalone script under test.
_spec = importlib.util.spec_from_file_location(
    "skilllift_image_prefetch", Path(__file__).resolve().parents[2] / "scripts/prefetch_skilllearnbench_images.py"
)
prefetch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = prefetch
_spec.loader.exec_module(prefetch)


@pytest.mark.parametrize("value", [
    "https://example.org/ubuntu;id", "http://user:password@example.org/ubuntu",
    "http://example.org/ubuntu?x=1", "file:///ubuntu", "http://example.org/ubuntu&x",
])
def test_mirror_rejects_credentials_and_shell_metacharacters(value):
    with pytest.raises(argparse.ArgumentTypeError):
        prefetch.ubuntu_mirror_url(value)


def test_mirror_changes_only_ubuntu_stages_and_keeps_original_commands():
    source = ("FROM --platform=linux/amd64 ubuntu:20.04 AS base\n"
              "RUN apt-get install -y curl\nFROM python:3.11-slim\nRUN pip install pytest\n")
    updated = prefetch.mirror_dockerfile(source, "http://mirrors.ustc.edu.cn/ubuntu/")
    assert updated.count("99skilllift-mirror-direct") == 1
    assert "sources.list.d/*.sources" in updated  # Ubuntu 24.04 deb822
    assert "/etc/apt/sources.list " in updated  # Ubuntu 20.04
    assert "security\\.ubuntu\\.com" in updated
    assert updated.endswith("FROM python:3.11-slim\nRUN pip install pytest\n")
    assert "RUN apt-get install -y curl\n" in updated
    assert 'org.skilllift.ubuntu-mirror="http://mirrors.ustc.edu.cn/ubuntu"' in updated
    assert "trusted=yes" not in updated and "AllowInsecure" not in updated


def test_python_image_is_not_rewritten():
    source = "FROM python:3.10-slim\nRUN apt-get update\n"
    assert prefetch.mirror_dockerfile(source, "http://mirrors.ustc.edu.cn/ubuntu") == source


def test_failed_build_has_live_log_and_archived_temporary_dockerfile(tmp_path, monkeypatch):
    context = tmp_path / "temporary-context"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM ubuntu:24.04\nRUN apt-get update\n")
    monkeypatch.setattr(prefetch, "WORK", tmp_path / "work")
    monkeypatch.setattr(prefetch.docker_agent, "command", lambda *a, **kw: None)

    def build(args, *, stdout, stderr, **kwargs):
        assert "--progress=plain" in args
        assert "HTTPS_PROXY=http://proxy:7897" in args
        assert kwargs == {"timeout": 60, "check": False}
        stdout.write("simulated apt failure\n")
        stdout.flush()
        assert (prefetch.WORK / "image-locks/test.live.log").read_text() == "simulated apt failure\n"
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(prefetch.subprocess, "run", build)
    prefetch.install_build_args(["HTTPS_PROXY=http://proxy:7897"], "http://mirrors.ustc.edu.cn/ubuntu")
    result = prefetch.docker_agent.command(
        ["docker", "build", "-t", "test", str(context)], timeout=60, check=False)
    assert result.returncode == 1 and "simulated apt failure" in result.stdout
    assert (prefetch.WORK / "dockerfiles/test.Dockerfile").read_text() == (context / "Dockerfile").read_text()
