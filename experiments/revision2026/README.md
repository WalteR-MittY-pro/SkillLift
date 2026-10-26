# 补充实验：WSL + Windows Docker Desktop

本目录交付三个实验的代码、冻结技能、参数和恢复工具。真实模型与 Docker 运行在你的 WSL 机器完成；本机验证是无模型、无 Docker 的代码测试，以及对已有日志的只读恢复检查。

当前优先执行泛化和 SkillsBench 消融，最新配置、图片工具和执行次数见[开跑前审计](AUDIT_2026-10-26.md)。`run_all.sh` 默认仅这两个实验；旧 Table 2 恢复/重建保留为显式入口。

| 实验 | 运行内容 | 主要产物 |
|---|---|---|
| 泛化 | **SkillLearnBench**，GLM-5.1 / DeepSeek V4.1-flash，No skill、GPT-6-SOL 技能、官方 SkillOpt 适配、SkillLift；1 次完整演化 | 独立未见实例成绩、CI、配对检验、token/费用记录 |
| Direct Oracle 消融 | **SkillsBench（87 题）**：GLM-5.1 只跑 Direct Oracle，与论文 Table 2 的 SkillLift 75.1 对照；DeepSeek-V4.1-Flash 跑 SkillLift + Direct Oracle | 过程最好分（Table 2 口径）、DeepSeek 两组的独立复测配对比较、逐轮曲线、实际成本 |
| Table 2 统计 | 恢复旧技能并补独立评测；无法恢复时可完整重跑旧表的 3 模型 × 6 方法 × 2 benchmark | 36 项可用性审计、新均值和 CI、配对差值和 Holm 校正 p |

## 1. 在 WSL 准备环境

在 Windows 启动 Docker Desktop，启用 **Settings → Resources → WSL Integration** 对应发行版。以下命令全部在 WSL Bash 中执行，不是在 PowerShell 或 Windows Python 中执行。不需要在 WSL 再安装一套 Docker daemon。

建议放在 WSL 的 `~/SkillLift`，不要放在 `/mnt/c`。需要 Python 3.12+、Git、Docker CLI、Docker Compose v2；完整 WildClaw 素材准备还需要 ffmpeg。Ubuntu 24.04 示例：

```bash
sudo apt-get update
sudo apt-get install -y git curl python3.12-venv ffmpeg libpango-1.0-0 libpangoft2-1.0-0
git clone --branch codex/supplementary-experiments-linux git@github.com:WalteR-MittY-pro/SkillLift.git ~/SkillLift
cd ~/SkillLift
docker info
docker compose version
REVISION_PYTHON=python3.12 bash experiments/revision2026/setup.sh
```

`setup.sh` 下载锁定的源码和任务数据，建立 **WSL 自己的** `.venv-revision2026`，安装依赖并运行无网络测试。不要复制 macOS/Windows 的虚拟环境。SkillsBench 是任务仓库，脚本安装它声明的依赖，不把它误当作可安装的 Python 包。

WildClaw 还需要官方 OpenClaw 镜像和 workspace 素材；以下才会在目标机器下载镜像、加载镜像和准备视频/模型文件：

```bash
.venv-revision2026/bin/python -m experiments.revision2026 prepare-wildclaw
```

使用固定 Hugging Face dataset revision `75f945578aa00cbdb8f46e4d42e4f4e98f704b4f` 的 `Images/wildclawbench-ubuntu_v1.3.tar`。外部视频和 SAM3 下载沿用官方准备脚本，实际文件 SHA256 另存 `.cache/revision2026/wildclaw_data_manifest.json`。YouTube、模型下载、任务外部服务可能需要网络或账号；失败必须解决并重试，不能跳过任务后仍称全量结果。已有有效缓存会复用。此步骤不会安装 Docker Desktop。

## 2. 配置接口与任务凭据

将 `experiments/revision2026/.env.example` 中需要的条目写入仓库根目录 `.env`，保留你已有的配置。**不要提交 `.env` 或复制任何真实 key 到命令行。**

- 泛化和消融：`GLM_*`、`DEEPSEEK_*`。官方 `deepseek-flash` 是当前 V4.1 Flash 别名；保存实际请求模型标识，不拿它冒充历史 V4-Pro。
- 经确认，GLM 各方法共用 `glm-5.3-flash` 图片观察工具。`GLM_VISION_BASE_URL` / `GLM_VISION_API_KEY` 留空时继承 GLM 配置；也可设置独立视觉端点。只向视觉模型发送图片和固定观察提示，主执行模型仍是 GLM-5.1；工具 usage 纳入成本。论文需注明辅助模型，历史 75.1 仅作参考。
- 完整旧表重建：另外需要 `GPT_SKILLSBENCH_*`（gpt-5.4-mini）、`GPT_WILDCLAW_*`（gpt-5.4）、`DEEPSEEK_PRO_*`（deepseek-v4-pro）。不要把 Flash 填进 Pro 的模型字段。
- `WRITER_*` 仅供 **table2-rebuild** 生成旧 benchmark 缺失的一次性技能，固定 `WRITER_MODEL=gpt-6-sol`。这要求一个支持该模型 ID 的接口；不能用别的模型静默替代。泛化实验不需要它：20 份 GPT-6-SOL skills 已提交并冻结，不会重新生成。
- 官方任务声明的 `GH_TOKEN` 等，以及 WildClaw 搜索所需 `BRAVE_API_KEY`，在对应任务运行前必须具备。容器内还必须能访问任务所需外部服务。普通文本 API 可用不代表所有多模态任务都可执行。

```bash
.venv-revision2026/bin/python -m experiments.revision2026 preflight
# 仅在完整重建 Table 2 时再检查旧模型和生成器配置：
.venv-revision2026/bin/python -m experiments.revision2026 preflight --historical
```

预检检查 WSL/Linux、Python、Docker/Compose、固定源码、BenchFlow 0.6.3 的 SSE 注册、凭据字段和冻结技能。它不等同于模型推理成功；pilot 才检验实际接入。

## 3. 先做训练侧 pilot，再跑泛化

```bash
PY=.venv-revision2026/bin/python
$PY -m experiments.revision2026 pilot --run-dir runs/revision2026/pilot --workers 2
$PY -m experiments.revision2026 generalization --run-dir runs/revision2026/generalization --workers 4
```

Pilot 只使用预先指定的 PDF 表单和 GDP 表格两类**训练实例**，覆盖 protocol 中的全部方法，优化预算缩小为一轮。它用于暴露接入/成本/耗时问题，不用于论文最终统计，也不能靠两个任务证明无技能分数在 30–60。

正式协议锁定 20 个任务家族、100 个官方实例：每类 **实例 1 训练**，其余共 **80 个实例测试**。测试 roster 和 instruction Git blob 固定，不将后来添加的文件自动纳入测试。所有训练阶段完成后才开始测试。优化器永远只能调用实例 1，最终测试反馈不回流。

**方法边界：**

- 2026-10-26 起正式方法为 `no_skill`、`llm_written`、`skillopt`、`skilllift`，只做 **1 次**完整演化（时间限制；不估计演化间波动）。`human_written` 代码仍在，但不在正式方法列表中；若加回，它是作者提供的参考技能，不宣称作者只接触过训练实例。
- `llm_written` 是本会话 fresh-context GPT-6-SOL 子 agent 生成的 20 份技能，两模型共用。输入来源和未读取资源边界见 `seeds/`。这是无执行反馈的 agent 生成流程，不声称单个原始 API 请求，也不伪造其 token 用量。
- SkillOpt 和 SkillLift 从同一份冻结 GPT-6-SOL skill 起步。
- SkillOpt 直接使用固定官方 `ReflACTTrainer + EnvAdapter`。它的中间验收也对训练实例进行全新执行，不把实例 2+ 当 selection。该调整必须在论文中声明。
- SkillOpt 每家族训练侧 5–11 次逻辑任务执行，默认 3 轮不变；初始验收 2 次，加每轮训练 1 次、候选验收最多 2 次。超出审计上限会报错。`python -m experiments.revision2026 budget` 可复算；这不是旧论文的 32 次预算复现。
- SkillLift 使用本仓库真实 Mode A/B coordinator。它内部的 `final` 仍是训练实例复评，**不进入最终成绩**。包括 anchor 已成功的任务，外层仍统一进行独立测试。
- 官方 SkillLearnBench CLI 没有原生 GLM/DeepSeek 通用 API 执行器，本工具使用同一套 OpenAI-compatible Docker tool agent，复用官方环境和 verifier。论文应注明 harness 变化；不能拿官方 Claude harness 的分数直接当这两个模型的无技能分数。
- Solver 容器启动时不提供隐藏 tests；停止全部 solver 进程后，从容器快照启动独立 scorer，再挂载官方 tests。评分进程异常、缺失/非法 reward 都是 ERROR，不记为有效 0 分。

## 4. Direct Oracle 消融（SkillsBench）

```bash
$PY -m experiments.revision2026 ablation --run-dir runs/revision2026/ablation --workers 4
$PY -m experiments.revision2026 report --run-dir runs/revision2026/ablation
```

范围（作者 2026-10-26 决定，见 `protocol.json` 的 `ablation.arms`）：

- 只在 SkillsBench 87 题上运行，使用各题原生初始技能（与论文 Table 2 的演化方法相同）和原生 OpenHands 执行器。
- **GLM-5.1 只跑 `direct_oracle`**，对照论文 Table 2 的 SkillLift 结果 75.1（`ablation.published_reference`），不新跑 SkillLift，也不做独立复测。
- **DeepSeek-V4.1-Flash 两组都新跑**（论文中的 DeepSeek 是 V4-Pro），冻结后每题再独立复测 2 次，报告逐题配对差值、CI 和 p。
- 主指标是 Table 2 的口径：每题演化过程中观察到的最好 oracle 分数（含 anchor），由 `report` 写入 `report/process_best_summary.csv`；SkillLift 的 2 次内部 final 单独列出。这个口径使用了选择技能时的同一批执行，偏乐观，不是独立测试成绩；论文中需说明。已发表行只从论文抄录，不重算。

**结构与 SkillLift 对齐（方案 A）。** SkillLift 每轮只在 K 个候选上花 oracle（内部修改由 rubric 打分）。`direct_oracle` 去掉 rubricator、verifier 和 rank alignment，每轮：

1. 从当前 champion 生成 K 个候选（`max_candidates_per_round`，默认 3），**每个候选只做 1 次 bounded edit**。K 个候选全部生成完才开始执行；后生成的候选能看到同轮已生成的补丁，用于避免重复，但看不到它们的执行结果。
2. 每个有效候选真实执行 1 次 oracle。
3. 选分数最高者；分数更高则接受。分数相同时，若通过的 verifier 测试项比例更高也接受（替代 SkillLift 的 verifier tie-break，信息仍只来自 oracle）。
4. 停止规则与 SkillLift coordinator 相同：anchor_terminal、terminal_candidate、连续 2 轮无提升、轮数用完、全部候选无效。

因此每个 cell 最多 `1 + max_rounds × K` = 10 次 oracle（不含基础设施重试）。基础设施失败与 SkillLift 一样在同一候选上重试，最多 3 次，失败从不记为 0 分。

**生成器收到的反馈**：当前 champion 的分数、求解状态、执行轨迹（超长时从最早部分截断，总量受 `max_feedback_chars` 限制）、最后回复、逐项测试通过/失败；上一轮已执行候选的分数、逐项结果、最后回复片段和补丁片段。不提供测试源码、断言细节、参考答案、模型 thought 或任何测试实例信息。这比 SkillLift 生成器多了逐项测试结果，论文中需注明。

**逐轮曲线**：每个 cell 的 `work/curve.json` 记录每轮 champion 分数、累计 oracle 次数、累计生成器调用和累计 token，用于审稿人 4 要求的性能–成本对比。

## 5. 补齐 Table 2 的统计数据

### 5.1 优先恢复旧日志和技能

把原始 `expirment/` 目录复制到 WSL，保留文件权限和内容。原始日志不进 Git。这里的名字是原归档拼写；`skillsbench/experiments/` 是基础设施目录。

```bash
$PY -m experiments.revision2026 repair-plan \
  --archive ~/data/expirment --output runs/revision2026/legacy-audit
$PY -m experiments.revision2026 repair-run \
  --plan runs/revision2026/legacy-audit/repair_plan.json \
  --run-dir runs/revision2026/table2-repair --workers 4
```

工具读取原始逐题 metadata，区分 selection best、final、重试与完整优化运行；只有实际 portfolio hash 与旧 winner 一致的资产才自动复测。原日志不修改，新结果保存在新的 run 目录。缺失资产仍留在预期分母中，成为 blocked cells，不会通过删除任务生成貌似完整的 CI。

当前提供的归档经本机只读验证，有 **221 个 SkillsBench 冻结技能可核对恢复，40 个预期位置仍未解决**。这不代表 221 个独立完整优化实验；也不代表六方法的配对结果齐全。所有36项的历史可用性清单由审计脚本重新生成。恢复复测能够给出新的条件成绩；不能把新方差附到旧表均值旁边。

### 5.2 无法恢复的行：完整建立新的 36 项结果

新增的 `skilllift_glm51_verification_logs/` 已另行审计，见[WildClaw GLM-5.1 证据报告](legacy_evidence/wildclaw_glm51_verification.md)。它包含 60 题优化尝试、40 题 final 证据，但存在缺失独立 final、重复取最大值及 judge 失败，不能直接给原总分补 CI/p。可用 `python -m experiments.revision2026.verification_audit --archive <日志目录> --output <审计输出目录>` 复算逐题缺口。新运行额外拒绝包含 `judge_error` / `llm_judge_error` / `llm_error` 的数值分数，避免 scorer 失败被记成真实零分。

```bash
$PY -m experiments.revision2026 table2-rebuild \
  --run-dir runs/revision2026/table2-fresh --workers 4
```

覆盖两个旧 benchmark 的原模型组合与全部六方法：No skill、Human-written、LLM-written、SkillOpt、CoEvoSkills、SkillLift。单独使用原模型配置，绝不将 DeepSeek Flash 替代旧 Pro。共同 GPT-6-SOL 一次性技能按任务生成并缓存；SkillOpt/SkillLift 从该技能起步。CoEvoSkills 复用仓库现有原生初始化与 surrogate 机制，并在配置中注明其初始化差异，不声称它与其他方法拥有同一初始技能池。

**这是新的、明确记录协议的完整重跑，不是已经证明复现了历史 Table 2。** 新均值、CI、p 必须一起更新，不能只取新 CI 配旧均值。CoEvo 的内部最好分和内部复测不作为主表结果，主表统一使用外层冻结后评测。若必须逐项复现旧发表协议，仍需找回原版本、原初始技能及运行清单。

### 5.3 统计方法与边界

- 每个最终逻辑试次只采纳一份有效结果，基础设施 ERROR 不作为 0。重试不增加样本数。
- 必须满足固定预期任务/运行/重复清单。未覆盖的组只报告 coverage，不给全量 CI/p。
- 报告新均值、95% CI、`SkillLift − baseline` 百分点差及差值 CI。
- 按任务家族和完整 run index 交叉重采样，方法保持配对；新 benchmark 同源实例不能当独立家族。配对置换以家族为单位，适用前提是零假设下方法标签可交换。
- 所有预先计划的 overall 比较做 Holm 校正；SkillLift 自身是参照行，不填自身 p。
- 一个旧冻结技能的三次复测，只测执行波动，不变成三次完整演化。当前节省时间的协议只有一个完整优化 run，不估计完整优化的 run 间方差。
- 置信区间范围、run 数、家族数和缺口保留在结果中，不预设正态分布、显著性或目标提升。

## 6. 一次启动、续跑与结果

在两个 WSL 终端分别运行，自动采用 GLM 并发 4、DeepSeek 并发 8，输出目录按模型分开：

```bash
# 终端 A
bash experiments/revision2026/run_all.sh supplementary glm
# 终端 B
bash experiments/revision2026/run_all.sh supplementary deepseek
```

每条依次跑 pilot → generalization → ablation，不启动旧表重建。旧表需显式运行 `run_all.sh rebuild` 或设置 `LEGACY_ARCHIVE` 后运行 `run_all.sh repair`。

**API 中断自动重试。** 429、408、409、425、5xx、连接重置和超时视为暂时性错误：

| 层级 | 位置 | 最多尝试 | 退避 |
|---|---|---|---|
| 单次 API 调用 | 泛化 solver、框架及 SkillOpt LLM | 最多 8 次，不再叠加 SDK/内层重试 | 5 秒起指数退避，常规等待上限 300 秒；Retry-After 单独遵守 |
| 单次 oracle 执行 | 训练中的每次执行、最终独立复测 | 最多 3 次物理执行，coordinator 不再额外叠加 | 30 秒起，上限 900 秒 |
| 整个 job | 默认失败后停留，修复后重发同一命令续跑 | 每次启动 1 次 | 避免整轮自动重启放大费用 |

400、401、403、404、413、422、额度耗尽、1210/1301、内容过滤、上下文超长不会自动盲目重试。失败的执行从不记为 0 分。泛化 solver 因 API 重试等待的时间不计入求解时限。SkillsBench 的 OpenHands SDK 原生请求重试为最多 5 次；视觉工具使用 SDK 最多 8 次请求尝试。参数和边界详见审计报告。

**程序中断后续跑。** 直接重跑同一条命令：

- 已完成的 job（有 `frozen.json` 或状态为 OK 的 `selected.json`）直接跳过，不再执行。
- 中断或失败的 job 自动重新运行（默认行为；加 `--no-retry-errors` 才会改为停下）。
- 训练 cell 的每个已完成 LLM 回复和有效 oracle 执行都实时写入 `<cell>/journal/training.jsonl`（fsync）。重启后同样的请求按原顺序从 journal 返回，优化过程重放到中断点再继续，已执行的 oracle 不会再跑。SkillOpt 的 LLM 调用不在 journal 中，中断的 SkillOpt cell 会重新优化，但相同技能的执行仍会重放。
- 中断时的工作目录整体移到 `<cell>/attempts/<id>/`，所有日志和产物都保留；token/成本统计包含全部尝试。
- 按作者要求允许代码、协议或端点变化后继续执行，并记录身份历史。科学条件实际变化会标记 `mixed_conditions`，不直接生成混合条件的正式均值、CI 或 p；代码修复本身保留哈希历史，由执行者确认不改变实验定义。

`--stage train` / `--stage test` 可拆开执行；`--plan-only` 只保存计划，不调用模型或 Docker，但仍需要已准备的数据和对应配置身份。`--models glm` / `--models deepseek` 可以单模型运行，建议为两个模型分别使用目录。

```bash
$PY -m experiments.revision2026 report --run-dir runs/revision2026/generalization
$PY -m experiments.revision2026 report --run-dir runs/revision2026/ablation
$PY -m experiments.revision2026 report --run-dir runs/revision2026/table2-fresh
$PY -m experiments.revision2026 status --run-dir runs/revision2026/generalization
```

各 run 的 `report/` 包含 `overall_statistics.csv`、`paired_comparisons.csv`、`cost_by_method.csv`、`cost_records.csv` 和 JSON 明细。训练/最终执行成本分开，SkillOpt `_total` 不与分阶段统计重复相加；缺失费用为未知，不能写成免费。GPT-6-SOL 子 agent 初始技能生成用量没有接口可核查，因此单独注明未计入，不编造数值。

默认参数在 `protocol.json`：1 个完整 run；泛化每个测试实例执行一次；消融中 DeepSeek 两组每题独立复测两次，GLM 只用过程最好分对照论文；旧表重建每任务三次。SkillLift 三轮、每轮三候选、Mode A/B 各两次。全量旧表重跑明显比恢复复测昂贵；**总耗时需由目标机器 pilot 核验，约 6 天只是规划估计**。先看 pilot 的时间、内存和 token，再按 Docker Desktop 资源调整 `--workers`，不要为达到时限事后挑题。

## 7. 已做与尚未做的验证

本机测试覆盖：冻结技能完整性、固定训练/测试清单、错误评分拒绝、solver 停止后才评分、Direct Oracle 使用真实连续反馈且不调用 rubric、任务身份防碰撞、固定分母、无效/重复配对拒绝、Holm 校正、官方 SkillOpt 配置不含 key、CLI 导入和旧日志恢复。运行测试不会拉镜像、调用模型或执行正式任务。

没有在本机运行 Docker、SkillOpt 真实优化或模型端到端测试。WSL 的 pilot 是交付后必须完成的运行验证；pip/test 通过不能替代它。任务外部服务或镜像准备失败会明确报错，不伪造 PASS。

上游依据：[SkillLearnBench 固定版本](https://github.com/cxcscmu/SkillLearnBench/tree/726e1f79b96b0cb3d729d0e55a1c847e0b5353ae)、[SkillOpt 固定版本](https://github.com/microsoft/SkillOpt/tree/84b36cd5564e8893569e50e37f02a81d10b55e27)、[DeepSeek API 更新记录](https://api-docs.deepseek.com/updates/)。
