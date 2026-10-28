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
| 并发 | GLM 最多 4；DeepSeek 最多 4；同一 WSL 用户的实验进程共享限制 |
| GLM 图片能力 | 所有 GLM 方法共用 GLM-5.3-Flash 图片解析工具，计入已记录成本 |

`no_skill` 不提供技能；`llm_written` 直接使用冻结技能，不执行优化。当前没有 Human-written 组。这里使用共同的 OpenAI-compatible Docker 求解器和官方环境、验证器，不能把官方其他模型/执行器的成绩当作本次基线。

优化只能使用实例 1。官方检查项名称、通过/失败状态及断言细节不作为优化反馈。最终测试前冻结技能；测试结果不能回流用于修改或选择技能。无技能 30–60 分是原筛选预期，并非已验证结果；不得根据正式测试分数临时更换测试题或修改方法预算。

## 2. 执行阶段与完成条件

| 阶段 | 操作 | 完成条件 |
|---|---|---|
| A | 拉取分支，准备环境和接口 | setup 成功，预检输出 `configuration_ready` |
| B | 只生成两个模型的正式计划 | 每模型 400 个调度任务：80 个训练/冻结任务、320 个测试任务 |
| C | 两模型各跑训练侧 pilot | 各自 `execution_summary.json` 为 `complete`；确认真实接口和容器可运行 |
| D | 两个终端分别启动全量泛化 | 自动完成训练、冻结、测试；GLM 4 / DeepSeek 4 并发 |
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
bash experiments/revision2026/setup.sh skilllearnbench
```

需要 Git、Python 3.12+ 及 venv 支持、Docker CLI 和 Compose。若缺少系统依赖，按 [README 的 WSL 准备说明](README.md#1-在-wsl-准备环境)处理。`setup.sh skilllearnbench` 只准备 SkillLearnBench 和 SkillOpt 固定源码、安装所需依赖并运行离线测试，不读取 SkillsBench/WildClaw 的额外依赖。无参数的 `setup.sh` 仍准备整个实验包。脚本不安装 Docker。**本阶段无需执行 `prepare-wildclaw`。** SkillLearnBench 执行时优先复用已有任务镜像，缺失镜像才构建。

若旧命令报 `Untracked task files may alter the evaluation: .../skillsbench`，应使用上述限定范围的安装命令；无需删除其他基准的预热文件，也不应关闭源码校验。只准备源码时可用 `python -m experiments.revision2026 prepare --benchmarks skilllearnbench`。

WSL/Linux 已安装 uv 时，脚本优先使用 uv 并发安装；没有 uv 则使用 pip。若 PyPI 连接慢或 SSL 超时，可仅为本次安装指定镜像（不修改全局配置）：

```bash
REVISION_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  bash experiments/revision2026/setup.sh skilllearnbench
```

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
.venv-revision2026/bin/python -m experiments.revision2026 preflight --models glm deepseek --benchmarks skilllearnbench
```

`--benchmarks skilllearnbench` 将预检限定为本次泛化所需的源码、SkillOpt、Docker、冻结技能及配置字段，不依赖 SkillsBench/WildClaw 的准备状态。不传该参数时仍检查整个补充实验包。预检不发送真实模型推理请求，也不证明外部任务服务可访问；pilot 才验证真实执行链路。

### Windows PowerShell 执行

当前代码支持 Windows + Docker Desktop（Linux containers）。在仓库根目录用 Windows 虚拟环境运行，后续各节的 `.venv-revision2026/bin/python` 对应 `.\.venv-revision2026\Scripts\python.exe`，不要使用系统默认的 Python 3.10。

若已将官方 SkillOpt 下载在根目录 `SkillOpt/`，且其中包含协议锁定的 commit，可从本地复制，无需重新下载或切换原目录的分支。以下命令仅在 `.cache/revision2026/skillopt` 尚不存在时执行：

```powershell
git -C SkillOpt cat-file -t 84b36cd5564e8893569e50e37f02a81d10b55e27
git clone --no-hardlinks --no-checkout -c core.autocrlf=false -c core.eol=lf ./SkillOpt .cache/revision2026/skillopt
git -C .cache/revision2026/skillopt checkout --detach 84b36cd5564e8893569e50e37f02a81d10b55e27
```

已有可用的虚拟环境时安装依赖和本地包（不重建环境）。精简版 Windows Python 若忽略构建隔离所需的 `PYTHONPATH`，先安装构建工具，再使用 `--no-build-isolation`：

```powershell
$py = ".\.venv-revision2026\Scripts\python.exe"
& $py -m pip install "setuptools>=77" wheel
& $py -m pip install --no-build-isolation --config-settings editable_mode=compat -r experiments/revision2026/requirements.txt -e ./skilllift -e ./packages/bounded-edits -e .cache/revision2026/skillopt
& $py -X utf8 -m pytest -q tests/revision2026
& $py -m experiments.revision2026 preflight --models glm deepseek --benchmarks skilllearnbench
```

`editable_mode=compat` 避免根目录的 `skilllift/` 被 Python 当作同名 namespace package，从而遮蔽其中实际的算法包。预检会检查核心运行时导入以及 SkillOpt 的实际导入路径，确保使用缓存中的锁定版本。

`test_skillopt_adapter.py` 使用真实官方 Trainer 和反思/选择逻辑，只替换模型 HTTP 响应及 Docker rollout；这是离线集成测试，不是模型成绩。适配器将 OpenAI 工具调用转换为官方反思格式的命令/观察对，保留辅助文件，仅从训练实例选择技能，并由外层调度器冻结后执行独立测试。

## 5. 生成并核对正式计划：不调用模型

运行前确定模型、端点、视觉配置和协议，此后尽量固定。本手册的新目录适合尚未开跑的实验；不要复制不同条件下的旧产物来冒充本次完整结果。

```bash
PY=.venv-revision2026/bin/python
RUN_ROOT=runs/skilllearnbench-first-v1
$PY -m experiments.revision2026 generalization --models glm --workers 4 --run-dir "$RUN_ROOT/glm/generalization" --plan-only
$PY -m experiments.revision2026 generalization --models deepseek --workers 4 --run-dir "$RUN_ROOT/deepseek/generalization" --plan-only
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
.venv-revision2026/bin/python -m experiments.revision2026 pilot --models deepseek --workers 4 --run-dir runs/skilllearnbench-first-v1/deepseek/pilot
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
.venv-revision2026/bin/python -m experiments.revision2026 generalization --models deepseek --workers 4 --run-dir runs/skilllearnbench-first-v1/deepseek/generalization
```

两条命令各自自动完成训练、冻结、全部测试和统计/成本报告。无需为四种方法分别启动额外批次，也不要通过反复修改方法清单来分别开跑。

GLM 和 DeepSeek 合计最多同时执行 8 个任务，两者配额独立。机器资源不足时，可将 `--workers` 分别降低，例如 2 和 4；不要高于 4 和 4。共享槽位锁位于同一 WSL 用户的 `~/.cache/skilllift/model-slots/`，不跨电脑或不同 WSL 用户协调。

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

例如 100 个调度任务中已有 33 个完成：重复原命令会输出 `already_complete_skipped: 33`、`to_run: 67`，仅启动剩余任务。第 34 个任务的旧 `running` 状态不阻止重启；它会保留旧尝试并按现有 journal 机制恢复。完成判断依赖有效的 `frozen.json`（训练）和 `selected.json`（评测），不是仅看终端输出或旧状态文件。状态命令按逻辑任务去重，不会因超时参数调整后保留了旧 spec 文件而把任务计数翻倍。

SkillLearnBench 求解器、SkillLift 框架和 SkillOpt 优化器使用非流式 API 请求。单次 API 等待由 `solver.api_timeout_seconds` 控制，默认 900 秒；这是客户端超时，不是服务商声明的时限。它与单次求解的 `solver.timeout_seconds`（1800 秒）、单条容器命令的 `command_timeout_seconds`（120 秒）、重试退避上限是不同设置。`usage.jsonl` 中的 `solver_request_started/completed/failed` 记录每次请求的等待时长、超时设置和异常链；工具动作只在完整响应成功返回后执行。 `solver_usage.finish_reason` 记录服务端结束原因；`length` 标为 `output_token_limit`，不会执行截断的工具命令。

每次任务镜像命中或构建还会在执行目录的 `image-locks/*.events.jsonl` 中记录镜像名、不可变镜像 ID 和 `reused`。`reused: true` 表示直接使用已有任务镜像；求解完成后的临时状态快照用于官方验证器，结束后清理，不删除缓存的任务镜像。

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
.venv-revision2026/bin/python -m experiments.revision2026 ablation --models deepseek --workers 4 --run-dir runs/skilllearnbench-first-v1/deepseek/ablation
```

这些命令不会重新触发已经完成的 SkillLearnBench 实验。

## 验证边界

本手册依据当前 CLI、调度器和协议编写。交付前检查命令参数、Bash/Python 代码块语法和计划计数；真实接口、镜像、外部服务及完整执行耗时仍以实际运行环境中的 pilot 和正式日志为准。本机已启动 Windows/Docker Desktop 真实接口 smoke；正式泛化结论仍以独立测试集结果为准。

## 请求关闭思考的四方法链路 smoke

此诊断只运行 `python-scala-translation-1`，不用于泛化成绩。自定义协议使用 `human_written`（human-skill）、`no_skill`、`skillopt`、`skilllift`；在 `solver` 中增加：

```json
"extra_body_by_model": {"glm": {"thinking": {"type": "disabled"}}}
```

该字段同时作用于 SkillLearnBench solver、SkillLift 框架角色和 SkillOpt 优化 API。省略时不发送额外参数，不改变正式协议的思考策略。SkillLift journal key 包含该配置，避免把不同思考策略的响应当作相同请求复用。改变实验条件应使用新的运行目录；同条件中断恢复使用原目录。

```powershell
.\.venv-revision2026\Scripts\python.exe -X utf8 -m experiments.revision2026 pilot --models glm --workers 4 --protocol runs/skilllearnbench-python-scala-nothink-v1/smoke_protocol.json --run-dir runs/skilllearnbench-python-scala-nothink-v1/glm/pilot
```

2026-10-26 排查中，原 300 秒客户端超时改为 900 秒后，非流式请求在约 389–431 秒返回。长请求的 16,384 输出 token 几乎全是 reasoning tokens，未写出 Tokenizer.scala；不能把这种空响应标为正常完成。请求模型 `GLM-5.1` 的响应 model 为 `glm-5.3`，报告必须保留两者，不能仅凭请求名认定实际模型版本。简单探测返回 reasoning tokens=0，但实际任务调用仍返回非零 reasoning tokens，因此本轮只能标记为“请求关闭思考”，不能声称服务端完全关闭。当前使用的是 `/api/coding/paas/v4` 端点。[官方文档](https://docs.bigmodel.cn/cn/guide/capabilities/thinking) 说明 GLM-5.3 不再支持关闭思考；Coding Plan 的具体路由行为不能仅从请求模型名称推断。

2026-10-26 本机实测已完成：四种方法在 `python-scala-translation-1` 的外层官方评测均为 reward=1、编译成功、测试 10/10。SkillOpt 完成 5 次训练侧逻辑 rollout，SkillLift 完成 4 次；这不构成独立测试集泛化结论。结果汇总为 `runs/skilllearnbench-python-scala-nothink-v1/smoke_summary.json`。

真实中断后原命令跳过 4 个完成项，复用 SkillOpt 的 2 条和 SkillLift 的 1 条已完成 rollout；全部完成后再次执行原命令，输出 `already_complete_skipped: 8, to_run: 0`。8 份完成结果及全部 usage 日志的内容、修改时间均未改变。33/100 的训练和评测批次恢复回归测试也已通过。15 条镜像使用记录全部为缓存命中，image ID 保持 `sha256:af651b6edbcd900a8a069ab21598285eb292fe1889b7fb0536ed31e9290becc4`，未重建任务镜像。

## 开启思考、32,768 输出预算的正式泛化启动脚本

使用独立配置 `experiments/revision2026/protocol.skilllearnbench-thinking-32768.json`。GLM 和 DeepSeek 的 `thinking.type` 均显式设为 `enabled`，通过共享适配传递给任务求解器、SkillLift 框架和 SkillOpt 优化器；继续使用非流式。任务求解器的单次 `max_output_tokens` 从 16,384 加倍为 32,768（包含思考及答案），API 等待上限为 1,800 秒。此值不是输入加输出的总上下文窗口，也不修改优化器内部调用各自的输出预算或 `max_context_chars`。DeepSeek 的参数格式依据[官方 Thinking Mode 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

脚本仅运行 SkillLearnBench，采用正式协议的 `no_skill / llm_written / skillopt / skilllift`；上面的四方法 smoke 使用的 `human_written` 不替换正式协议中的 `llm_written`。每个模型 400 个计划项（80 个训练/冻结 + 320 个独立测试实例评测），模型 ID 和密钥沿用 `.env` / 已导出的环境变量。

Windows PowerShell：在两个终端中分别执行，可以同时运行。脚本从任意工作目录调用均可，默认路径相对于仓库根目录。

Windows 入口优先使用 `.venv-revision2026-windows/Scripts/python.exe`，无需手动激活；WSL 使用 `.venv-revision2026/bin/python`。两个系统不能共用虚拟环境的 `pyvenv.cfg`。Windows Git Bash 的 setup 现在也创建独立 Windows 环境。未显式设置 Docker 环境变量时，PowerShell 入口在当前进程内选择 Docker Desktop 的 `desktop-linux` context；启动前要求全部 100 个任务镜像可见，避免连到另一套 Docker 后重建镜像。无需进入 WSL 终端；Docker Desktop 的 Linux 容器后端仍需运行。

切换 Windows/WSL 时先结束另一端的实验进程，再用相同结果目录启动；两端的模型并发槽位不共享，不能把同时启动当成恢复。失败任务会重试，已完成且配置一致的项目会跳过。

```powershell
# DeepSeek：4 并发
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\code\SkillLift\experiments\revision2026\run_skilllearnbench.ps1 -Model deepseek

# GLM：4 并发
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\code\SkillLift\experiments\revision2026\run_skilllearnbench.ps1 -Model glm
```

默认分别写入 `runs/skilllearnbench-thinking-32768-v1/deepseek/generalization` 和 `runs/skilllearnbench-thinking-32768-v1/glm/generalization`。中断后执行同一命令，保留同一 `-RunRoot`。不使用之前的关闭思考 smoke 目录。两个脚本进程共享已有机器级模型槽位，分别上限 4 和 4；存在其他同模型实验时会共同占用这些槽位。

可选参数：`-PlanOnly` 只生成计划、不启动推理；`-Mode pilot` 使用协议的训练侧 pilot 家族；`-Stage train` / `-Stage test` 拆开阶段；`-RunRoot runs/another-name` 选择另一个独立实验目录。脚本在存在失败/等待任务时返回非零状态，即使底层 CLI 正常退出也不把它当成整批成功。

Git Bash / WSL / Linux 对应入口（在仓库根目录运行，在两个终端分别执行）：

本机 WSL 已改为 Docker Desktop 的 Ubuntu-22.04 Integration：Ubuntu 内的 Docker CE、独立 containerd 和配套 apt CLI 已卸载，`docker` 由 Desktop 提供，默认 socket 连接 Desktop。旧 `/var/lib/docker` 和 `/var/lib/containerd` 数据保留；无需重新拉取任务镜像。Resource Saver 已关闭，避免空闲休眠令此前直接使用的私有 socket 消失。正常执行前 `docker info --format '{{.Name}}'` 应返回 `docker-desktop`；无需再手动设置共享 socket 的 `DOCKER_HOST`。

从 `/mnt/d` 启动 WSL Python，默认结果仍保存在 Windows D 盘。若要把结果也放入 Linux 文件系统，可使用 `RUN_ROOT="$HOME/skilllift-runs/skilllearnbench-thinking-32768-v1"`；恢复已有实验前先复制原结果目录，并始终复用同一 `RUN_ROOT`。不要在 Windows/WSL 两端同时写同一实验目录。2026-10-27 的 Windows 单任务在原子替换对话文件时出现 WinError 5：完整临时 JSON 已写出、目标权限正常；持有不允许删除共享的读取句柄可以复现该错误，但历史占用进程未确认。当前写入使用唯一临时文件，并对 Windows 的暂时替换失败进行有界重试。

```bash
bash experiments/revision2026/run_skilllearnbench.sh deepseek
bash experiments/revision2026/run_skilllearnbench.sh glm
# 仅检查计划：PLAN_ONLY=1 bash experiments/revision2026/run_skilllearnbench.sh glm
# 自定义输出根目录：RUN_ROOT=runs/another-name bash experiments/revision2026/run_skilllearnbench.sh deepseek
```

交付验证：PowerShell 和 Git Bash 的两个模型入口均已通过 plan-only，分别生成 400 项；已验证非流式求解请求携带 `thinking.type=enabled` 和 `max_tokens=32768`。本次只准备脚本及计划，没有启动 800 项正式推理。

### API 请求间隔

同一模型提供方的请求开始时间至少间隔 0.5 秒，多个 worker 和实验进程共用此限制；DeepSeek 与 GLM 分别计时。覆盖任务 solver、SkillOpt、SkillLift 和宿主机图像读取请求。空闲超过 0.5 秒时直接发送，网络响应期间不占用间隔锁，仍可保持 4 并发。SkillLift 在本实验中不再叠加原有的 1 秒调用前等待。失败请求继续遵循 Retry-After 和指数退避。修改代码后，新启动的 worker 生效；已运行的 worker 需要重启才能采用此策略。
