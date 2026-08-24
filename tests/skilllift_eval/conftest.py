import os
import sys
from pathlib import Path

# Resolve `import skilllift` to the algorithm package inside the repo's
# skilllift/ directory, not the directory itself (namespace-package shadowing).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skilllift"))

# Placeholder endpoint values so CLI tests pass on a fresh clone without a
# configured .env (skilllift_eval/config.yaml resolves *_BASE_URL from env).
# Tests that exercise missing-field errors use configs without the env fields.
for _index, _key in enumerate(
    (
        "GPT_MODEL", "GPT_BASE_URL", "GPT_API_KEY",
        "CLAUDE_MODEL", "CLAUDE_BASE_URL", "CLAUDE_API_KEY",
        "GLM_MODEL", "GLM_BASE_URL", "GLM_API_KEY",
        "QWEN_MODEL", "QWEN_BASE_URL", "QWEN_API_KEY",
        "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY",
        "MINIMAX_MODEL", "MINIMAX_BASE_URL", "MINIMAX_API_KEY",
        "OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "JUDGE_MODEL",
        "DEFAULT_MODEL", "MY_PROXY_API_KEY",
    )
):
    os.environ.setdefault(_key, "model-%d" % (_index % 6) if _key.endswith("_MODEL") else "https://example.test/v1" if _key.endswith("_BASE_URL") else "test-key")
