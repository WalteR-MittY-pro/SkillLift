---
name: paper-to-loss-implementation
description: Translate a paper's pairwise preference loss into a numerically stable trainer implementation and reproducible test artifact.
---

# paper-to-loss-implementation

## Workflow
1. Read the paper's actual loss equation and definitions of sequence log-probability normalization, margin, beta, and any auxiliary term. Cross-check names and defaults with the repository's trainer/config implementation; do not infer an equation from a method name.
2. Trace tensor shapes, dtype, device, reduction, and return contract from the method and supplied test. Keep a per-example loss vector when the caller expects it; use torch.nn.functional.logsigmoid for stable logarithms.
3. Implement the exact paper equation using framework tensor operations without detaching gradients. Reward outputs should follow the existing trainer convention. Check chosen/rejected swap behavior and a simple hand-computed scalar example.
4. Run the supplied test unchanged in the intended environment. Confirm the output archive key, shape, finiteness, and deterministic inputs. Record python -VV and python -m pip freeze for reproduction.

## Example pattern
    margin = beta * (chosen_logp - rejected_logp) - gamma
    losses = -torch.nn.functional.logsigmoid(margin)

This is a generic pattern only: confirm the paper's normalization and placement of constants before using it.
