"""Pinned source checkout. Called explicitly on the execution machine, never on import."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
import hashlib

from .common import ROOT, HERE, read_json

PINS = {
    "skilllearnbench": ("https://github.com/cxcscmu/SkillLearnBench.git", "726e1f79b96b0cb3d729d0e55a1c847e0b5353ae"),
    "skillopt": ("https://github.com/microsoft/SkillOpt.git", "84b36cd5564e8893569e50e37f02a81d10b55e27"),
    "skillsbench": ("https://github.com/WalteR-MittY-pro/skillsbench.git", "67893f26077479a6a3ccfb62272bf14762016d50"),
    "wildclawbench": ("https://github.com/WalteR-MittY-pro/WildClawBench.git", "3102b50bf98a30590673476649939399242cc092"),
}


def source_path(name):
    return ROOT / ".cache" / "revision2026" / name


def verify_source(name):
    path = source_path(name)
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if revision != PINS[name][1]:
        raise ValueError(f"Wrong {name} revision: {revision}")
    dirty = subprocess.check_output(["git", "-C", str(path), "diff", "--name-only", "HEAD"], text=True)
    if dirty.strip():
        raise ValueError(f"Modified upstream source: {path}; use an untouched pinned checkout")
    added_tasks = subprocess.check_output(["git", "-C", str(path), "ls-files", "--others", "--exclude-standard", "--", "tasks"], text=True)
    if added_tasks.strip():
        raise ValueError(f"Untracked task files may alter the evaluation: {path}")
    return path


def prepare_sources(names):
    for name in names:
        url, revision = PINS[name]
        path = source_path(name)
        if (path / ".git").is_dir():
            head = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD"], capture_output=True)
            if head.returncode == 0:
                verify_source(name)
                continue
            origin = subprocess.check_output(["git", "-C", str(path), "remote", "get-url", "origin"], text=True).strip()
            if origin != url:
                raise ValueError(f"Incomplete checkout has an unexpected remote: {path}")
        else:
            if path.exists() and any(path.iterdir()):
                raise ValueError(f"Refusing to overwrite nonempty source directory: {path}")
            path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", str(path)], check=True)
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
        for command in (["git", "-C", str(path), "fetch", "--depth", "1", "origin", revision],
                        ["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"]):
            subprocess.run(command, check=True)
        verify_source(name)


@dataclass(frozen=True)
class Instance:
    family: str
    number: int
    path: Path

    @property
    def task_id(self):
        return f"{self.family}/{self.path.name}"


def instances(root=None):
    root = Path(root) if root else source_path("skilllearnbench") / "tasks"
    expected = set(read_json(HERE / "seeds" / "LLM_SKILLS_FROZEN.json")["per_family"])
    result = []
    registry = read_json(HERE / "skilllearnbench_instances.json")
    for row in registry["instances"]:
        path = root / row["family"] / (row["family"] + "-" + str(row["number"]))
        content = (path / "instruction.md").read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if actual != row["instruction_git_blob"]:
            raise ValueError(f"Task instruction differs from the fixed official revision: {path}")
        result.append(Instance(row["family"], row["number"], path))
    if {x.family for x in result} != expected:
        raise ValueError("Pinned instance registry and generated skill families differ")
    return result


def split_instances(items):
    train = [item for item in items if item.number == 1]
    test = [item for item in items if item.number > 1]
    if {x.task_id for x in train} & {x.task_id for x in test}:
        raise ValueError("Train/test overlap")
    return train, test
