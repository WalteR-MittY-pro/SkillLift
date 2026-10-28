"""Pinned source checkout. Called explicitly on the execution machine, never on import."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
import hashlib

from .common import IS_WINDOWS, ROOT, HERE, read_json

PINS = {
    "skilllearnbench": ("https://github.com/cxcscmu/SkillLearnBench.git", "726e1f79b96b0cb3d729d0e55a1c847e0b5353ae"),
    "skillopt": ("https://github.com/microsoft/SkillOpt.git", "84b36cd5564e8893569e50e37f02a81d10b55e27"),
    "skillsbench": ("https://github.com/WalteR-MittY-pro/skillsbench.git", "67893f26077479a6a3ccfb62272bf14762016d50"),
    "wildclawbench": ("https://github.com/WalteR-MittY-pro/WildClawBench.git", "3102b50bf98a30590673476649939399242cc092"),
}

# Required local environment dependencies, pinned separately from upstream Git.
# These additions do not authorize edits to tracked task or evaluation files.
SUPPLEMENTAL_TASK_FILES = {
    "skillsbench": {
        "tasks/fix-druid-loophole-cve/environment/apache-druid-0.20.0-bin.tar.gz":
            "734f836375bc1121100712b0501149f8bcbcb6d4cf2109b522af06367a8b1cb5",
        "tasks/multilingual-video-dubbing/environment/prewarm_kokoro_hub.py":
            "3496c8383fddbfbfe36bd96deab20150c54e9c88413a31d9a1879173db35c5a6",
    },
}


def source_path(name):
    return ROOT / ".cache" / "revision2026" / name


def _verify_task_supplements(name, path, untracked_paths):
    allowed = SUPPLEMENTAL_TASK_FILES.get(name, {})
    unexpected = sorted(set(untracked_paths) - set(allowed))
    if unexpected:
        raise ValueError(f"Untracked task files may alter the evaluation: {path}: {', '.join(unexpected)}")
    for relative in untracked_paths:
        dependency = path / relative
        if dependency.is_symlink() or not dependency.is_file():
            raise ValueError(f"Supplemental dependency must be a regular file: {dependency}")
        with dependency.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != allowed[relative]:
            raise ValueError(f"Supplemental dependency SHA-256 mismatch: {dependency}")


def verify_source(name):
    path = source_path(name)
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if revision != PINS[name][1]:
        raise ValueError(f"Wrong {name} revision: {revision}")
    dirty = subprocess.check_output(["git", "-C", str(path), "diff", "--name-only", "HEAD"], text=True)
    if dirty.strip():
        raise ValueError(f"Modified upstream source: {path}; use an untouched pinned checkout")
    added_tasks = subprocess.check_output(["git", "-C", str(path), "ls-files", "--others", "--exclude-standard", "--", "tasks"], text=True)
    _verify_task_supplements(name, path, added_tasks.splitlines())
    # A Windows checkout with core.autocrlf=true rewrites LF to CRLF on disk while `git diff`
    # still looks clean. Task bytes are hashed and test.sh runs in Linux containers, so reject it.
    eol = subprocess.check_output(["git", "-C", str(path), "ls-files", "--eol"], text=True) if IS_WINDOWS else ""
    converted = [line.split("\t", 1)[-1] for line in eol.splitlines() if "i/lf" in line and "w/crlf" in line]
    if converted:
        raise ValueError(f"{len(converted)} files in {path} were checked out with CRLF line endings "
                         f"(e.g. {converted[0]}). Delete {path} and rerun `prepare`; it now checks out with core.autocrlf=false.")
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
        # Byte-exact upstream files on every platform (Windows Git defaults to CRLF conversion).
        for key, value in (("core.autocrlf", "false"), ("core.eol", "lf"), ("core.longpaths", "true")):
            subprocess.run(["git", "-C", str(path), "config", key, value], check=True)
        # Network fetch: retry transient failures, then explain how to route Git through a proxy.
        for attempt in range(1, 4):
            if subprocess.run(["git", "-C", str(path), "fetch", "--depth", "1", "origin", revision]).returncode == 0:
                break
            if attempt < 3:
                print(f"git fetch {name} failed (attempt {attempt}/3); retrying in {10 * attempt}s", flush=True)
                time.sleep(10 * attempt)
        else:
            raise ValueError(
                f"Could not download {url} @ {revision[:12]}. GitHub is unreachable from this machine. "
                "Route Git through your proxy and rerun, e.g. in Git Bash: "
                "export HTTPS_PROXY=http://127.0.0.1:<proxy-port> HTTP_PROXY=http://127.0.0.1:<proxy-port> "
                "(or: git config --global http.https://github.com.proxy http://127.0.0.1:<proxy-port>). "
                "The partial checkout is resumed automatically.")
        subprocess.run(["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"], check=True)
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
