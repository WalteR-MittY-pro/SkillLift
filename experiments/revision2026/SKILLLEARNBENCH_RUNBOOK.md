# 先全量运行 SkillLearnBench：WSL 执行手册

本手册对应 `codex/supplementary-experiments-linux` 分支。先完成 SkillLearnBench 上两个模型、四种方法的全部泛化实验，再安排 SkillsBench 的 Direct Oracle 消融。以下命令全部在 WSL Bash、仓库根目录执行。

本次使用独立运行根目录 `runs/skilllearnbench-first-v1`。中断后继续使用这个目录，不要每次启动都生成新的时间戳目录。第 1–9 节不会启动 SkillsBench 消融、WildClaw 或 Table 2 重建。`run_all.sh supplementary` 会同时启动泛化和消融，因此这次使用下面的 `pilot` / `generalization` 独立入口。

## 1. 实验目标与范围

验证：在训练实例上优化技能，冻结后能否提升同一家族未见实例的成绩。这里的训练只修改技能文件，不更新模型权重；结论范围是已见任务家族中的新实例泛化。

| 项目 | 固定设置 |
|---|---|
| Benchmark | 锁定版本的 SkillLearnBench，20 个家族、100 个实例 |
| 训练集 | 每个家族的实例 1，共 20 个 |
| 测试集 | 每个家族其余实例，合计 80 个 |
| 模型 | GLM-5.1、DeepSeek-V4.1-Flash；实际 API 模型 ID 由 `.env` 决定并记录 |
| 方法 | `no_skill`、`llm_written`、`skillopt`、`skilllift` |
| 初始技能 | 已提交并冻结的 20 份 GPT-6-SOL 技能，两模型共用；SkillOpt 与 SkillLift 从相同技能起步 |
| 优化次数 | 每模型、每家族、每优化算法完整优化 1 次 |
| 测试重复 | 每测试实例、每模型、每方法执行 1 次 |
| 并发 | GLM 最多 4；DeepSeek 最多 8；同一 WSL 用户的实验进程共享限制 |
| GLM 图片能力 | 所有 GLM 方法共用 GLM-5.3-Flash 图片解析工具，计入已记录成本 |

`no_skill` 不提供技能；`llm_written` 直接使用冻结技能，不执行优化。当前没有 Human-written 组。这里使用共同的 OpenAI-compatible Docker 求解器和官方环境、验证器，不能把官方其他模型/执行器的成绩当作本次基线。

优化只能使用实例 1。官方检查项名称、通过/失败状态及断言细节不作为优化反馈。最终测试前冻结技能；测试结果不能回流用于修改或选择技能。无技能 30–60 分是原筛选预期，并非已验证结果；不得根据正式测试分数临时更换测试题或修改方法预算。

## 2. 执行阶段与完成条件

| 阶段 | 操作 | 完成条件 |
|---|---|---|
| A | 拉取分支，准备环境和接口 | setup 成功，预检输出 `configuration_ready` |
| B | 只生成两个模型的正式计划 | 每模型 400 个调度任务：80 个训练/冻结任务、320 个测试任务 |
| C | 两模型各跑训练侧 pilot | 各自 `execution_summary.json` 为 `complete`；确认真实接口和容器可运行 |
| D | 两个终端分别启动全量泛化 | 自动完成训练、冻结、测试；GLM 4 / DeepSeek 8 并发 |
| E | 处理失败、重复原命令、检查报告 | 8 个模型×方法结果全部完整，每行 80/80，总计 640 个有效测试结果 |
| F | 归档报告与原始证据 | 保存运行目录、版本和配置记录，随后单独安排消融 |

每个模型内部先处理全部训练/冻结任务，再执行测试。两个模型互不等待：GLM 的训练阶段不阻止 DeepSeek 开始自己的测试。

## 3. WSL 环境准备

在 Windows 启动 Docker Desktop，并开启对应 WSL 发行版的集成。使用 WSL 自己的 Python 3.12+，不要复制 macOS 或 Windows 虚拟环境。建议仓库位于 WSL 文件系统，例如 `~/SkillLift`，避免放在 `/mnt/c`。

首次获取仓库（以下 SSH 地址要求已配置 GitHub 认证）：

```bash
git clone --branch codex/supplementary-experiments-linux git@github.com:WalteR-MittY-pro/SkillLift.git ~/SkillLift
cd ~/SkillLift
```

已有仓库则在其根目录更新；如有本地修改，先保留并处理，不要强制覆盖：

```bash
git status --short
git switch codex/supplementary-experiments-linux
git pull --ff-only
```

确认工具并执行准备脚本：

```bash
python3.12 --version
docker info
docker compose version
REVISION_PYTHON=python3.12 bash experiments/revision2026/setup.sh
```

需要 Git、Python 3.12+ 及 venv 支持、Docker CLI 和 Compose。若缺少系统依赖，按 [README 的 WSL 准备说明](README.md#1-在-wsl-准备环境)处理。`setup.sh` 不安装 Docker；它目前为整个补充实验包获取四个固定源码仓库、安装共享依赖并运行离线测试，这不等于运行其他 benchmark。**本阶段无需执行 `prepare-wildclaw`。** SkillLearnBench 执行时仍会构建/使用任务所需 Docker 镜像，首次构建时间需要单独预留。

已成功准备 `.venv-revision2026` 且代码更新未改变依赖时，可直接进入配置检查。不要在实验运行中重建虚拟环境或更新依赖。

## 4. 配置模型与任务凭据

参考 [`.env.example`](.env.example)，在仓库根目录 `.env` 中填写实际接口。保留已有配置，不要用示例文件覆盖密钥。

| 配置 | 要求 |
|---|---|
| `GLM_MODEL`、`GLM_BASE_URL`、`GLM_API_KEY` | 指向真实 GLM-5.1 接口 |
| `GLM_VISION_MODEL` | `glm-5.3-flash` |
| `GLM_VISION_BASE_URL`、`GLM_VISION_API_KEY` | 留空时继承 GLM 地址和密钥；该端点必须支持视觉模型，否则配置独立端点 |
| `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_API_KEY` | 填服务商对应 V4.1-Flash 的实际模型 ID 与接口，核对别名实际指向 |
| 任务声明的外部服务凭据 | 按锁定任务的 `task.toml` 配置；预检会列出缺失变量，例如 `GH_TOKEN` |

本实验不需要 `WRITER_*`，也不需要历史 Table 2 的 GPT/DeepSeek Pro 配置。模型 ID 字段有值不代表服务商实际路由正确，需结合接口配置及 pilot 日志确认。导出的环境变量优先于 `.env`；修改 `.env` 后仍使用旧配置时，检查 shell 的同名变量，不要打印密钥。

```bash
.venv-revision2026/bin/python -m experiments.revision2026 preflight --models glm deepseek
```

当前通用预检也检查 SkillsBench 的源码与适配器配置，但**不会运行 SkillsBench 任务**。它检查 Docker、源码版本、技能完整性及配置字段，不发送真实模型推理请求，也不证明外部任务服务可访问；pilot 才验证真实执行链路。

## 5. 生成并核对正式计划：不调用模型

运行前确定模型、端点、视觉配置和协议，此后尽量固定。本手册的新目录适合尚未开跑的实验；不要复制不同条件下的旧产物来冒充本次完整结果。

```bash
PY=.venv-revision2026/bin/python
RUN_ROOT=runs/skilllearnbench-first-v1
$PY -m experiments.revision2026 generalization --models glm --workers 4 --run-dir "$RUN_ROOT/glm/generalization" --plan-only
$PY -m experiments.revision2026 generalization --models deepseek --workers 8 --run-dir "$RUN_ROOT/deepseek/generalization" --plan-only
```

核对任务数和训练/测试划分：

```bash
.venv-revision2026/bin/python - <<'PY'
import json
from collections import Counter
from pathlib import Path
root = Path("runs/skilllearnbench-first-v1")
for model in ("glm", "deepseek"):
    plan = json.loads((root / model / "generalization/generalization_plan.json").read_text())
    jobs = plan["jobs"]
    assert len(jobs) == 400
    assert {j["benchmark"] for j in jobs} == {"skilllearnbench"}
    assert {j["model"] for j in jobs} == {model}
    assert {j["method"] for j in jobs} == {"no_skill", "llm_written", "skillopt", "skilllift"}
    assert Counter(j["kind"] for j in jobs) == {"train": 80, "evaluate": 320}
    assert len({j["family"] for j in jobs}) == 20
    assert all(j["instance"] > 1 and j["phase"] == "heldout" for j in jobs if j["kind"] == "evaluate")
    print(model, "PASS: 80 training/freezing jobs + 320 held-out evaluations")
PY
```

80 个训练/冻结任务中，No-skill 和 LLM-written 共 40 个只做冻结，不调用优化器。400 个调度任务不等于 400 次模型请求或容器执行。

## 6. 小规模 pilot：两个模型可以同时进行

Pilot 使用 `court-form-filling` 和 `weighted-gdp-calculation` 的训练实例，覆盖四种方法，优化预算缩小为一轮。它不接触正式测试实例，结果不计入论文总分。Pilot 技能不会替代正式训练技能。

打开两个 WSL 终端，并分别进入仓库根目录。

终端 A：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 pilot --models glm --workers 4 --run-dir runs/skilllearnbench-first-v1/glm/pilot
```

终端 B：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 pilot --models deepseek --workers 8 --run-dir runs/skilllearnbench-first-v1/deepseek/pilot
```

各模型 pilot 结束后，查看对应 `pilot/execution_summary.json`，应为 `complete`。有失败则查看 `pilot/jobs/*.stderr.log` 和单题日志，修复后重跑该模型同一条 pilot 命令。重点检查模型路由、429/404、图片解析、容器评分、单题用时和 token 记录。

Pilot 通过不证明全部 20 类任务及外部服务都可运行，也不能证明正式基线在 30–60 分。每个模型确认自己的 pilot 后即可正式运行，无需等待另一个模型。长时间运行建议使用已有的 tmux 会话，并保持 Docker Desktop 与主机唤醒。

## 7. 全量正式执行：两个终端并行

终端 A，GLM 全部四种方法：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 generalization --models glm --workers 4 --run-dir runs/skilllearnbench-first-v1/glm/generalization
```

终端 B，DeepSeek 全部四种方法：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 generalization --models deepseek --workers 8 --run-dir runs/skilllearnbench-first-v1/deepseek/generalization
```

两条命令各自自动完成训练、冻结、全部测试和统计/成本报告。无需为四种方法分别启动额外批次，也不要通过反复修改方法清单来分别开跑。

GLM 和 DeepSeek 合计最多同时执行 12 个任务，两者配额独立。机器资源不足时，可将 `--workers` 分别降低，例如 2 和 4；不要高于 4 和 8。共享槽位锁位于同一 WSL 用户的 `~/.cache/skilllift/model-slots/`，不跨电脑或不同 WSL 用户协调。

### 运行量与时间预期

| 部分 | 每模型计划量 | 两模型合计 |
|---|---:|---:|
| SkillOpt 训练执行上限 | 20 家族 × 11 = 220 | 440 |
| SkillLift 训练执行上限，含内部复测 | 20 家族 × 12 = 240 | 480 |
| 四方法独立测试 | 4 × 80 = 320 | 640 |
| 正式逻辑任务执行上限 | 780 | 1,560 |

上限不含 pilot、基础设施重试，也不等于 API 请求数；提前停止会减少执行量。SkillOpt 保持 3 轮预算，可复核：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 budget
```

两天内是目标，不是保证。按 pilot 实际耗时、资源需求、API 限流和镜像构建情况安排窗口；两个 pilot 家族无法准确预测所有任务耗时。

## 8. 进度、失败与断点恢复

另开 WSL 终端，在仓库根目录查询：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 status --run-dir runs/skilllearnbench-first-v1/glm/generalization
.venv-revision2026/bin/python -m experiments.revision2026 status --run-dir runs/skilllearnbench-first-v1/deepseek/generalization
```

Pilot 阶段把路径末尾换为 `pilot`。`status` 展示落盘状态；异常退出后可能暂留 `running`，不代表进程仍存活，需要结合终端/进程和日志判断。

- 404、429 等暂时性错误：首次尝试加最多 3 次重试，共最多 4 次。耗尽后跳过该任务并继续队列，外层不再叠加整轮重跑。
- 训练失败的家族/方法，其测试显示 `waiting_for_training`；其他已冻结技能的测试继续。
- 修复后重复第 7 节该模型的原命令。已完成训练和测试跳过；失败、未完成及等待任务重新处理。已记录优化请求和有效执行结果通过 journal 重放。
- stdout/stderr 追加保存，历史工作目录保存在 `attempts/`。尚未完成的单次 solver 执行可能需要重新启动，不能恢复已销毁容器中的任意 shell 指令。
- 不删除 run 目录、journal、attempts 或冻结技能；手动修改冻结文件仍会触发内容校验。

退出码为 0 只说明命令处理完队列，**不能单凭退出码认定实验完整**。检查 `execution_summary.json` 的状态、失败/等待清单和下节报告。修改模型、视觉配置、预算或任务集合后仍允许续跑，但报告会标记混合条件，不能当作统一条件的正式结果。

## 9. 结果验收与归档

正式命令结束时生成报告，也可重复生成，无需调用模型：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 report --run-dir runs/skilllearnbench-first-v1/glm/generalization
.venv-revision2026/bin/python -m experiments.revision2026 report --run-dir runs/skilllearnbench-first-v1/deepseek/generalization
```

不要用 `report` 将 pilot 成绩当正式测试；pilot 不包含独立测试样本。

| 文件（位于各模型的 `generalization/` 下） | 用途 |
|---|---|
| `report/overall_statistics.csv` | 四方法测试均分、95% 置信区间、完整性状态 |
| `report/paired_comparisons.csv` | SkillLift 对三个基线的配对差值、差值区间、Holm 校正 p 值 |
| `report/cost_by_method.csv` | 训练/测试、框架/求解器、视觉辅助的已记录 token 与费用 |
| `report/cost_summary.json` | 成本记录的完整性和缺失说明 |
| `execution_summary.json`、`execution_history.jsonl` | 当前及历史批次的完成/失败/等待情况 |
| `run_manifest.json`、`evaluation_manifest.json` | 模型/协议/代码身份及固定评测清单 |
| `training/`、`evaluations/`、`jobs/` | 冻结技能、原始结果、journal、尝试目录及日志 |

以下检查验证覆盖率和统计完整性，不要求 SkillLift 必须获胜或 p 值必须显著：

```bash
.venv-revision2026/bin/python - <<'PY'
import csv
from pathlib import Path
root = Path("runs/skilllearnbench-first-v1")
methods = {"no_skill", "llm_written", "skillopt", "skilllift"}
for model in ("glm", "deepseek"):
    report = root / model / "generalization/report"
    with (report / "overall_statistics.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4 and {r["method"] for r in rows} == methods
    assert all(r["benchmark"] == "skilllearnbench" and r["model"] == model for r in rows)
    assert all(r["status"] == "complete" and int(r["expected"]) == int(r["observed_valid"]) == 80 for r in rows)
    assert all(r["mean_percent"] and r["ci95"] for r in rows)
    with (report / "paired_comparisons.csv").open(newline="") as stream:
        comparisons = list(csv.DictReader(stream))
    assert len(comparisons) == 3 and {r["baseline"] for r in comparisons} == methods - {"skilllift"}
    assert all(r["status"] == "complete" and r["p_holm"] for r in comparisons)
    print(model, "PASS: four complete methods, 320 valid tests, three paired comparisons")
print("PASS: 640 valid held-out evaluations across both models")
PY
```

每行必须 80/80；缺失时只报告覆盖率，不用已完成子集代替完整均分。置信区间按任务家族分组抽样，配对检验保持相同实例配对；一次完整优化不能估计重新优化技能的种子间波动。这些新统计不自动补齐历史 Table 2 的置信区间。

成本按实际记录汇总，包含已记录的失败尝试；缺失费用/usage 不按零算。GPT-6-SOL 初始技能生成的子 agent usage 未暴露，不计入声称的完整总费用。Pilot 成本在单独目录，可检查其 usage 日志，不能混入正式方法成绩。

保留整个 `runs/skilllearnbench-first-v1`，不要只保留 CSV。记录归档时的代码版本及依赖：

```bash
git rev-parse HEAD > runs/skilllearnbench-first-v1/repository_commit.txt
cp .cache/revision2026/resolved-requirements.txt runs/skilllearnbench-first-v1/resolved-requirements.txt
```

运行 manifest 已保存启动时的代码身份，上述归档 HEAD 不能替代启动记录。归档时不复制 `.env`，不把密钥或原始实验日志提交到代码仓。

## 10. 全量泛化完成后再启动消融

等第 9 节两个模型均通过验收，再按 [统一运行指南](RUN_GUIDE.md)及 [README 消融说明](README.md#4-direct-oracle-消融skillsbench)单独启动 `ablation`。以下是后续步骤，第 1–9 节无需执行；两条命令可分别放在两个终端：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 ablation --models glm --workers 4 --run-dir runs/skilllearnbench-first-v1/glm/ablation
```

```bash
.venv-revision2026/bin/python -m experiments.revision2026 ablation --models deepseek --workers 8 --run-dir runs/skilllearnbench-first-v1/deepseek/ablation
```

这些命令不会重新触发已经完成的 SkillLearnBench 实验。

## 验证边界

本手册依据当前 CLI、调度器和协议编写。交付前检查命令参数、Bash/Python 代码块语法和计划计数；真实接口、镜像、外部服务及完整执行耗时仍以 WSL 上 pilot 和正式日志为准。本机未运行 Docker 或付费模型实验。
