from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

import yaml

from ..errors import SkillLiftError


_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_FENCED_DIFF_RE = re.compile(r"\A\s*```diff\s*\n(.*?)```\s*\Z", re.DOTALL | re.IGNORECASE)
_FORBIDDEN_FILENAMES = {
    "dockerfile",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "pipfile",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
}


class PortfolioError(SkillLiftError):
    """Raised when a canonical portfolio or candidate patch is invalid."""


@dataclass(frozen=True)
class PortfolioRef:
    task_id: str
    root: Path
    tree_hash: str
    seed_hash: str
    config_hash: str
    curated_skill_names: frozenset[str]
    generated_skill_names: frozenset[str]
    curated_asset_paths: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_directory(
        cls,
        *,
        task_id: str,
        root: Path | str,
        seed_hash: str | None = None,
        config_hash: str,
        curated_skill_names: Iterable[str] = (),
        generated_skill_names: Iterable[str] = (),
        curated_asset_paths: Iterable[str] | None = None,
    ) -> PortfolioRef:
        resolved = Path(root).resolve()
        curated = frozenset(curated_skill_names)
        generated = frozenset(generated_skill_names)
        names, shared_paths = _portfolio_layout(resolved)
        assets = frozenset(shared_paths if curated_asset_paths is None else curated_asset_paths)
        if not curated and not generated:
            curated = frozenset(names)
        _validate_origins(names, curated, generated)
        validate_portfolio(
            resolved,
            curated_skill_names=curated,
            generated_skill_names=generated,
            curated_asset_paths=assets,
            grandfathered_curated=True,
        )
        tree_hash = portfolio_tree_hash(resolved)
        return cls(
            task_id=task_id,
            root=resolved,
            tree_hash=tree_hash,
            seed_hash=seed_hash or tree_hash,
            config_hash=config_hash,
            curated_skill_names=curated,
            generated_skill_names=generated,
            curated_asset_paths=assets,
        )


@dataclass(frozen=True)
class PortfolioPatchResult:
    portfolio: PortfolioRef
    patch_hash: str
    changed_paths: tuple[str, ...]


def portfolio_tree_hash(root: Path | str) -> str:
    digest = hashlib.sha256()
    for rel_path, mode, data in _tree_files(Path(root).resolve()):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{mode:o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def validate_portfolio(
    root: Path | str,
    *,
    curated_skill_names: Iterable[str] = (),
    generated_skill_names: Iterable[str] = (),
    curated_asset_paths: Iterable[str] | None = None,
    grandfathered_curated: bool = False,
    modified_skill_names: Iterable[str] = (),
) -> None:
    resolved = Path(root).resolve()
    _tree_files(resolved)
    names, shared_paths = _portfolio_layout(resolved)
    curated = frozenset(curated_skill_names)
    generated = frozenset(generated_skill_names)
    assets = frozenset(shared_paths if curated_asset_paths is None else curated_asset_paths)
    modified = frozenset(modified_skill_names)
    if curated or generated:
        _validate_origins(names, curated, generated)
    if shared_paths != set(assets):
        raise PortfolioError(
            f"portfolio shared asset mismatch: actual={sorted(shared_paths)}, expected={sorted(assets)}"
        )

    for name in sorted(names):
        if not _SKILL_NAME_RE.fullmatch(name):
            raise PortfolioError(f"invalid skill directory name: {name}")
        skill_md = resolved / name / "SKILL.md"
        if not skill_md.is_file():
            raise PortfolioError(f"skill directory must contain SKILL.md: {name}")
        if grandfathered_curated and name in curated and name not in modified:
            continue
        _validate_skill_markdown(skill_md, expected_name=name)


def extract_unified_diff(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PortfolioError("skill generator output must be a non-empty unified diff")
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    fenced = _FENCED_DIFF_RE.fullmatch(normalized)
    patch = fenced.group(1) if fenced else normalized
    has_bare_fence = any(line.startswith("```") for line in patch.splitlines())
    if not fenced and (has_bare_fence or not patch.startswith("diff --git ")):
        raise PortfolioError("skill generator output must contain only one unified diff")
    if not patch.startswith("diff --git "):
        raise PortfolioError("invalid unified diff")
    if "\x00" in patch:
        raise PortfolioError("binary patch content is forbidden")
    _validate_patch_headers(patch)
    return patch


def apply_portfolio_patch(
    parent: PortfolioRef,
    raw_patch: str,
    destination: Path | str,
    *,
    target_scope: Iterable[str],
    new_skill_names: Iterable[str] = (),
) -> PortfolioPatchResult:
    patch = extract_unified_diff(raw_patch)
    destination_path = Path(destination).resolve()
    if destination_path.exists():
        raise PortfolioError(f"candidate destination already exists: {destination_path}")
    if portfolio_tree_hash(parent.root) != parent.tree_hash:
        raise PortfolioError("parent portfolio hash no longer matches its directory")

    scopes = tuple(_normalize_scope(scope) for scope in target_scope)
    if not scopes:
        raise PortfolioError("target_scope must contain at least one path")
    declared_new = frozenset(new_skill_names)
    if any(not _SKILL_NAME_RE.fullmatch(name) for name in declared_new):
        raise PortfolioError("new skill names must use lowercase kebab-case")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="portfolio-patch-", dir=destination_path.parent) as temp:
        work_root = Path(temp) / "tree"
        shutil.copytree(parent.root, work_root, copy_function=shutil.copy2)
        before = _snapshot(work_root)
        _git_apply(work_root, patch, check=True)
        _git_apply(work_root, patch, check=False)
        after = _snapshot(work_root)
        changed_paths = tuple(
            sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        )
        if not changed_paths:
            raise PortfolioError("candidate patch does not change the portfolio")
        _validate_changed_paths(changed_paths, scopes)
        _validate_forbidden_paths(changed_paths)

        previous_names = _portfolio_skill_names(parent.root)
        candidate_names = _portfolio_skill_names(work_root)
        candidate_assets = _portfolio_shared_paths(work_root)
        if candidate_assets != parent.curated_asset_paths:
            raise PortfolioError(
                "curated shared asset identities cannot be deleted, renamed, or added: "
                f"actual={sorted(candidate_assets)}, expected={sorted(parent.curated_asset_paths)}"
            )
        added_names = candidate_names - previous_names
        if added_names != declared_new:
            raise PortfolioError(
                f"new skill directories {sorted(added_names)} do not match declaration {sorted(declared_new)}"
            )
        missing_curated = parent.curated_skill_names - candidate_names
        if missing_curated:
            raise PortfolioError(f"curated skill identities cannot be deleted or renamed: {sorted(missing_curated)}")
        active_generated = (parent.generated_skill_names & candidate_names) | added_names
        modified_skill_names = {
            PurePosixPath(path).parts[0]
            for path in changed_paths
            if len(PurePosixPath(path).parts) == 2 and PurePosixPath(path).name == "SKILL.md"
        }
        modified_curated = modified_skill_names & parent.curated_skill_names
        for name in modified_curated:
            _validate_skill_markdown(
                work_root / name / "SKILL.md",
                expected_name=name,
                require_triggers=_skill_has_valid_triggers(parent.root / name / "SKILL.md"),
            )
        validate_portfolio(
            work_root,
            curated_skill_names=parent.curated_skill_names,
            generated_skill_names=active_generated,
            curated_asset_paths=parent.curated_asset_paths,
            grandfathered_curated=True,
            modified_skill_names=(modified_skill_names - modified_curated) | added_names,
        )
        candidate_hash = portfolio_tree_hash(work_root)
        os.replace(work_root, destination_path)

    portfolio = PortfolioRef(
        task_id=parent.task_id,
        root=destination_path,
        tree_hash=candidate_hash,
        seed_hash=parent.seed_hash,
        config_hash=parent.config_hash,
        curated_skill_names=parent.curated_skill_names,
        generated_skill_names=frozenset(active_generated),
        curated_asset_paths=parent.curated_asset_paths,
    )
    return PortfolioPatchResult(
        portfolio=portfolio,
        patch_hash=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        changed_paths=changed_paths,
    )


def portfolio_manifest(root: Path | str) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "path": path,
            "mode": mode,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for path, mode, data in _tree_files(Path(root).resolve())
    )


def portfolio_skill_names(root: Path | str) -> frozenset[str]:
    return frozenset(_portfolio_skill_names(Path(root).resolve()))


def _tree_files(root: Path) -> list[tuple[str, int, bytes]]:
    if not root.is_dir():
        raise PortfolioError(f"portfolio root is not a directory: {root}")
    result: list[tuple[str, int, bytes]] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in tuple(dirnames):
            path = current_path / name
            if path.is_symlink():
                raise PortfolioError(f"symlink is forbidden: {path.relative_to(root).as_posix()}")
            if not path.is_dir():
                raise PortfolioError(f"non-directory tree entry is forbidden: {path.relative_to(root).as_posix()}")
        for name in filenames:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise PortfolioError(f"symlink is forbidden: {rel}")
            if not stat.S_ISREG(info.st_mode):
                raise PortfolioError(f"non-regular file is forbidden: {rel}")
            data = path.read_bytes()
            if b"\x00" in data:
                raise PortfolioError(f"binary file is forbidden: {rel}")
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PortfolioError(f"file must be UTF-8: {rel}") from exc
            result.append((rel, stat.S_IMODE(info.st_mode), data))
    return sorted(result, key=lambda item: item[0])


def _portfolio_skill_names(root: Path) -> set[str]:
    return _portfolio_layout(root)[0]


def _portfolio_shared_paths(root: Path) -> frozenset[str]:
    return frozenset(_portfolio_layout(root)[1])


def _portfolio_layout(root: Path) -> tuple[set[str], set[str]]:
    if not root.is_dir():
        raise PortfolioError(f"portfolio root is not a directory: {root}")
    names: set[str] = set()
    shared: set[str] = set()
    for path in root.iterdir():
        if path.is_symlink():
            raise PortfolioError(f"symlink is forbidden: {path.name}")
        if path.is_dir() and (path / "SKILL.md").is_file():
            names.add(path.name)
        else:
            shared.add(path.name)
    return names, shared


def _validate_origins(names: set[str], curated: frozenset[str], generated: frozenset[str]) -> None:
    overlap = curated & generated
    if overlap:
        raise PortfolioError(f"skill origins overlap: {sorted(overlap)}")
    unknown = names - curated - generated
    missing_generated = generated - names
    if unknown or missing_generated:
        raise PortfolioError(
            f"portfolio origin mismatch: unknown={sorted(unknown)}, missing_generated={sorted(missing_generated)}"
        )
    missing_curated = curated - names
    if missing_curated:
        raise PortfolioError(f"curated skills are missing: {sorted(missing_curated)}")


def _validate_skill_markdown(
    path: Path,
    *,
    expected_name: str,
    require_triggers: bool = True,
) -> None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.fullmatch(text)
    if not match:
        raise PortfolioError(f"{expected_name}/SKILL.md must contain YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise PortfolioError(f"{expected_name}/SKILL.md has invalid YAML frontmatter") from exc
    if not isinstance(frontmatter, dict):
        raise PortfolioError(f"{expected_name}/SKILL.md frontmatter must be an object")
    if frontmatter.get("name") != expected_name:
        raise PortfolioError(f"SKILL.md name must match skill directory: {expected_name}")
    if not isinstance(frontmatter.get("description"), str) or not frontmatter["description"].strip():
        raise PortfolioError(f"{expected_name}/SKILL.md must include description")
    triggers = frontmatter.get("triggers")
    if require_triggers and not _valid_triggers(triggers):
        raise PortfolioError(f"{expected_name}/SKILL.md must include non-empty triggers")
    if not match.group(2).strip():
        raise PortfolioError(f"{expected_name}/SKILL.md body must not be empty")


def _skill_has_valid_triggers(path: Path) -> bool:
    match = _FRONTMATTER_RE.fullmatch(path.read_text(encoding="utf-8"))
    if not match:
        return False
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return False
    return isinstance(frontmatter, dict) and _valid_triggers(frontmatter.get("triggers"))


def _valid_triggers(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value.strip()
        or isinstance(value, list)
        and value
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _validate_patch_headers(patch: str) -> None:
    found = False
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        found = True
        fields = line.split()
        if len(fields) != 4 or not fields[2].startswith("a/") or not fields[3].startswith("b/"):
            raise PortfolioError(f"invalid diff header: {line}")
        for diff_field in fields[2:]:
            _safe_patch_path(diff_field[2:])
    if not found:
        raise PortfolioError("unified diff has no file headers")


def _safe_patch_path(raw: str) -> str:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise PortfolioError(f"unsafe patch path: {raw}")
    return path.as_posix()


def _normalize_scope(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PortfolioError("target scope must be a non-empty relative path")
    return _safe_patch_path(raw.strip().rstrip("/"))


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {path: (mode, data) for path, mode, data in _tree_files(root)}


def _git_apply(root: Path, patch: str, *, check: bool) -> None:
    command = ["git", "apply", "--no-index", "--recount"]
    if check:
        command.append("--check")
    completed = subprocess.run(
        command,
        cwd=root,
        input=patch.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PortfolioError(f"git apply {'check' if check else 'apply'} failed: {message}")


def _validate_changed_paths(changed_paths: tuple[str, ...], scopes: tuple[str, ...]) -> None:
    outside = [
        path
        for path in changed_paths
        if not any(path == scope or path.startswith(f"{scope}/") for scope in scopes)
    ]
    if outside:
        raise PortfolioError(f"patch changed paths outside target_scope: {outside}")


def _validate_forbidden_paths(changed_paths: tuple[str, ...]) -> None:
    forbidden = [path for path in changed_paths if PurePosixPath(path).name.lower() in _FORBIDDEN_FILENAMES]
    if forbidden:
        raise PortfolioError(f"dependency or environment files are forbidden: {forbidden}")
