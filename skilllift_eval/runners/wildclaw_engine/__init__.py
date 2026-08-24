"""WildClawBench execution engine, decoupled from skilllift.

This package wraps WildClawBench's run_batch pipeline (skill injection,
multi-model config, grading) so that skilllift_eval can run WildClawBench tasks
without depending on the skilllift CoEvo repository. The skilllift
originals are intentionally left in place during the transition; CoEvo switches
to this engine via the skilllift_eval runner (Task 4.3) and the originals are
removed afterwards.
"""

from skilllift_eval.runners.wildclaw_engine import run_batch  # noqa: F401
