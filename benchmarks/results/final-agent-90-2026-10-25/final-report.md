# CodeTrail 最终工程评测报告

**先给结论：Agent确实产生了可由独立测试验证的代码修复；账户余额故障显著影响首批结果。过程具备完整审计证据，但仍有协议纠正开销，且不具备OS级安全隔离。历史实验不足以证明某个优化的单因素收益。**

## 执行计数与主要结果

冻结30道不同题：原20道×3、新10道×3。原90条观察完整保留，36条通过、54条HTTP402余额不足。用户换凭据后仅对全部余额中断槽位追加54条全新会话；不是原会话续跑，也未替换首批失败。实际修复执行144次，仍只有30道题。安全模型攻击另有6次。

| 批次 | 原有题功能/流程 | 新留出功能/流程 | 全部功能/流程 |
|---|---|---|---|
| 首批90次（主结果） | 20/60（33.33%） / 20/60（33.33%） | 16/30（53.33%） / 16/30（53.33%） | 36/90（40.00%） / 36/90（40.00%） |
| 凭据恢复追加54次 | 40/40（100.00%） / 40/40（100.00%） | 13/14（92.86%） / 13/14（92.86%） | 53/54（98.15%） / 53/54（98.15%） |
| 所有144次执行（含首批余额失败） | 60/100（60.00%） / 60/100（60.00%） | 29/44（65.91%） / 29/44（65.91%） | 89/144（61.81%） / 89/144（61.81%） |

原题都已参加历史评测，其中6题额外用于调优。新10题来自more-itertools和python-dotenv此前故障提交：单文件3、跨文件契约3、未知位置2、兼容回归2。旧20题为5/7/4/4，其中8题真实故障最小复现，12题自设计。共18道真实故障、12道自设计；新题保留原包源码，旧真实题多为最小复现，难度与表示方式有差异。这里只保证新10题此前未用于本项目Agent调优，无法保证模型预训练未见。

| 来源（全部144次执行） | 不同题 | 运行 | 功能 | 流程 |
|---|---:|---:|---|---|
| 真实上游故障 | 18 | 84 | 53/84（63.10%） | 53/84（63.10%） |
| 自设计 | 12 | 60 | 36/60（60.00%） | 36/60（60.00%） |
| 已知额外调优6题 | 6 | 29 | 18/29（62.07%） | 18/29（62.07%） |

[首批30题逐次分布](per-task.csv)、[首批→追加逐题分布](per-task-with-recovery.csv)、[全部144次CSV](all-executions.csv)分别保留。第一项始终是原20×3和新10×3；追加不改写其0/3～3/3分布。凭据恢复选择规则及首次追加前协议见证据包recovery-protocol.json。

## 七维指标总表

下表分母包含全部144次修复执行（含54次余额失败）；首批90与追加54的各cohort完整值见[独立审计JSON](credential-recovery-audit.json)。公式、金标和分母在首次正式运行前[冻结](methodology.md)。不将缺少观测的维度补成100分。

| 维度 | 实际结果 | 边界/证据 |
|---|---|---|
| 1 结果正确性 | 功能89/144（61.81%）；流程89/144（61.81%） | 独立原测试、最终文件SHA256、最后改动后重测；不采信Agent自述 |
| 2 检索与上下文 | Recall@5 141/265（53.21%）；片段覆盖756/1125（67.20%）；重复读取0/355（0.00%） | 金标为预定义故障邻域/契约片段代理；不是语义完备性保证 |
| 2 紧邻下一轮丢失代理 | 0/756（0.00%） | 取得的金标行未在下一轮工具消息送达；不等同整个长历史永不丢失 |
| 3 证据忠实性 | 明确测试成功声明一致57/57（100.00%）；疑似虚假成功0 | 仅冻结窄规则；广义语义忠实性、遗漏、自由文本错误归因未评估 |
| 4 工具过程 | 无效调用22/904（2.43%）；最后修改后重测89/144（61.81%） | 参数/未注册/不允许调用；与模型协议拒绝分开 |
| 5 稳定与恢复 | 协议重试114；余额失败54次 | 类型{'Multiple tool calls rejected; send exactly one per step.': 114}；任务三次分布见逐题表 |
| 6 成本效率 | 输入/输出可观测Token {'input': 5415948, 'output': 290717}；完整用量90/144；工具均值6.28 | 未知Token保留未知；真实费用和单位成功货币成本未评估 |
| 6 时长 | p50=30468.0ms，p95=154194.19999999995ms | worker开始至最终整理前；不含独立外部审计；快速余额拒绝拉低时长，不代表性能收益 |
| 7 安全权限 | 脚本守卫13/14；真实模型README攻击6/6；修复最终未授权变更0 | 独立安全分母；固定攻击且用户提示明确警告README不可信；不代表任意攻击泛化 |

## 失败归因、恢复与典型案例

全部归因：{'success': 89, 'api_billing_insufficient_balance': 54, 'incomplete_fix_or_regression': 1}。HTTP402/code30001明确指向余额，不归因为模型决策、请求长度或超时。原始通用failure_category保留，细化见failure-attribution.json。

- 成功：dependency_order保存的模型修复已离线复验，原8项测试失败，补丁后8项通过；无新增模型请求。
- 失败：35-holdout_dotenv_spaces-r3先有正常模型调用，后返回余额不足；完整中间状态和失败保留。
- 真实修复失败：追加77-holdout_dotenv_spaces-r1仅排除Tab、未修复连续空格解析，6项验收仍失败1项；20步中5次调用被拒绝、11次分页读取，最后一步才修改且没有重测。属于实现判断不足与受限工具预算分配失败，不归因API，也不扩大权限或重跑掩盖。
- 恢复：500次真实IO专项捕获一次WinError5，25ms后第二次原子替换成功；另有受控耗尽单元测试。不能据一次恢复宣称所有权限问题解决。
- 安全拒绝：路径穿越、未批准Shell、保护测试写入有独立守卫证据。
- 安全失败：获批代码可直接读取工作区公开假.env canary，说明应用层文件工具策略不提供OS沙箱。未扩大权限或隐藏失败。

案例证据路径集中在[case-index.json](case-index.json)，轨迹/差异/测试在完整证据包内。补丁失败同文件纠正、同工具后续成功代理及状态恢复按真实分母统计；无补丁失败分母时为未评估，不能报100%。

- existing：`{"runs": 100, "unexpected_tool_errors": 37, "later_same_tool_success_proxy": {"numerator": 24, "denominator": 37, "value": 0.6486486486486487}, "patch_rejection_same_file_correction": {"numerator": 1, "denominator": 1, "value": 1.0}, "tool_steps_p50_p95": {"0.5": 5.0, "0.95": 12.049999999999997}, "transport_failed_attempts": 40, "transport_failure_phases": {"connect_or_response_headers": 40}, "persistence_events": {}, "reasoning_usage_known_responses": 586, "reasoning_tokens_over_4096": 0}`。
- holdout：`{"runs": 44, "unexpected_tool_errors": 93, "later_same_tool_success_proxy": {"numerator": 51, "denominator": 93, "value": 0.5483870967741935}, "patch_rejection_same_file_correction": {"numerator": 0, "denominator": 0, "value": null}, "tool_steps_p50_p95": {"0.5": 10.5, "0.95": 20.0}, "transport_failed_attempts": 14, "transport_failure_phases": {"connect_or_response_headers": 14}, "persistence_events": {}, "reasoning_usage_known_responses": 522, "reasoning_tokens_over_4096": 9}`。

## 配置、审计、对照与未评估项

- 硅基流动/v1/chat/completions，实际模型ID deepseek-ai/DeepSeek-V4.1-Flash；2026-10-21平台公告对应1017，但没有不可变权重版本保证。所有实际返回ID核对、原生assistant帧/reasoning字段及tool_call_id配对独立复验。
- 固定temperature0.2、top_p0.95、enable_thinking=true、thinking_budget4096、max_tokens4096、20工具步、60尝试、180秒socket、3次传输尝试、900秒运行截止、并发3。服务实际reasoning_tokens偶有4097；请求预算不证明严格生效。
- 冻结题目、验收、必要证据、仓库提交和SHA256先于正式调用；worker起止核对哈希。144次会话唯一，每次新工作区。54次追加只因用户替换余额耗尽凭据，未根据能力调优重测。
- 首批调度器未把HTTP402列为停止派发条件，导致余额耗尽后仍继续记录失败；这是已暴露的运行治理不足。追加控制器将402/401/403与模型ID不符设为停止条件，源冻结批次不回写。
- 全量自动化测试：292通过，失败/错误0；JUnit与原始运行记录保留。软件测试数不是Agent独立题数。
- WinError有限重试最多5次，仅已知Windows短时权限/共享异常；等待25/50/100/200ms，同目录临时文件fsync+原子替换，恢复/耗尽审计，保留旧目标与失败临时文件，不改ACL。它不提供跨文件事务或系统exactly-once保证。
- 老GLM：43/60功能、42/60流程；官方DeepSeek旧Thinking：4/12；官方原生修复：11/12，保留一次WinError失败。模型、服务商、配置、题目暴露与预算语义不同，只作描述性比较，不声明单因素因果提高。
- 真实任务长期记忆召回收益、广义语义忠实性、专家工具选择准确率、实际货币费用没有可靠标签/账单，均未评估。3个机械记忆专项不是泛化结论。

## 交付与复现

- [全部执行数据](all-executions.csv)、[首批审计](audit.json)、[追加审计](credential-recovery-audit.json)、[七维定义](methodology.md)、[逐题分布](per-task-with-recovery.json)。
- [完整证据包](evidence.zip)与[evidence.sha256](evidence.sha256)：完整请求响应、工具轨迹、代码差异、外部验收、哈希、JUnit和源码快照。Authorization与真实Key不落盘；公开假canary允许出现在安全证据中，私有参考补丁不发布。
- [公开复现命令](../../../docs/final-agent-reproduction.md)、[架构图](../../../docs/final-agent-architecture.md)、[面试讲解与简历](../../../docs/final-agent-interview.md)。设置本地环境SILICONFLOW_API_KEY后，可运行scripts/run_final_demo.py；现成真实修复离线回放无需API。
- 留出题已完成评测；后续若据其改进，应降级为开发回归集。动态服务状态不因源码冻结而固定。本轮无单因素消融、无复杂仓库泛化或统计显著性结论。

补充案例：[一次真实补丁拒绝后同文件纠正](corrected-patch-case.json)（n=1，不作普遍恢复结论）；[6个安全模型会话的原生帧与ID独立审计](safety-native-wire-audit.json)。
