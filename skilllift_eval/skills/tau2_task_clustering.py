from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "v2.4"
SUPPORTED_DOMAINS = ("telecom", "airline", "retail")
DEFAULT_MAX_CLUSTER_SIZE = 8
DEFAULT_SEED = 42


@dataclass(frozen=True)
class TaskFeatures:
    task_id: str
    domain: str
    issue_type: str
    difficulty: str
    persona: str | None
    action_signature: str


@dataclass(frozen=True)
class TaskCluster:
    cluster_id: str
    domain: str
    split: str
    issue_type: str
    semantic_key: str
    task_ids: list[str]

    @property
    def size(self) -> int:
        return len(self.task_ids)

    def to_manifest_entry(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "issue_type": self.issue_type,
            "semantic_key": self.semantic_key,
            "task_ids": list(self.task_ids),
            "size": self.size,
        }


def extract_features(domain: str, task_id: str, task_obj: dict[str, Any]) -> TaskFeatures:
    normalized_domain = _normalize_domain(domain)
    task_id_s = str(task_id or task_obj.get("id") or "").strip()
    if normalized_domain == "telecom":
        return _extract_telecom(task_id_s)
    if normalized_domain in {"airline", "retail"}:
        return _extract_action_domain(normalized_domain, task_id_s, task_obj)
    raise ValueError(f"unsupported domain: {domain}")


def cluster_tasks(
    domain: str,
    split: str,
    features: list[TaskFeatures],
    max_cluster_size: int = DEFAULT_MAX_CLUSTER_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[TaskCluster]:
    del seed  # The algorithm is deterministic by sorting; seed is kept in the public contract.
    normalized_domain = _normalize_domain(domain)
    if max_cluster_size <= 0:
        raise ValueError("max_cluster_size must be positive")

    grouped: dict[str, list[TaskFeatures]] = defaultdict(list)
    for feature in features:
        if feature.domain != normalized_domain:
            raise ValueError(f"feature domain mismatch: {feature.domain} != {normalized_domain}")
        grouped[_semantic_key(feature)].append(feature)

    clusters: list[TaskCluster] = []
    for semantic_key in sorted(grouped):
        ordered = sorted(grouped[semantic_key], key=lambda item: _stable_task_sort_key(item.task_id))
        for index, chunk in enumerate(_chunks(ordered, max_cluster_size)):
            task_ids = [feature.task_id for feature in chunk]
            clusters.append(
                TaskCluster(
                    cluster_id=f"{normalized_domain}_{_safe_id(semantic_key)}_{index:02d}",
                    domain=normalized_domain,
                    split=split,
                    issue_type=semantic_key,
                    semantic_key=semantic_key,
                    task_ids=task_ids,
                )
            )
    return clusters


def build_cluster_manifest(
    tau2_domains_root: Path,
    *,
    split: str,
    phase: str,
    domains: tuple[str, ...] = SUPPORTED_DOMAINS,
    max_cluster_size: int = DEFAULT_MAX_CLUSTER_SIZE,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    root = Path(tau2_domains_root)
    manifest_domains: dict[str, list[dict[str, Any]]] = {}
    for domain in domains:
        normalized_domain = _normalize_domain(domain)
        task_ids = _load_split_ids(root / normalized_domain, split)
        tasks = _load_tasks_by_id(root / normalized_domain)
        missing = [task_id for task_id in task_ids if task_id not in tasks]
        if missing:
            raise ValueError(f"{normalized_domain} {split} split references missing tasks: {missing[:3]}")

        features = [
            extract_features(normalized_domain, task_id, tasks[task_id])
            for task_id in task_ids
        ]
        clusters = cluster_tasks(
            normalized_domain,
            split,
            features,
            max_cluster_size=max_cluster_size,
            seed=seed,
        )
        manifest_domains[normalized_domain] = [cluster.to_manifest_entry() for cluster in clusters]

    return {
        "version": MANIFEST_VERSION,
        "split": split,
        "phase": phase,
        "seed": seed,
        "embedding_model": None,
        "domains": manifest_domains,
    }


def write_cluster_manifest(manifest: dict[str, Any], path: Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _extract_telecom(task_id: str) -> TaskFeatures:
    match = re.match(r"^\[([^\]]+)\]([^\[]*)\[PERSONA:([^\]]*)\]$", task_id)
    if not match:
        return TaskFeatures(
            task_id=task_id,
            domain="telecom",
            issue_type="unknown",
            difficulty="medium",
            persona=None,
            action_signature="unknown",
        )

    issue_type = match.group(1).strip() or "unknown"
    faults = [fault for fault in match.group(2).split("|") if fault]
    raw_persona = match.group(3).strip()
    persona = None if raw_persona in {"", "None", "none"} else raw_persona
    return TaskFeatures(
        task_id=task_id,
        domain="telecom",
        issue_type=issue_type,
        difficulty=_difficulty_from_fault_count(len(faults)),
        persona=persona,
        action_signature=issue_type,
    )


def _extract_action_domain(domain: str, task_id: str, task_obj: dict[str, Any]) -> TaskFeatures:
    actions = task_obj.get("evaluation_criteria", {}).get("actions", [])
    action_names = [
        str(action.get("name") or "")
        for action in actions
        if isinstance(action, dict)
    ]
    issue_type = _dominant_action_verb(action_names)
    return TaskFeatures(
        task_id=task_id,
        domain=domain,
        issue_type=issue_type,
        difficulty=_difficulty_from_action_count(len(action_names)),
        persona=None,
        action_signature=issue_type,
    )


def _dominant_action_verb(action_names: list[str]) -> str:
    verbs = Counter(name.split("_", 1)[0] for name in action_names if name)
    if not verbs:
        return "noop"
    return verbs.most_common(1)[0][0]


def _semantic_key(feature: TaskFeatures) -> str:
    return feature.action_signature or feature.issue_type


def _difficulty_from_fault_count(count: int) -> str:
    if count <= 2:
        return "easy"
    if count <= 5:
        return "medium"
    return "hard"


def _difficulty_from_action_count(count: int) -> str:
    if count <= 1:
        return "easy"
    if count <= 5:
        return "medium"
    return "hard"


def _load_split_ids(domain_root: Path, split: str) -> list[str]:
    payload = json.loads((domain_root / "split_tasks.json").read_text(encoding="utf-8"))
    if split not in payload:
        raise ValueError(f"split {split!r} not found in {domain_root / 'split_tasks.json'}")
    return [str(task_id) for task_id in payload[split]]


def _load_tasks_by_id(domain_root: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads((domain_root / "tasks.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"tasks.json must contain a list: {domain_root / 'tasks.json'}")
    return {str(task["id"]): task for task in payload}


def _chunks(items: list[TaskFeatures], size: int) -> list[list[TaskFeatures]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _stable_task_sort_key(task_id: str) -> tuple[int, int | str]:
    if task_id.isdigit():
        return (0, int(task_id))
    return (1, task_id)


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return safe or "cluster"


def _normalize_domain(domain: str) -> str:
    normalized = str(domain).strip()
    if normalized not in SUPPORTED_DOMAINS:
        raise ValueError(f"unsupported domain: {domain}")
    return normalized
