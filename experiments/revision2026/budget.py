"""Oracle-execution bound of the pinned single-instance SkillOpt adapter."""


def skillopt_budget(protocol):
    cfg = protocol["skillopt"]
    epochs, selection = int(cfg["num_epochs"]), int(cfg["sel_env_num"])
    if epochs < 1 or selection < 1:
        raise ValueError("SkillOpt epochs and selection repeats must be positive")
    if cfg.get("use_slow_update") or cfg.get("use_meta_skill"):
        raise ValueError("Slow/meta updates require a new oracle budget audit")
    return {"initial_selection": selection, "training": epochs,
            "candidate_selection_max": epochs * selection,
            "minimum": selection + epochs, "maximum": selection + epochs * (1 + selection)}


class OracleBudget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0

    def consume(self):
        if self.used >= self.limit:
            raise RuntimeError(f"SkillOpt exceeded its audited {self.limit}-execution budget; inspect the upstream call path")
        self.used += 1
