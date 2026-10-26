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

## 消融改回 SkillsBench（2026-10-26，作者决定）

- `ablation.benchmarks = ["skillsbench"]`；GLM-5.1 只跑 `direct_oracle` 并对照论文 Table 2 SkillLift 75.1；DeepSeek-V4.1-Flash 跑 `skilllift` + `direct_oracle`，独立复测 2 次。SkillLearnBench 泛化恢复为 4 个方法。
- 训练时记录每次有效 oracle 执行的分数，冻结时写入 `process_best`；`report` 输出 Table 2 口径的过程最好分，分母为完整计划，缺格只报覆盖率。
- 测试 36 项通过（Python 3.12）。仍未运行 Docker、模型或真实任务。

## 断点重试与续跑（2026-10-26）

- 新增 `resilience.py`：暂时性错误分类、指数退避、journal（按请求内容 + 出现次序索引，fsync 追加，容忍崩溃时的半行）、带 journal 的 LLM 包装。
- 求解器 API 调用、框架 LLM、SkillOpt LLM、训练 oracle 执行、最终复测、整个 job 均有重试；求解器重试等待不计入时限。
- `run_jobs` 默认跳过已完成 job、自动重跑失败/中断 job；训练 cell 在固定 `work/` 目录运行，中断目录归档到 `attempts/`，重启时重放 journal。
- 新增测试 20 项，共 56 项通过（Python 3.12），包括：连续 3 次以上 429/404 后成功、永久错误不重试、journal 半行恢复、LLM 重放不调用 API、oracle 重试与重启重放、Direct Oracle 崩溃后续跑不重复执行 oracle、已完成 job 不重启、失败 job 自动重启、独立复测重试。仍未运行 Docker、模型或真实任务；SkillLift 的完整重放需要在试跑中用"中途 kill 再重启"实测确认。

## 两实验、视觉工具与预算审计复查（2026-10-26）

上节是早先提交的验证记录；最新重试上限和 404 分类已按[审计报告](AUDIT_2026-10-26.md)修正。GLM 统一图片观察桥接已覆盖新泛化 solver 与原生 OpenHands formatter，SkillOpt 预算维持 3 轮（每家族最多 11 次），追加超额拦截。宽松续跑按作者确认保留，实际科学条件混合会标明，执行本身继续。

新增实验测试 72 项；加上相关 SkillsBench/OpenHands/WildClaw 回归共 **122 项通过**。覆盖图片转文字、不向视觉模型发送任务目标、重复图片缓存、失败不伪装成功、视觉成本归档、429 后在同一步恢复、SkillOpt 重试不叠乘、执行预算上限、默认脚本不启动旧表、混合条件不生成正式统计。Bash 语法及 diff whitespace 检查通过。运行验证边界仍为离线；没有调用真实图片 API、安装 Docker 或运行真实 benchmark。

## 统一队列、404 三次重试及断点恢复（2026-10-26）

按作者最新要求，404 可重试，统一为首次尝试加最多 3 次重试；耗尽标记阻止外层重试相乘。OpenHands 与视觉解析也使用共同 HTTP 重试实现。某任务最终失败后保留日志并继续队列，缺训练产物的测试等待下一次恢复。

一条 supplementary 命令同时管理两个模型，每个模型把泛化/消融交错排队，共享 4/8 并发上限；文件锁还限制不同启动进程的总并发及同一逻辑任务的重复执行。SkillOpt LLM 完成记录加入 journal，使用稳定执行编号，重放不重复计费 token。中断时尚未完成的 solver 执行允许重新开始。

新增实验测试 **81 项通过**；加上相关原生适配器回归 **131 项通过**。包含实际启动多个无模型子进程验证共享槽位上限、两个模型并行调度、404 第四次成功/耗尽不叠乘、失败后继续其他任务与就绪测试、恢复只补失败任务且旧日志仍在、SkillOpt 回复重放、冻结中断恢复，以及取消后不再启动新任务。没有运行 Docker 或真实模型实验。最新操作说明见 [RUN_GUIDE.md](RUN_GUIDE.md)。
