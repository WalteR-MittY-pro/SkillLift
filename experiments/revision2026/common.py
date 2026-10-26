from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def tree_hash(root):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    values = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in frozen skills: {path}")
        if path.is_file():
            values.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(values)


def load_protocol(path=None):
    value = read_json(path or HERE / "protocol.json")
    if value["evolution_runs"] < 1 or value["test_repeats"] < 1:
        raise ValueError("Repeat counts must be positive")
    if value["skilllearnbench"]["train_instance"] != 1:
        raise ValueError("The agreed protocol trains only on instance 1")
    return value


def verify_seeds():
    root = HERE / "seeds"
    manifest = read_json(root / "LLM_SKILLS_FROZEN.json")
    for entry in manifest["artifacts"]:
        path = root / entry["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Frozen GPT-6-SOL skill changed: {path}")
    return manifest


def endpoint(model):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    prefix = {"glm": "GLM", "deepseek": "DEEPSEEK", "gpt": "GPT", "gpt_skillsbench": "GPT_SKILLSBENCH",
              "gpt_wildclaw": "GPT_WILDCLAW", "deepseek_pro": "DEEPSEEK_PRO", "writer": "WRITER", "glm_vision": "GLM_VISION"}[model]
    values = {key: os.environ.get(f"{prefix}_{key}", "") for key in ("MODEL", "BASE_URL", "API_KEY")}
    if model == "glm_vision":
        defaults = {**endpoint("glm"), "MODEL": "glm-5.3-flash"}
        values = {key: value or defaults[key] for key, value in values.items()}
    missing = [f"{prefix}_{key}" for key, value in values.items() if not value]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    from urllib.parse import urlsplit
    url = urlsplit(values["BASE_URL"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query:
        raise ValueError(f"{prefix}_BASE_URL must be an HTTP(S) API base URL without credentials or query parameters")
    return values


def endpoint_identity(model):
    from urllib.parse import urlsplit, urlunsplit
    values = endpoint(model)
    parsed = urlsplit(values["BASE_URL"])
    # Credentials never enter a run manifest. Endpoint routing still affects identity.
    host = parsed.hostname or ""
    if parsed.port:
        host += f":{parsed.port}"
    return {"model": values["MODEL"], "base_url": urlunsplit((parsed.scheme, host, parsed.path, "", ""))}


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each experiment cell owns its ledger; no cross-process append races.
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"recorded_at": utc_now(), **event}, ensure_ascii=False) + "\n")


def latest_protocol(manifest):
    """Protocol currently in force for a run directory (the newest one recorded on resume)."""
    return manifest.get("identity_latest", manifest["identity"])["protocol"]


def require_frozen(marker):
    marker = Path(marker)
    value = read_json(marker)
    if value["status"] != "complete" or tree_hash(marker.parent / "skills") != value["skill_hash"]:
        raise ValueError(f"Incomplete or modified frozen skill: {marker}")
    return value
