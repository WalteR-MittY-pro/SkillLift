# 统一启动与断点续跑

**当前优先先跑 SkillLearnBench：请使用 [SkillLearnBench 专用执行手册](SKILLLEARNBENCH_RUNBOOK.md)。本页的 `supplementary` 会同时启动泛化和消融，适用于需要同时运行两项实验的情况。**

以下命令在 WSL 内、仓库根目录执行。Windows Docker Desktop 已运行，并已开启对应 WSL 发行版的集成。此页是当前重试、并发和续跑规则。

## 第一次准备

```bash
git pull --ff-only
REVISION_PYTHON=python3.12 bash experiments/revision2026/setup.sh
# 在仓库根目录 .env 配置 GLM_*、DEEPSEEK_*；GLM_VISION_* 可继承 GLM 的地址和 key。
.venv-revision2026/bin/python -m experiments.revision2026 preflight
```

这次两个实验只需要 SkillLearnBench 和 SkillsBench，不必为它们执行 `prepare-wildclaw` 或启动 Table 2 重建。

## 一条命令运行两个模型、两个实验

```bash
bash experiments/revision2026/run_all.sh supplementary
```

执行次序自动安排：

1. GLM 和 DeepSeek 分别启动训练侧 pilot，两个模型可以同时运行。
2. 每个模型完成自己的 pilot 队列后，启动正式泛化与 Direct Oracle 消融。两个实验的优化任务交错排队，共用该模型的并发额度。
3. 该模型两项实验的训练队列处理完后，执行已经冻结技能的最终测试。训练失败的任务所对应的测试记为 `waiting_for_training`；其他任务继续。
4. 自动生成成绩、过程最好分、覆盖率、失败/等待清单和成本报告。GLM 不必等 DeepSeek，DeepSeek 也不必等 GLM。

| 模型 | 两实验合计最大同时执行任务数 |
|---|---:|
| GLM-5.1（含该任务内部的视觉辅助调用） | 4 |
| DeepSeek-V4.1-Flash | 8 |

不是每个实验分别开 4/8。进程锁存放于 WSL 用户的 `~/.cache/skilllift/model-slots/`，同一用户在同一 WSL 环境另开实验命令时，也共享这些额度；锁在进程退出后由操作系统释放。另有逻辑任务锁，防止两条命令同时修改同一个任务目录。

内存不足时可以降低并发，例如：

```bash
GLM_WORKERS=2 DEEPSEEK_WORKERS=4 bash experiments/revision2026/run_all.sh supplementary
```

需要单独补一个模型时：

```bash
bash experiments/revision2026/run_all.sh supplementary glm
bash experiments/revision2026/run_all.sh supplementary deepseek
```

## Direct Oracle 反馈边界

Direct Oracle 的生成器只接收总分和求解执行反馈，不接收官方检查项名称、通过/失败状态、期望值或断言细节；同分保留当前技能。旧 journal 的反馈重放也会过滤这些字段，原始评分日志仍保留。如果已用旧反馈完成优化，过滤不能撤销已写入技能的影响：宽松续跑仍跳过已完成任务；正式比较应使用新 `RUN_ROOT` 重跑受影响的实验，旧数据保留作审计。

## 重试规则

“3 次重试”明确指 **首次尝试 + 最多 3 次重试，共最多 4 次尝试**。404、429 和其他暂时性网络/服务错误采用指数退避；401 等永久配置错误不会盲目重试。

- 泛化 solver、框架 LLM、SkillOpt、OpenHands 和图片解析统一包含 404 重试。
- API 请求已经耗尽 3 次重试后，标记 `[retry-exhausted]`。外层不会重新启动整个任务，再对相同失败请求乘一次重试数。
- 非 API 的可重试执行故障，由 oracle 执行层首次尝试后再试 3 次；coordinator 和 job 层不再叠加整轮自动重跑。
- 某任务仍失败，就保存错误和日志，跳过它并继续队列。不会把失败计成有效 0 分，也不会因一个失败停止其他实验。
- 失败任务的后续测试保持“等待训练完成”，不会使用不存在的技能或缩减统计分母。

## 中断或失败后恢复

直接执行完全相同的启动命令，并保持同一个 `RUN_ROOT`：

```bash
bash experiments/revision2026/run_all.sh supplementary
```

已完成的训练和测试跳过；只处理失败、未完成和等待中的任务。训练过程中已落盘的 LLM 回复与有效 oracle 结果通过 journal 重放，包括 SkillOpt 的 LLM 请求；同一路径和相同请求恢复时不重新计费调用。中断的工作目录保存在 `attempts/`，原有 stdout/stderr 追加保存。求解器每轮工具执行后也保存会话记录，方便定位失败点。

恢复粒度是已完成任务、已记录的优化请求/执行结果。某个尚未完成的 solver 执行可能需要重新启动；不声称能恢复已销毁容器中的任意一条 shell 命令。发生中断时不要删除 run 目录、journal、attempts 或冻结技能。

按作者要求，改代码/设置后也允许继续运行并保留版本记录。实际混合模型、预算、视觉配置或任务集合时，报告标记混合条件，不冒充同一条件的完整统计。冻结技能的内容校验继续保留。

## 看进度与结果

```bash
PY=.venv-revision2026/bin/python
$PY -m experiments.revision2026 status --run-dir runs/revision2026/glm/queue
$PY -m experiments.revision2026 status --run-dir runs/revision2026/deepseek/queue
```

pilot 期间将上述末尾的 `queue` 换成 `pilot`。

- 整体执行情况：`runs/revision2026/supplementary_summary.json`。
- 失败和等待任务：各模型 `queue/execution_summary.json`；历史批次记录在 `execution_history.jsonl`。
- 单题日志：各模型 `queue/jobs/*.stdout.log`、`*.stderr.log`，以及对应实验目录下的 `training/`、`evaluations/`。
- 实验结果：各模型 `generalization/report/` 和 `ablation/report/`。

命令完成表示队列已处理完；若 summary 为 `completed_with_errors`，应查看失败清单、修复原因，再重复原命令。缺失结果只报告覆盖情况，正式统计保持未完成。
