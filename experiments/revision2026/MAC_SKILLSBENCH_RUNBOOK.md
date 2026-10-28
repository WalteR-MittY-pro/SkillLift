# 在 Mac 单独运行 SkillsBench 消融

使用分支 `codex/supplementary-experiments-linux`。本机的 `.env`、虚拟环境、镜像、下载缓存和实验结果不随 Git 提交；在 Mac 建立自己的环境。代码已在 WSL 通过回归测试，尚未在真实 Mac 上完成端到端验证。

## 准备

安装可用的 Python 3.12+ 和 Git，启动 Docker Desktop，确认以下命令成功。命令均在仓库根目录执行：

```bash
git switch codex/supplementary-experiments-linux
git pull --ff-only
docker info
docker compose version
bash experiments/revision2026/setup.sh skillsbench
```

`skillsbench` 安装范围只准备固定版本的 SkillsBench 和 SkillOpt 源码，安装框架及 SkillsBench 依赖；不下载 SkillLearnBench 或 WildClaw 的任务素材。无需手动激活虚拟环境，后续明确使用 `.venv-revision2026/bin/python`。

在仓库根目录私下配置 `.env`，字段见 `experiments/revision2026/.env.example`。从现有机器转移配置时使用私密方式；不要提交密钥。模型请求 ID 按 `.env` 实际值读取，DeepSeek 当前配置为 `deepseek-flash`。

## 预热 87 个任务镜像

```bash
SKILLSBENCH_TASKS_DIR="$PWD/.cache/revision2026/skillsbench/tasks"   PARALLEL=4 MAX_RETRY=3 bash scripts/prewarm.sh
```

已有同名 `prewarm/<任务>:prewarm` 镜像会复用。Mac 的 Docker 镜像存储与 Windows 独立，需要在本机预热或另行导入。Apple Silicon 如遇仅支持 x86_64 的任务依赖，应在 Docker Desktop 配置相应仿真，并保持各算法使用一致的镜像与平台；跨架构的端到端表现仍需在 Mac 核验。

## 单独运行消融

不要使用 `run_all.sh supplementary`，该入口会同时安排 SkillLearnBench 泛化。消融专用命令如下，两个模型可分别在两个终端运行：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 ablation   --models glm --workers 4 --run-dir runs/skillsbench-ablation/glm
```

```bash
.venv-revision2026/bin/python -m experiments.revision2026 ablation   --models deepseek --workers 4 --run-dir runs/skillsbench-ablation/deepseek
```

默认使用 `experiments/revision2026/protocol.json`。GLM 只跑 Direct Oracle，与论文 SkillLift 75.1 的历史成绩对照；DeepSeek 跑 SkillLift 和 Direct Oracle，并做独立复测。没有更改该算法范围，也不把历史成绩当作相同实验条件下的新测量。

首次运行可加 `--plan-only` 查看计划，不调用模型或 Docker。正式启动前移除此参数。此消融默认 solver 输出预算为 16384 token；SkillLearnBench 的 thinking-32768 专用启动器不参与此命令。

中断后在同一目录重新执行相同命令，已完成任务跳过，失败和中断任务继续处理。后台执行可在原命令前加 `nohup`、末尾加日志重定向及 `&`；避免同时启动两份相同队列。

报告命令：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 report   --run-dir runs/skillsbench-ablation/glm
.venv-revision2026/bin/python -m experiments.revision2026 report   --run-dir runs/skillsbench-ablation/deepseek
```

输出目录包含任务日志、逐轮曲线、过程最好分、覆盖率及成本报告。失败不作为有效零分，缺失结果需要补齐后才能形成完整比较。
