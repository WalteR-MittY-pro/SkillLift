"""Explicit execution-machine-only preparation of the official OpenClaw image/data."""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess

from .common import ROOT, write_json
from .sources import verify_source

DATASET = "internlm/WildClawBench"
DATASET_REVISION = "75f945578aa00cbdb8f46e4d42e4f4e98f704b4f"
IMAGE_FILE = "Images/wildclawbench-ubuntu_v1.3.tar"
IMAGE_TAG = "wildclawbench-ubuntu:v1.3"


def prepare():
    if platform.system() != "Linux":
        raise ValueError("Run image/data preparation inside the target WSL/Linux environment")
    from huggingface_hub import hf_hub_download, snapshot_download
    root = verify_source("wildclawbench")
    subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, check=True)
    if subprocess.run(["docker", "image", "inspect", IMAGE_TAG], capture_output=True).returncode:
        archive = hf_hub_download(repo_id=DATASET, repo_type="dataset", revision=DATASET_REVISION, filename=IMAGE_FILE)
        subprocess.run(["docker", "load", "-i", archive], check=True)
    subprocess.run(["docker", "image", "inspect", IMAGE_TAG], stdout=subprocess.DEVNULL, check=True)
    snapshot_download(repo_id=DATASET, repo_type="dataset", revision=DATASET_REVISION,
                      allow_patterns=["workspace/**"], local_dir=str(root))
    missing = [name for name in ["ffmpeg", "yt-dlp", "modelscope"] if shutil.which(name) is None]
    if missing:
        raise ValueError("Workspace downloaded; official media preparation still needs: " + ", ".join(missing) + ". See README.md; rerun this command after installing them.")
    subprocess.run(["bash", "script/prepare.sh"], cwd=root, check=True)
    tracked = []
    for pattern in ["**/first_half.mp4", "**/video.mp4", "**/recording.mp4", "**/sam3.pt"]:
        for path in sorted((root / "workspace").glob(pattern)):
            sha = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
                    sha.update(chunk)
            tracked.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha.hexdigest()})
    write_json(ROOT / ".cache/revision2026/wildclaw_data_manifest.json", {"dataset": DATASET, "revision": DATASET_REVISION,
        "image_tag": IMAGE_TAG, "external_media_files": tracked,
        "note": "Official prepare.sh downloads video/model material from external services; their actual local hashes are recorded. Source availability and credentials are not simulated."})
