from __future__ import annotations

import json
from pathlib import Path

from skilllift_eval.skills.tau2_task_clustering import (
    build_cluster_manifest,
    cluster_tasks,
    extract_features,
)


ROOT = Path(__file__).resolve().parents[2]
TAU2_DOMAINS = ROOT / "tau2-bench" / "data" / "tau2" / "domains"
DOMAINS = ("telecom", "airline", "retail")


def test_extract_features_uses_domain_specific_signals() -> None:
    telecom_task_id = (
        "[mms_issue]airplane_mode_on|bad_network_preference|bad_wifi_calling"
        "|break_apn_mms_setting[PERSONA:None]"
    )
    telecom = extract_features("telecom", telecom_task_id, {"id": telecom_task_id})
    assert telecom.issue_type == "mms_issue"
    assert telecom.action_signature == "mms_issue"
    assert telecom.persona is None

    airline = extract_features("airline", "0", _task_by_id("airline")["0"])
    assert airline.issue_type == "noop"
    assert airline.action_signature == "noop"

    retail = extract_features("retail", "0", _task_by_id("retail")["0"])
    assert retail.issue_type == "get"
    assert retail.action_signature == "get"


def test_cluster_tasks_keeps_semantic_groups_capacity_bounded_and_stable() -> None:
    train_keys: set[str] = set()
    test_keys: set[str] = set()
    total_train = 0
    total_test = 0
    train_cluster_count = 0
    test_cluster_count = 0

    for domain in DOMAINS:
        domain_tasks = _task_by_id(domain)
        train_ids = _split_ids(domain, "train")
        test_ids = _split_ids(domain, "test")
        total_train += len(train_ids)
        total_test += len(test_ids)
        train_keys.update(f"{domain}:{task_id}" for task_id in train_ids)
        test_keys.update(f"{domain}:{task_id}" for task_id in test_ids)

        train_clusters = _clusters_for(domain, "train")
        test_clusters = _clusters_for(domain, "test")
        train_cluster_count += len(train_clusters)
        test_cluster_count += len(test_clusters)

        assert set(_covered_task_ids(train_clusters)) == set(train_ids)
        assert set(_covered_task_ids(test_clusters)) == set(test_ids)
        assert all(cluster.size <= 8 for cluster in train_clusters + test_clusters)
        assert [cluster.to_manifest_entry() for cluster in train_clusters] == [
            cluster.to_manifest_entry()
            for cluster in cluster_tasks(
                domain,
                "train",
                [extract_features(domain, task_id, domain_tasks[task_id]) for task_id in train_ids],
                max_cluster_size=8,
                seed=42,
            )
        ]

        if domain == "telecom":
            assert {"mms_issue", "mobile_data_issue", "service_issue"} <= {
                cluster.semantic_key for cluster in train_clusters + test_clusters
            }
            assert all(
                feature.persona != "None"
                for task_id in train_ids + test_ids
                for feature in [extract_features(domain, task_id, domain_tasks[task_id])]
            )
        if domain == "airline":
            semantic_keys = {cluster.semantic_key for cluster in train_clusters + test_clusters}
            assert "book" in semantic_keys
            assert "cancel" in semantic_keys
            assert "booking_state_change" not in semantic_keys
            assert "noop" in semantic_keys
            assert "transfer" in semantic_keys
        if domain == "retail":
            assert "booking_state_change" not in {
                cluster.semantic_key for cluster in train_clusters + test_clusters
            }

    assert total_train == 178
    assert total_test == 100
    assert train_cluster_count == 33
    assert test_cluster_count == 22
    assert train_keys.isdisjoint(test_keys)


def test_build_cluster_manifest_is_split_aware_and_reproducible() -> None:
    first = build_cluster_manifest(TAU2_DOMAINS, split="train", phase="train_evolve", seed=42)
    second = build_cluster_manifest(TAU2_DOMAINS, split="train", phase="train_evolve", seed=42)
    test_manifest = build_cluster_manifest(TAU2_DOMAINS, split="test", phase="test_eval", seed=42)

    assert first == second
    assert first["version"] == "v2.4"
    assert first["split"] == "train"
    assert first["phase"] == "train_evolve"
    assert test_manifest["split"] == "test"
    assert test_manifest["phase"] == "test_eval"
    assert set(first["domains"]) == set(DOMAINS)

    encoded_first = json.dumps(first, ensure_ascii=False, sort_keys=True)
    encoded_second = json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert encoded_first == encoded_second

    train_keys = _manifest_task_keys(first)
    test_keys = _manifest_task_keys(test_manifest)
    assert len(train_keys) == 178
    assert len(test_keys) == 100
    assert train_keys.isdisjoint(test_keys)


def _clusters_for(domain: str, split: str):
    domain_tasks = _task_by_id(domain)
    task_ids = _split_ids(domain, split)
    features = [extract_features(domain, task_id, domain_tasks[task_id]) for task_id in task_ids]
    return cluster_tasks(domain, split, features, max_cluster_size=8, seed=42)


def _task_by_id(domain: str) -> dict[str, dict]:
    tasks = json.loads((TAU2_DOMAINS / domain / "tasks.json").read_text(encoding="utf-8"))
    return {str(task["id"]): task for task in tasks}


def _split_ids(domain: str, split: str) -> list[str]:
    split_tasks = json.loads(
        (TAU2_DOMAINS / domain / "split_tasks.json").read_text(encoding="utf-8")
    )
    return [str(task_id) for task_id in split_tasks[split]]


def _covered_task_ids(clusters) -> list[str]:
    return [task_id for cluster in clusters for task_id in cluster.task_ids]


def _manifest_task_keys(manifest: dict) -> set[str]:
    return {
        f"{domain}:{task_id}"
        for domain, clusters in manifest["domains"].items()
        for cluster in clusters
        for task_id in cluster["task_ids"]
    }
