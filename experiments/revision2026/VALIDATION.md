# 交付验证记录

验证日期：2026-10-26。目标运行环境为 WSL + Windows Docker Desktop；以下是交付前在 macOS 完成的离线验证，不是模型实验成绩。

## 已通过

- 新增实验测试：25 项。覆盖冻结技能、train/test 隔离、固定任务清单、Direct Oracle 反馈、评分异常、完整性检查、配对统计、CLI 和计划生成。
- 新测试与原生 SkillsBench/WildClaw 适配器相关回归合计：56 项通过。回归只读使用本机已有、版本匹配的任务源码；临时引用已恢复，没有下载任务或镜像。
- `setup.sh` 与 `run_all.sh` 的 Bash 语法检查通过。
- 旧归档只读恢复检查：221 个 SkillsBench portfolio 可通过历史 winner hash 校验；40 个预期位置尚不可恢复，其中 34 个缺 portfolio、6 个缺历史 winner hash。缺口保留在计划中。
- GPT-6-SOL 生成的 20 份技能及冻结清单通过哈希校验；不包含原始大文件、私密接口配置或实验日志。

## 仍需目标机器验证

尚未执行依赖安装、Docker 任务、真实 SkillOpt 优化或模型端到端推理。目标机器须依次完成 setup、素材准备、preflight 和训练侧 pilot，随后运行三个正式流程。离线测试不能证明接口兼容、外部任务服务可用、最终分数或两天内完成。

恢复旧资产的独立复测与完整重建均产生新的实验结果。论文中的均值、置信区间和显著性应来自同一批有效观测；不能将新方差附到历史均值，不能把未完成行或基础设施失败伪装成有效结果。

## 新增 WildClaw 验证日志后的复查

同日新增只读审计 `skilllift_glm51_verification_logs/`：确认 60 题覆盖、40 题有 final 证据；32 份评分有明确异常标志，其中 final 7 份、涉及 4 题。审计和逐题补跑范围见 `legacy_evidence/wildclaw_glm51_verification.md`。新增对 WildClaw judge 报错但仍返回数值成绩的拒绝检查；参数化回归测试覆盖三种实际错误字段，并确认正常任务惩罚项不被误判。

此时新增实验测试为 29 项，合并相关原生适配器回归共 **60 项通过**。本次仍未执行模型、Docker 或新的付费实验。

## Direct Oracle 方案 A 与范围调整（2026-10-26）

- `run_direct_oracle` 改为与 SkillLift 相同的 oracle 结构：每轮 K 个候选、每个 1 次修改、1 次 oracle；先生成全部候选再执行；同样的接受、基础设施重试和停止规则；分数相同时用 oracle 逐项通过比例做 tie-break；记录逐轮曲线。
- protocol：`evolution_runs` 1；SkillLearnBench 方法为 no_skill / llm_written / skillopt / skilllift / direct_oracle；原生 SkillsBench/WildClaw `ablation` 禁用（`run_all.sh` 不再调用）。
- 新增/更新测试：结构与调用顺序、10 次上限、tie-break、同候选重试、anchor 直接终止、反馈截断、`ablation` 命令禁用、计划规模。本机 Python 3.12 下 `tests/revision2026` 共 34 项通过。仍未运行 Docker、模型或真实任务。
