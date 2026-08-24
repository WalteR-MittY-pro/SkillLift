from __future__ import annotations

import os
from pathlib import Path

import pytest

from skilllift.portfolio import (
    PortfolioError,
    PortfolioRef,
    apply_portfolio_patch,
    extract_unified_diff,
    portfolio_tree_hash,
    validate_portfolio,
)


def _skill(name: str, body: str = "Follow the workflow.", *, complete: bool = True) -> str:
    triggers = f"triggers:\n  - {name}\n" if complete else ""
    return (
        f"---\nname: {name}\ndescription: Use for {name} tasks.\n{triggers}---\n"
        f"# {name}\n\n{body}\n"
    )


def _write_skill(root: Path, name: str, body: str = "Follow the workflow.", *, complete: bool = True) -> None:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(_skill(name, body, complete=complete), encoding="utf-8")


def _ref(
    root: Path,
    *,
    curated: tuple[str, ...] = (),
    generated: tuple[str, ...] = (),
) -> PortfolioRef:
    return PortfolioRef.from_directory(
        task_id="task-1",
        root=root,
        seed_hash=portfolio_tree_hash(root),
        config_hash="config-1",
        curated_skill_names=curated,
        generated_skill_names=generated,
    )


def _replace_body_patch(name: str, old: str, new: str) -> str:
    return f"""diff --git a/{name}/SKILL.md b/{name}/SKILL.md
--- a/{name}/SKILL.md
+++ b/{name}/SKILL.md
@@ -6,4 +6,4 @@
 ---
 #{' '}{name}
 
-{old}
+{new}
"""


def _new_skill_patch(name: str) -> str:
    content = _skill(name, "Use the deterministic helper.")
    lines = "".join(f"+{line}" for line in content.splitlines(keepends=True))
    return f"""diff --git a/{name}/SKILL.md b/{name}/SKILL.md
new file mode 100644
--- /dev/null
+++ b/{name}/SKILL.md
@@ -0,0 +1,99 @@
{lines}diff --git a/{name}/scripts/run.py b/{name}/scripts/run.py
new file mode 100644
--- /dev/null
+++ b/{name}/scripts/run.py
@@ -0,0 +1 @@
+print("ok")
"""


def _delete_skill_patch(name: str, content: str) -> str:
    lines = "".join(f"-{line}" for line in content.splitlines(keepends=True))
    return f"""diff --git a/{name}/SKILL.md b/{name}/SKILL.md
deleted file mode 100644
--- a/{name}/SKILL.md
+++ /dev/null
@@ -1,99 +0,0 @@
{lines}"""


def test_tree_hash_is_deterministic_and_includes_mode_and_bytes(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_skill(left, "beta")
    _write_skill(left, "alpha")
    _write_skill(right, "alpha")
    _write_skill(right, "beta")

    assert portfolio_tree_hash(left) == portfolio_tree_hash(right)

    script = right / "alpha" / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('ok')\n", encoding="utf-8")
    content_hash = portfolio_tree_hash(right)
    script.chmod(0o755)
    assert portfolio_tree_hash(right) != content_hash


def test_extract_unified_diff_accepts_one_fence_and_rejects_extra_text() -> None:
    patch = _replace_body_patch("alpha", "old", "new")

    assert extract_unified_diff(f"```diff\n{patch}```\n") == patch
    with pytest.raises(PortfolioError):
        extract_unified_diff(f"explanation\n{patch}")


def test_extract_unified_diff_allows_markdown_fences_inside_patch_body() -> None:
    patch = """diff --git a/alpha/SKILL.md b/alpha/SKILL.md
--- a/alpha/SKILL.md
+++ b/alpha/SKILL.md
@@ -1,3 +1,3 @@
 ```python
-print("old")
+print("new")
 ```
"""

    assert extract_unified_diff(patch) == patch
    with pytest.raises(PortfolioError):
        extract_unified_diff(patch + "```\n")


def test_patch_can_atomically_edit_multiple_skills_and_add_a_skill(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "alpha", "old")
    _write_skill(parent_root, "beta", "old")
    parent = _ref(parent_root, curated=("alpha", "beta"))
    patch = (
        _replace_body_patch("alpha", "old", "new alpha")
        + _replace_body_patch("beta", "old", "new beta")
        + _new_skill_patch("gamma")
    )

    result = apply_portfolio_patch(
        parent,
        patch,
        tmp_path / "candidate",
        target_scope=("alpha/", "beta/", "gamma/"),
        new_skill_names=("gamma",),
    )

    assert result.portfolio.tree_hash == portfolio_tree_hash(result.portfolio.root)
    assert result.portfolio.generated_skill_names == frozenset({"gamma"})
    assert "new alpha" in (result.portfolio.root / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert (result.portfolio.root / "gamma" / "scripts" / "run.py").is_file()


def test_modified_legacy_curated_skill_does_not_require_new_triggers(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "alpha", "old", complete=False)
    parent = _ref(parent_root, curated=("alpha",))
    patch = """diff --git a/alpha/SKILL.md b/alpha/SKILL.md
--- a/alpha/SKILL.md
+++ b/alpha/SKILL.md
@@ -4,4 +4,4 @@ description: Use for alpha tasks.
 ---
 # alpha
 
-old
+new
"""

    result = apply_portfolio_patch(
        parent,
        patch,
        tmp_path / "candidate",
        target_scope=("alpha/",),
    )

    assert "new" in (result.portfolio.root / "alpha" / "SKILL.md").read_text()


def test_modified_curated_skill_cannot_remove_existing_triggers(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "alpha")
    parent = _ref(parent_root, curated=("alpha",))
    patch = """diff --git a/alpha/SKILL.md b/alpha/SKILL.md
--- a/alpha/SKILL.md
+++ b/alpha/SKILL.md
@@ -1,8 +1,6 @@
 ---
 name: alpha
 description: Use for alpha tasks.
-triggers:
-  - alpha
 ---
 # alpha
 
"""

    with pytest.raises(PortfolioError, match="triggers"):
        apply_portfolio_patch(
            parent,
            patch,
            tmp_path / "candidate",
            target_scope=("alpha/",),
        )


def test_generated_skill_can_be_deleted_but_curated_identity_cannot(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "curated")
    _write_skill(parent_root, "generated")
    parent = _ref(parent_root, curated=("curated",), generated=("generated",))

    deleted = apply_portfolio_patch(
        parent,
        _delete_skill_patch("generated", _skill("generated")),
        tmp_path / "without-generated",
        target_scope=("generated/",),
    )
    assert not (deleted.portfolio.root / "generated").exists()
    assert deleted.portfolio.generated_skill_names == frozenset()

    with pytest.raises(PortfolioError, match="curated"):
        apply_portfolio_patch(
            parent,
            _delete_skill_patch("curated", _skill("curated")),
            tmp_path / "without-curated",
            target_scope=("curated/",),
        )


def test_failed_patch_is_transactional_and_does_not_change_parent(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "alpha", "old")
    parent = _ref(parent_root, curated=("alpha",))
    before = parent.tree_hash
    target = tmp_path / "candidate"

    with pytest.raises(PortfolioError):
        apply_portfolio_patch(
            parent,
            _replace_body_patch("alpha", "missing", "new"),
            target,
            target_scope=("alpha/",),
        )

    assert portfolio_tree_hash(parent_root) == before
    assert not target.exists()


@pytest.mark.parametrize(
    "patch,target_scope",
    [
        (
            "diff --git a/../escape b/../escape\n--- /dev/null\n+++ b/../escape\n@@ -0,0 +1 @@\n+bad\n",
            ("alpha/",),
        ),
        (
            "diff --git a/alpha/link b/alpha/link\nnew file mode 120000\n--- /dev/null\n+++ b/alpha/link\n@@ -0,0 +1 @@\n+../../escape\n",
            ("alpha/",),
        ),
        (
            "diff --git a/alpha/data.bin b/alpha/data.bin\nnew file mode 100644\n--- /dev/null\n+++ b/alpha/data.bin\n@@ -0,0 +1 @@\n+bad\x00data\n",
            ("alpha/",),
        ),
        (
            "diff --git a/alpha/requirements.txt b/alpha/requirements.txt\nnew file mode 100644\n--- /dev/null\n+++ b/alpha/requirements.txt\n@@ -0,0 +1 @@\n+requests\n",
            ("alpha/",),
        ),
        (_replace_body_patch("beta", "old", "changed"), ("alpha/",)),
    ],
)
def test_patch_rejects_unsafe_binary_forbidden_and_out_of_scope_changes(
    tmp_path: Path,
    patch: str,
    target_scope: tuple[str, ...],
) -> None:
    parent_root = tmp_path / "parent"
    _write_skill(parent_root, "alpha", "old")
    _write_skill(parent_root, "beta", "old")
    parent = _ref(parent_root, curated=("alpha", "beta"))

    with pytest.raises(PortfolioError):
        apply_portfolio_patch(
            parent,
            patch,
            tmp_path / "candidate",
            target_scope=target_scope,
        )


def test_curated_seed_is_grandfathered_but_generated_skill_requires_complete_format(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    _write_skill(root, "legacy", complete=False)
    validate_portfolio(root, curated_skill_names=("legacy",), grandfathered_curated=True)

    with pytest.raises(PortfolioError, match="triggers"):
        validate_portfolio(root, generated_skill_names=("legacy",))


def test_portfolio_rejects_symlink_already_present_in_tree(tmp_path: Path) -> None:
    root = tmp_path / "portfolio"
    _write_skill(root, "alpha")
    os.symlink(root / "alpha" / "SKILL.md", root / "alpha" / "alias")

    with pytest.raises(PortfolioError, match="symlink"):
        portfolio_tree_hash(root)


def test_curated_shared_assets_are_hashed_and_editable_but_their_identity_is_protected(tmp_path: Path) -> None:
    root = tmp_path / "portfolio"
    _write_skill(root, "alpha")
    (root / "licenses").mkdir()
    (root / "licenses" / "NOTICE").write_text("notice\n", encoding="utf-8")
    (root / "reference.md").write_text("old reference\n", encoding="utf-8")
    parent = PortfolioRef.from_directory(task_id="task-1", root=root, config_hash="cfg")

    assert parent.curated_asset_paths == frozenset({"licenses", "reference.md"})
    result = apply_portfolio_patch(
        parent,
        """diff --git a/reference.md b/reference.md
--- a/reference.md
+++ b/reference.md
@@ -1 +1 @@
-old reference
+new reference
""",
        tmp_path / "candidate",
        target_scope=("reference.md",),
    )
    assert (result.portfolio.root / "reference.md").read_text(encoding="utf-8") == "new reference\n"

    with pytest.raises(PortfolioError, match="shared asset"):
        apply_portfolio_patch(
            parent,
            """diff --git a/reference.md b/reference.md
deleted file mode 100644
--- a/reference.md
+++ /dev/null
@@ -1 +0,0 @@
-old reference
""",
            tmp_path / "deleted",
            target_scope=("reference.md",),
        )
