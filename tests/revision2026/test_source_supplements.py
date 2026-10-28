"""Verify allowed environment dependencies without weakening upstream checks."""
import hashlib
import subprocess

import pytest

from experiments.revision2026 import sources


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "upstream"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    git("init", "--quiet")
    task = root / "tasks/example/task.md"
    task.parent.mkdir(parents=True)
    task.write_text("Original task\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Source test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "Pinned task")
    monkeypatch.setattr(sources, "source_path", lambda name: root)
    monkeypatch.setattr(sources, "PINS", {"skillsbench": ("unused", git("rev-parse", "HEAD"))})
    allowed = {}
    for relative in sources.SUPPLEMENTAL_TASK_FILES["skillsbench"]:
        dependency = root / relative
        dependency.parent.mkdir(parents=True, exist_ok=True)
        payload = relative.encode("utf-8")
        dependency.write_bytes(payload)
        allowed[relative] = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(sources, "SUPPLEMENTAL_TASK_FILES", {"skillsbench": allowed})
    return root, list(allowed)


def test_registered_dependencies_pass_source_verification(checkout):
    root, _ = checkout
    assert sources.verify_source("skillsbench") == root


def test_modified_dependency_is_rejected(checkout):
    root, paths = checkout
    (root / paths[0]).write_bytes(b"changed dependency")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        sources.verify_source("skillsbench")


def test_unregistered_task_file_is_rejected(checkout):
    root, _ = checkout
    (root / "tasks/example/extra.py").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="Untracked task files"):
        sources.verify_source("skillsbench")


def test_tracked_task_modification_is_rejected(checkout):
    root, _ = checkout
    (root / "tasks/example/task.md").write_text("Changed task\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Modified upstream source"):
        sources.verify_source("skillsbench")


def test_dependency_symlink_is_rejected(checkout):
    root, paths = checkout
    dependency = root / paths[0]
    target = root / "dependency-target"
    dependency.replace(target)
    try:
        dependency.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable on this platform")
    with pytest.raises(ValueError, match="regular file"):
        sources.verify_source("skillsbench")
