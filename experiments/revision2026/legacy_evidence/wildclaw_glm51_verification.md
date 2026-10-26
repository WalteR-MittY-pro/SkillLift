# 新增 WildClawBench GLM-5.1 验证日志审计

2026-10-26，只读分析 `skilllift_glm51_verification_logs/`。原始日志不提交 Git。结论：**有帮助，能恢复 GLM-5.1 / SkillLift 一组的逐题证据并缩小补跑范围；目前不足以直接补齐 Table 2 的正式全量 CI 或方法间显著性。**

## 原始证据盘点

| 检查项 | 实际发现 | 对统计的意义 |
|---|---|---|
| 任务覆盖 | manifest 和目录合计 60 题，ID 与本机固定版 WildClawBench 的 60 题相同 | 覆盖全任务的优化尝试，不等于覆盖全任务的独立最终评测 |
| 模型 / 方法 | 73 份配置均为 GLM-5.1 / SkillLift，算法参数 hash 相同 | 可补这一组；没有 No skill、Human、LLM、SkillOpt、CoEvo 的配对成绩 |
| 任务汇总 | 68 份 cell summary，含重复、重试和失败 | 不能当成 68 个独立任务或多轮全量实验 |
| 原始评分 | 447 份：anchor 81、candidate 278、final 88 | 候选和训练评分不增加最终评测的独立样本数 |
| 有 final 的任务 | 40 个不同任务、42 个 cell 的 84 个逻辑 final 数值 | 距完整 60 题仍有缺口；不能只选这 40 题算全量 CI |
| raw / logical 映射 | 88 个 final score 文件，比汇总的 84 个逻辑数值多 4 个 | 需要 trial ID、final_index 与物理 retry 映射，不能将多余文件直接作为额外样本 |
| final 评分异常 | 7 份 raw final 明确含 judge 错误，涉及 4 题 | 数值分数不代表评分有效；这些试次需修复或复测 |
| 冻结技能和恢复状态 | 包内没有 final_portfolio、state.json 或 trial_result.json | 有 winner hash，缺实际资产，暂不能对冻结技能做 hash 校验后自动续测 |

这里的逻辑 final 数值来自 `cells/*/*/cell_summary.json:final_rewards`；raw 评分来自精确的 `rollouts/*/adapter_artifacts/<task>/final-*/.../score.json`。脚本输出来源索引和输入 SHA256，未执行原归档中的聚合脚本或任何模型调用。

## 为什么现成的两个总分不能直接用于补表

归档的 `scripts/final_aggregate.py` 明确对同一任务的不同运行取最高值；A 来自 cell 的 `mean_score`，B 来自优化过程最高值。核对发现 `mean_score` 对有两次 final 的 cell 实际取两次的最大值，不是算术平均；达到阈值的 anchor 则直接结束，根本没有 final。

因此 **A=60.82%** 混合了 anchor、两次 final 的最大值、部分任务多次运行的最大值，以及失败记零。**B=66.89%** 采用优化过程最高值。两者都不等于统一协议下的单次执行平均成绩。若论文明确报告固定预算 best-of-two，可以对该 estimand 设计统计，但仍须处理 anchor 跳过、重复预算不齐、评分失效等问题。

例：`01_Productivity_Flow_task_8_real_image_category` 的逻辑 final 为 `[0.12, 0.952]`，汇总取 0.952；两次均值是 0.536。两种口径回答的问题不同，不能把前者的总分和后者的方差拼在一起。

附带报告声称“66.9 与 66.5 接近，故复现成功”和“置信区间约 ±3–4pp”，没有提供支持这些判断的统一评测、对照数据或可复算的区间计算。本次不采纳这两个统计结论。环境缺陷具体损失多少分也没有反事实测量，不能直接给分数加回所谓 3–4pp。

## 一条不按成绩挑选的恢复路径

为定位补跑，采用诊断性归属规则：类别 1–3 用 main，4–6 用 c456；只有原 cell 未成功才采用 retry / retry2。**这是事后审计约定，不冒充预先注册的规则。**

- 38 题有两次逻辑 final，21 题 anchor_terminal 没有 final，1 题 anchor_indeterminate 失败。
- 4 题 final 有评分器异常：`02-10 acad_homepage`、`02-11 resume_homepage`、`03-06 chat_cross_dept_update`、`05-03 product_poster`。其中 `02-10`、`02-11` 和 `05-03` 是多模态 API 1210；`03-06` 有内容过滤 1301 导致一次 judge 失败。`trap_errors` 本身是任务评分项，不作为基础设施异常。
- 包括训练阶段在内，32 份评分有明确异常标志（anchor 4、candidate 21、final 7），涉及上述 4 题以及 `05-02 goal_highlights`。这第 5 题也需核查训练反馈是否受污染，不能仅因为 final 没报错就断言优化过程无影响。
- 22 个缺 final 的位置加 4 个评分异常位置，共 **26 题需要最终评测方面的处理**。另有 3 题存在多余 raw final 文件，需要查清物理重试对应关系：real_image_category、vlmeval_ocrbench、clothing_outfit_to_model_image。
- 全目录合并时能找到 40 题 final，而上述归属规则下只有 38 题。这两个数字不矛盾：额外批次里有不同 winner hash 的结果，不能据“任务名相同”拼成同一冻结技能的重复评测。

## 最小补数据建议

1. 从原执行机保留完整任务状态、final_portfolio、trial_result、oracle 记录以及源代码 revision；这些资产应留在私有数据目录。先核对 winner hash，再复用对应 final，禁止跨 winner 拼试次。
2. 修复 grader 的图像接入及内容过滤错误；区分 solver 正常失败和 scorer 无法评分。若错误也影响了训练时的 Oracle 反馈，需重跑对应任务的优化，单补 final 只能评价原来的冻结资产，不能声称已修复训练流程。
3. 给所有 anchor_terminal 补独立 final；修复 google_scholar 的失败原因，并按事先确定的预算重试。根据最小方案处理 26 题及 3 题映射问题；完整保留其他有效结果。最小范围能否直接复用还取决于资产/身份核验和目标协议是否一致。
4. 若统一采用当前新协议的“三次独立最终执行”，最稳妥的是在身份确认后对 60 个冻结技能统一复测，共 180 次 solver 执行；这不等于重跑 180 次优化。若目标是完整优化算法的跨 run 稳定性，则仍需独立完整优化 run，不能用重复评测代替。
5. 对要声称优于的每个 baseline，取得相同模型、执行器、预算、评分器和 60 题的逐题最终成绩，再计算配对差值 CI、配对置换 p 与计划内的多重比较校正。不能仅拿论文里的 baseline 总分做配对检验。

置信区间回答“同一评测口径下平均成绩的不确定性”；显著性检验回答“与具体对照方法的逐题差异是否有证据”。一次训练后完整任务的结果可以支持条件于该批冻结技能的任务重采样 CI；若没有独立训练重复，则不能把它解释为完整优化过程的 run 间稳定性。不能因为缺三个完整 run，就否认所有条件性统计；也不能混淆两种不确定性。

实现方法参照 [SciPy bootstrap 的配对重采样说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)及[配对置换检验说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html)。任务完整性、评分有效性和 estimand 一致性必须先解决，不能靠统计方法自动修复。

## 复算

```bash
.venv-revision2026/bin/python -m experiments.revision2026.verification_audit \
  --archive /path/to/skilllift_glm51_verification_logs \
  --output runs/revision2026/wildclaw-verification-audit
```

输出 `audit.json`、`task_readiness.json`、`raw_score_index.csv`、`final_cell_statistics.csv` 和 `input_sha256.json`。正式全量 CI / p 保留为空，逐题数据和异常清单可供恢复。原始日志不修改，不下载镜像，不调用模型。
