from experiments.revision2026.common import write_json
from experiments.revision2026.verification_audit import audit


def test_verification_audit_keeps_missing_finals_and_scorer_errors_out_of_inference(tmp_path):
    archive, output = tmp_path / "archive", tmp_path / "audit"
    tasks = [f"01_Productivity_Flow_task_{i}_example" for i in range(60)]
    write_json(archive / "batch_summaries/main.batch_manifest.json", {"task_ids": tasks})
    write_json(archive / "final_aggregation.json", {"tasks": [{"task_id": task} for task in tasks], "A_mean": .6, "B_mean": .7})
    cell = archive / "cells/main" / tasks[0]
    write_json(cell / "run_config.json", {"provider_model_id": "GLM-5.1", "baseline": "skilllift", "algorithm_param_hash": "fixed"})
    write_json(cell / "cell_summary.json", {"status": "SUCCEEDED", "stop_reason": "terminal_candidate",
               "final_rewards": [0, 1], "mean_score": 1, "winner_portfolio_hash": "winner"})
    for index, value in enumerate([0, 1, 0]):
        path = archive / "rollouts/main/adapter_artifacts" / tasks[0] / f"final-{index}-a0/benchmark_output/category/task/run/score.json"
        write_json(path, {"overall_score": value, **({"judge_error": "1210"} if index == 0 else {})})
    facts = audit(archive, output)
    assert facts["expected_tasks"] == 60 and facts["tasks_with_any_final"] == 1
    assert len(facts["canonical_missing_final_tasks"]) == 59
    assert facts["raw_final_files"] == 3 and facts["logical_final_values"] == 2
    assert facts["raw_final_scorer_errors"] == 1
    assert facts["canonical_ambiguous_raw_final_tasks"] == [tasks[0]]
    assert facts["formal_full_benchmark_ci"] is None and facts["formal_between_method_p"] is None
    assert len(facts["canonical_tasks_requiring_final_work"]) == 60
