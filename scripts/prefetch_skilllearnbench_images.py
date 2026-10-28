#!/usr/bin/env python3
"""Pre-pull base images and pre-build every SkillLearnBench task image (WSL/Linux or Windows Git Bash).

正式实验中，experiments/revision2026/docker_agent.base_image() 先执行
`docker image inspect <skilllift-slb-...>`：镜像已存在就直接复用；不存在才现场
`docker build`，构建过程要访问 Docker Hub、apt、PyPI、GitHub、Hugging Face 等。
网络一抖，该次 rollout 就记为 ERROR。

本脚本提前调用同一个 base_image()，所以镜像名与正式运行一致，使用 no_skill、空 skills/。
可选 --ubuntu-mirror 仅在临时上下文插入换源步骤，记录在镜像 labels 和清单中；
不修改锁定的上游源码。构建完成后，正式实验复用已有镜像。

本文件刻意放在 scripts/ 而不是 experiments/revision2026/：后者的 *.py 参与 run 的
driver_hash，在那里新增文件会让已有 run-dir 拒绝续跑。本脚本只构建镜像，不改变任何
实验代码、协议或任务源码。

常用（在仓库根目录执行；Windows Git Bash 用 Scripts/python.exe，WSL/Linux 用 bin/python）：
  PY=.venv-revision2026/Scripts/python.exe      # WSL/Linux: PY=.venv-revision2026/bin/python
  $PY scripts/prefetch_skilllearnbench_images.py --pilot             # 先只做 pilot 用到的 2 个镜像
  $PY scripts/prefetch_skilllearnbench_images.py --jobs 3            # 全部 100 个实例镜像
  $PY scripts/prefetch_skilllearnbench_images.py --check             # 只检查缺哪些，不下载
  $PY scripts/prefetch_skilllearnbench_images.py --mirror docker.m.daocloud.io \\
      --build-arg HTTP_PROXY=http://host.docker.internal:7890 \\
      --build-arg HTTPS_PROXY=http://host.docker.internal:7890
  $PY scripts/prefetch_skilllearnbench_images.py --save ~/data/slb-images.tar   # 额外导出离线包
  $PY scripts/prefetch_skilllearnbench_images.py --load ~/data/slb-images.tar   # 在另一台机器导入
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.revision2026 import docker_agent  # noqa: E402
from experiments.revision2026.common import digest, ensure_utf8_mode, load_protocol, utc_now, write_json  # noqa: E402
from experiments.revision2026.sources import PINS, instances, prepare_sources  # noqa: E402

CACHE = ROOT / ".cache" / "revision2026"
WORK = CACHE / "image-prefetch"          # base_image() 把锁和 build log 放在 WORK/image-locks/
MANIFEST = CACHE / "skilllearnbench_images.json"
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY")
FROM_RE = re.compile(r"^\s*FROM\s+(.+?)\s*$", re.IGNORECASE)
_print_lock = threading.Lock()


def log(message):
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(args, timeout=3600):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout after {timeout}s: {' '.join(args[:3])}")


def tail(result, lines=3):
    text = (result.stderr or result.stdout or "").strip().splitlines()
    return " | ".join(text[-lines:])[:400]


def expected_name(instance):
    """Must equal docker_agent.base_image(); re-checked against its return value after each build."""
    return "skilllift-slb-" + digest({"source": PINS["skilllearnbench"][1], "task": instance.task_id})[:20]


def image_exists(name):
    return run(["docker", "image", "inspect", name], timeout=120).returncode == 0


def image_info(name):
    result = run(["docker", "image", "inspect", name], timeout=120)
    if result.returncode:
        return None, None, {}
    info = json.loads(result.stdout)[0]
    return info["Id"], int(info["Size"]), info.get("Config", {}).get("Labels") or {}


# ---------------------------------------------------------------- base images

def base_refs(dockerfile):
    """[(platform or None, image)] for external FROM lines; skips scratch and earlier stage aliases."""
    stages, refs = set(), []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        match = FROM_RE.match(line)
        if not match:
            continue
        plat, words = None, []
        for token in match.group(1).split():
            if token.startswith("--platform="):
                plat = token.split("=", 1)[1]
            elif not token.startswith("--"):
                words.append(token)
        if not words:
            continue
        image = words[0]
        if "$" in image:
            log(f"WARN {dockerfile}: FROM 使用了构建参数（{image}），无法预拉取，构建时会自行解析")
        elif image.lower() != "scratch" and image.lower() not in stages:
            refs.append((plat, image))
        if len(words) >= 3 and words[1].lower() == "as":
            stages.add(words[2].lower())
    return refs


def mirror_ref(image, mirror):
    """docker.io 镜像改写成镜像源地址；其他 registry 的镜像不改写。"""
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return None
    path = image if "/" in image else "library/" + image
    return mirror.strip().rstrip("/").removeprefix("https://").removeprefix("http://") + "/" + path


def pull_base(plat, image, mirrors, retries, force):
    if not force and image_exists(image):
        log(f"base  {image}: 已存在，跳过")
        return {"image": image, "platform": plat, "status": "cached"}
    candidates = [mirror_ref(image, m) for m in mirrors] + [image]
    candidates = [c for c in dict.fromkeys(candidates) if c]
    error = ""
    for attempt in range(1, retries + 1):
        for ref in candidates:
            args = ["docker", "pull"] + (["--platform", plat] if plat else []) + [ref]
            result = run(args)
            if result.returncode == 0:
                if ref != image:
                    tagged = run(["docker", "tag", ref, image], timeout=120)
                    if tagged.returncode:
                        error = tail(tagged)
                        continue
                    run(["docker", "image", "rm", ref], timeout=120)   # 只去掉镜像源别名标签，层保留
                log(f"base  {image}: 拉取成功（来源 {ref}，第 {attempt} 次）")
                return {"image": image, "platform": plat, "status": "pulled", "source": ref, "attempts": attempt}
            error = tail(result)
            log(f"base  {image}: 从 {ref} 拉取失败（第 {attempt}/{retries} 次）：{error}")
        if attempt < retries:
            time.sleep(min(60, 10 * attempt))
    return {"image": image, "platform": plat, "status": "failed", "error": error}


# ---------------------------------------------------------------- task images

def ubuntu_mirror_url(value):
    """Accept a repository URL, never arbitrary sed/shell syntax or credentials."""
    value = value.rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or not re.fullmatch(r"https?://[A-Za-z0-9.:-]+/[A-Za-z0-9/_-]+", value)):
        raise argparse.ArgumentTypeError("需要完整 Ubuntu 仓库 URL，例如 http://mirrors.ustc.edu.cn/ubuntu")
    return value


def mirror_dockerfile(source, mirror):
    """Only edit the disposable build context; leave pinned upstream files intact.

    The resulting image keeps its mirror configuration for the official verifier.
    Package lists, package versions, signatures and verifier code are not rewritten.
    A mirror can lag upstream, so record the configuration instead of claiming
    byte-identical images. Debian-based Python images are deliberately untouched.
    """
    mirror = ubuntu_mirror_url(mirror)
    host = urlsplit(mirror).hostname
    expressions = (
        f"s@https?://([A-Za-z0-9-]+\\.)?archive\\.ubuntu\\.com/ubuntu/?@{mirror}/@g; "
        f"s@https?://security\\.ubuntu\\.com/ubuntu/?@{mirror}/@g"
    )
    direct = (f'Acquire::http::Proxy::{host} "DIRECT";\\n'
              f'Acquire::https::Proxy::{host} "DIRECT";\\n')
    setup = (
        "RUN for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list "
        "/etc/apt/sources.list.d/*.sources; do "
        'if [ -f "$f" ]; then sed -i -E ' + shlex.quote(expressions) + ' "$f"; fi; done'
        " && printf '%b' " + shlex.quote(direct)
        + " > /etc/apt/apt.conf.d/99skilllift-mirror-direct\n"
    )
    label = ("LABEL org.skilllift.ubuntu-mirror=" + json.dumps(mirror)
             + " org.skilllift.upstream-dockerfile-sha256="
             + json.dumps(hashlib.sha256(source.encode("utf-8")).hexdigest()) + "\n")
    output = []
    for line in source.splitlines(keepends=True):
        output.append(line)
        match = FROM_RE.match(line)
        if match:
            words = [word for word in match.group(1).split() if not word.startswith("--")]
            if words and re.fullmatch(r"(?:docker\.io/(?:library/)?)?ubuntu[:@].+", words[0]):
                if not line.endswith("\n"):
                    output.append("\n")
                output.extend((setup, label))
    return "".join(output)


def resilient_download_dockerfile(source):
    """Make the Druid build resilient to unreliable upstream downloads."""
    official = "https://archive.apache.org/dist/druid/0.20.0/apache-druid-0.20.0-bin.tar.gz"
    mirror = "https://mirrors.huaweicloud.com/apache/druid/0.20.0/apache-druid-0.20.0-bin.tar.gz"
    updated = re.sub(
        r"(?<![A-Za-z0-9_-])wget(?=\s+https://archive\.apache\.org/dist/druid/)",
        "sh -c 'until wget --continue --tries=1 --waitretry=3 --timeout=60 --read-timeout=60",
        source,
    )
    # Pin the checksum published alongside the official Apache archive.
    separator = " && " + chr(92) + "\n"
    replacement = (
        mirror + "; do sleep 3; done' && echo "
        + "'67fbb50071d09bc3328fe2172ee6f27ce1d960a2ce11fa863a38173897126d5e0009495da3f145d1ad110e65db6d6a7e8f37ea2204a2f76a89e10886f6d2aa41  apache-druid-0.20.0-bin.tar.gz' "
        + "| sha512sum -c - && " + chr(92) + "\n"
    )
    updated = updated.replace(official + separator, replacement, 1)
    # Maven tries repository.apache.org snapshots before Central for many stable
    # artifacts. That endpoint is unreachable from some networks; resolve through
    # Central directly in this disposable build context instead.
    maven_settings = (
        "RUN printf '%s\\n' '<settings><mirrors><mirror><id>central-fast</id>"
        "<mirrorOf>*</mirrorOf><url>https://repo.maven.apache.org/maven2</url>"
        "</mirror></mirrors></settings>' > /tmp/maven-settings.xml\n"
    )
    updated = updated.replace(
        "# Verify Maven can now resolve dependencies for indexing-service module\n",
        maven_settings + "\n# Verify Maven can now resolve dependencies for indexing-service module\n",
        1,
    )
    updated = updated.replace(
        "mvn dependency:resolve -DskipTests -pl indexing-service -am",
        "mvn -s /tmp/maven-settings.xml dependency:resolve -DskipTests -pl indexing-service -am",
        1,
    )
    return updated


def install_build_args(build_args, ubuntu_mirror=None, resilient_downloads=False):
    """Configure temporary contexts and stream Docker output to a live log."""
    original = docker_agent.command

    def command(args, **kwargs):
        if list(args[:2]) == ["docker", "build"]:
            args = list(args)
            context = Path(args[-1])
            name = args[args.index("-t") + 1]
            if ubuntu_mirror or resilient_downloads:
                dockerfile = context / "Dockerfile"
                source = dockerfile.read_text(encoding="utf-8")
                updated = mirror_dockerfile(source, ubuntu_mirror) if ubuntu_mirror else source
                if resilient_downloads:
                    updated = resilient_download_dockerfile(updated)
                dockerfile.write_text(updated, encoding="utf-8", newline="\n")
                archive = WORK / "dockerfiles" / (name + ".Dockerfile")
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_text(updated, encoding="utf-8", newline="\n")
            extra = []
            for item in build_args:
                extra += ["--build-arg", item]
            args = args[:2] + ["--progress=plain"] + extra + args[2:]
            path = WORK / "image-locks" / (name + ".live.log")
            path.parent.mkdir(parents=True, exist_ok=True)
            log(f"{name}: 实时日志 {path}")
            with path.open("w", encoding="utf-8") as handle:
                result = subprocess.run(args, stdout=handle, stderr=subprocess.STDOUT, **kwargs)
            return subprocess.CompletedProcess(args, result.returncode,
                                              path.read_text(encoding="utf-8", errors="replace"), "")
        return original(args, **kwargs)

    docker_agent.command = command


def build_task(instance, retries, force):
    name = expected_name(instance)
    row = {"task_id": instance.task_id, "family": instance.family, "instance": instance.number,
           "split": "train" if instance.number == 1 else "test", "image": name}
    if image_exists(name):
        if not force:
            row["status"] = "cached"
            log(f"task  {instance.task_id}: 已存在 {name}，跳过")
            return row
        run(["docker", "image", "rm", name], timeout=300)
    build_log = WORK / "image-locks" / (name + ".build.log")
    started = time.monotonic()
    for attempt in range(1, retries + 1):
        log(f"task  {instance.task_id}: 开始构建（第 {attempt}/{retries} 次）")
        try:
            built = docker_agent.base_image(instance, WORK)
            if built != name:
                raise RuntimeError(f"镜像名不一致：base_image()={built}，脚本预期={name}。请同步更新 expected_name()")
            row.update(status="built", attempts=attempt, seconds=round(time.monotonic() - started, 1))
            log(f"task  {instance.task_id}: 构建完成 {name}（{row['seconds']}s）")
            return row
        except Exception as exc:   # build 失败、超时都重试；docker 层缓存让重试从断点继续
            row["error"] = f"{type(exc).__name__}: {exc}"
            log(f"task  {instance.task_id}: 构建失败（第 {attempt}/{retries} 次）：{row['error'][:300]}")
            if "镜像名不一致" in row["error"]:
                break
            if attempt < retries:
                time.sleep(min(120, 15 * attempt))
    row.update(status="failed", attempts=attempt, build_log=str(build_log),
               seconds=round(time.monotonic() - started, 1))
    return row


# ---------------------------------------------------------------- main

def select(args):
    items = instances()   # 同时校验 instruction blob 与固定版本一致
    if args.pilot:
        families = set(load_protocol()["skilllearnbench"]["pilot_families"])
        items = [x for x in items if x.family in families and x.number == 1]
    if args.family:
        unknown = set(args.family) - {x.family for x in items}
        if unknown:
            raise SystemExit(f"未知 family：{', '.join(sorted(unknown))}")
        items = [x for x in items if x.family in set(args.family)]
    if args.split == "train":
        items = [x for x in items if x.number == 1]
    elif args.split == "test":
        items = [x for x in items if x.number > 1]
    return items


def preconditions():
    if platform.system() not in {"Linux", "Windows"}:
        raise SystemExit("请在 WSL/Linux 或 Windows Git Bash 中运行（与正式实验相同的 Docker 环境）。")
    if sys.version_info < (3, 11):
        raise SystemExit("需要 Python 3.11+；请使用 setup.sh 建立的 .venv-revision2026 里的 Python。")
    if run(["docker", "info"], timeout=60).returncode:
        raise SystemExit("无法连接 Docker。请先启动 Docker Desktop" +
                         ("（Linux containers 模式）。" if platform.system() == "Windows"
                          else "，并在 Settings > Resources > WSL Integration 启用本发行版。"))


def main(argv=None):
    p = argparse.ArgumentParser(description="预拉取基础镜像并预构建 SkillLearnBench 任务镜像（与正式运行同名）")
    p.add_argument("--jobs", type=int, default=2, help="并行构建数（默认 2；带宽/磁盘慢时用 1）")
    p.add_argument("--retries", type=int, default=3, help="每个镜像的拉取/构建重试次数（默认 3）")
    p.add_argument("--split", choices=["all", "train", "test"], default="all", help="train=各家族实例1；test=其余80个")
    p.add_argument("--family", action="append", help="只处理指定任务家族，可重复")
    p.add_argument("--pilot", action="store_true", help="只处理 pilot 用到的训练实例（protocol.json 的 pilot_families）")
    p.add_argument("--mirror", action="append", default=[],
                   help="Docker Hub 镜像源主机，例如 docker.m.daocloud.io；可重复，按顺序尝试，最后回退官方源")
    p.add_argument("--build-arg", action="append", default=[], metavar="KEY=VALUE",
                   help="附加到 docker build，通常是 HTTP_PROXY/HTTPS_PROXY/NO_PROXY")
    p.add_argument("--proxy-from-env", action="store_true",
                   help="把当前 shell 的 HTTP(S)_PROXY/NO_PROXY/ALL_PROXY 作为 --build-arg 传给构建")
    p.add_argument("--ubuntu-mirror", type=ubuntu_mirror_url,
                   help="Ubuntu apt 仓库 URL；同时切换 archive/security 并让该主机直连；只改临时构建上下文")
    p.add_argument("--resilient-downloads", action="store_true",
                   help="在临时 Dockerfile 中为大体积官方 Apache Druid 下载启用断点续传和自动重试")
    p.add_argument("--force", action="store_true", help="删除并重建已存在的任务镜像、重新拉取基础镜像")
    p.add_argument("--check", action="store_true", help="只检查镜像是否齐全，不下载、不构建；缺失时退出码 1")
    p.add_argument("--save", type=Path, help="完成后用 docker save 导出全部所选镜像到该 tar（离线备份/迁移）")
    p.add_argument("--load", type=Path, help="先 docker load 一个之前 --save 的 tar，再检查/补齐")
    p.add_argument("--no-prepare", action="store_true", help="不自动获取固定版本 SkillLearnBench 源码（默认会自动获取/校验）")
    args = p.parse_args(argv)
    if args.jobs < 1 or args.retries < 1:
        raise SystemExit("--jobs 与 --retries 必须为正整数")

    preconditions()
    if args.load:
        log(f"docker load -i {args.load} …")
        loaded = run(["docker", "load", "-i", str(args.load)], timeout=7200)
        if loaded.returncode:
            raise SystemExit("docker load 失败：" + tail(loaded))
        log(loaded.stdout.strip().splitlines()[-1] if loaded.stdout.strip() else "load 完成")
    if not args.no_prepare:
        prepare_sources(["skilllearnbench"])   # 已有正确 checkout 时只校验，不联网
    items = select(args)
    if not items:
        raise SystemExit("没有选中任何实例")

    bases = {}
    for item in items:
        for plat, image in base_refs(item.path / "environment" / "Dockerfile"):
            bases.setdefault((plat, image), None)
    log(f"选中 {len(items)} 个实例；基础镜像 {len(bases)} 个：" + ", ".join(i for _, i in bases))

    if args.check:
        missing_bases = [i for _, i in bases if not image_exists(i)]
        missing = [x for x in items if not image_exists(expected_name(x))]
        for item in missing:
            print(f"MISSING  {item.task_id}  {expected_name(item)}")
        print(json.dumps({"instances": len(items), "task_images_present": len(items) - len(missing),
                          "task_images_missing": len(missing), "base_images_missing": missing_bases}, ensure_ascii=False, indent=2))
        return 1 if missing else 0

    build_args = list(args.build_arg)
    if args.proxy_from_env:
        for key in PROXY_VARS:
            value = os.environ.get(key) or os.environ.get(key.lower())
            if value and not any(a.split("=", 1)[0] == key for a in build_args):
                build_args.append(f"{key}={value}")
    install_build_args(build_args, args.ubuntu_mirror, args.resilient_downloads)
    if args.ubuntu_mirror:
        log(f"Ubuntu apt 源：{args.ubuntu_mirror}（直连；Debian/Python 基础镜像不受影响）")
        log("镜像会保留换源配置供 verifier 使用；已有镜像仍复用，来源记录在镜像 labels 和结果清单中")
    if build_args:
        log("docker build 附加参数：" + ", ".join(a.split("=", 1)[0] for a in build_args))

    # 1) 基础镜像：任务镜像都已存在时无需拉取
    todo = [x for x in items if args.force or not image_exists(expected_name(x))]
    base_rows = []
    if todo:
        needed = {}
        for item in todo:
            for plat, image in base_refs(item.path / "environment" / "Dockerfile"):
                needed.setdefault((plat, image), None)
        for plat, image in needed:
            base_rows.append(pull_base(plat, image, args.mirror, args.retries, args.force))
    else:
        log("所选任务镜像均已存在，跳过基础镜像拉取")

    # 2) 任务镜像：调用与正式运行相同的 base_image()
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(build_task, item, args.retries, args.force) for item in items]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda r: (r["family"], r["instance"]))
    for row in rows:
        if row["status"] != "failed":
            row["image_id"], row["size_bytes"], labels = image_info(row["image"])
            row["ubuntu_mirror"] = labels.get("org.skilllift.ubuntu-mirror")
            row["upstream_dockerfile_sha256"] = labels.get("org.skilllift.upstream-dockerfile-sha256")

    previous = {}
    if MANIFEST.exists():
        try:
            previous = {r["task_id"]: r for r in json.loads(MANIFEST.read_text())["task_images"]}
        except (ValueError, KeyError):
            previous = {}
    previous.update({r["task_id"]: r for r in rows})
    write_json(MANIFEST, {"updated_at": utc_now(), "skilllearnbench_revision": PINS["skilllearnbench"][1],
                          "note": "Images are built by experiments.revision2026.docker_agent.base_image() with the same names used at run time.",
                          "base_images": base_rows, "task_images": sorted(previous.values(), key=lambda r: r["task_id"])})

    failed = [r for r in rows if r["status"] == "failed"]
    failed_bases = [r for r in base_rows if r["status"] == "failed"]
    counts = {s: sum(r["status"] == s for r in rows) for s in ("built", "cached", "failed")}
    total_bytes = sum(r.get("size_bytes") or 0 for r in rows)
    log(f"完成：新构建 {counts['built']}，已存在 {counts['cached']}，失败 {counts['failed']}；"
        f"清单 {MANIFEST.relative_to(ROOT)}")
    log(f"所选镜像名义大小合计约 {total_bytes / 1e9:.1f} GB（共享层只占一次磁盘，实际占用更小）")
    for row in failed:
        log(f"FAILED {row['task_id']}: {row.get('error', '')[:300]}  日志：{row.get('build_log')}")
    for row in failed_bases:
        log(f"FAILED base {row['image']}: {row.get('error', '')}")

    if args.save:
        names = [r["image"] for r in rows if r["status"] != "failed"]
        names += [image for _, image in bases if image_exists(image)]
        if names:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            log(f"docker save → {args.save}（{len(names)} 个标签，层自动去重）…")
            saved = run(["docker", "save", "-o", str(args.save)] + names, timeout=14400)
            if saved.returncode:
                log("docker save 失败：" + tail(saved))
                return 1
            log(f"已导出 {args.save}（{args.save.stat().st_size / 1e9:.1f} GB）")

    if failed:
        log("可直接重跑同一命令：已成功的镜像会跳过，失败的从 docker 层缓存断点继续。")
        return 1
    return 0


if __name__ == "__main__":
    ensure_utf8_mode([str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(main())
