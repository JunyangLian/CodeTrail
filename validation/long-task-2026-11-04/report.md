# CodeTrail 长任务治理升级与验证

日期：2026-11-04。基线提交：`004fc02222cc6acd291bd556aedef3a6f87ce400`。

本轮实施了实际原生请求预算、带证据的跨会话知识生命周期、风险操作意图持久化与中断核对。**全量软件测试 340/340 通过，离线连续场景 12/12 通过。真实硅基流动检查因第一次请求 HTTP402 余额不足停止，完成模型会话 0 次。** 不用离线检查充当模型成功率，不修改任何历史 Benchmark 结果。

## 修复的实际缺口

1. 原 `ContextManager` 裁剪普通文本，而原生 provider 会用另一份 messages 覆盖它；旧文本预算不能代表实际请求大小。现在在完整原生请求创建 HTTP 数据前统一计量、压缩或拒绝，并记录真实请求 SHA256。
2. 文件摘要原先只是前三条非空行，长期笔记主要按固定格式写 Markdown。新增机械 AST 结构事实及 SQLite 生命周期记录；自然语言候选需显式确认，旧笔记不冒充新系统的已验证事实。
3. checkpoint/原子写入无法判断“动作已发生、结果未保存”的窗口。新增 write-ahead 操作记录，以调用标识、参数和文件快照绑定操作，重启后对不确定效果拒绝自动重放。

## 验证分层与结果

| 类型 | 结果 | 可说明什么 |
|---|---|---|
| 全量自动化 | **340 通过，失败/错误/跳过均 0**；803.37 秒 | 软件回归，包括原有安全约束、真实 Windows AppContainer、真实子进程退出 |
| 本轮新增专项 | **31 项** | 原生真实发送体、协议配对、记忆确认/失效/隔离/冲突、故障恢复 |
| 连续场景 Demo | **12/12**，API 调用 0 | 真实文件、SQLite 和工具执行器的离线机制连通性；模型帧明确为脚本夹具 |
| 合成请求压缩示例 | **110589 → 15451 字节**，移除 15 个旧完整消息组 | 序列化请求预算确实生效；不是 Token、费用或模型效果提升 |
| 真实 provider 只读检查 | **HTTP402/code30001，完成 0/2 会话** | 第一请求已发送，平台余额不足；不能证明真实记忆收益或压缩后模型能力 |

340 包含本轮 31 项与此前 309 项。前端 pytest 入口内部的五个 Node 子测试不再重复计入总数。全量 JUnit 确认真实进程退出测试 `test_actual_process_exit_after_effect_leaves_durable_unresolved_operation` 通过。

开发接入中曾因合法 assistant `content=None` 触发兼容性回归；修正后原生协议相关测试及最终全量回归均通过。没有修改冻结验收资产。Demo 冻结的全部运行时源码 SHA256 与最终实现一致。

## 连续场景的证据

`demo/checks.json`、`demo/context-audit.json`、`demo/prepared-request.json`：

1. 首次读取源码后，从 AST 生成符号、参数、导入及行号，不将其称为业务语义摘要。
2. 新会话可以召回带文件哈希的结构事实。
3. 自然语言候选不进入已验证知识召回；显式操作者确认后激活。
4. TTL 到期被标为 expired；源码修改使旧结构事实 stale。
5. 重新读取源码后生成新版本结构事实。
6. 合成请求经过实际原生 payload 预算处理，保留用户约束、最新调用配对和运行状态。
7. 写入效果发生后模拟中断，旧意图保持 executing；新实例拒绝重复风险操作。
8. 操作者核对当前文件哈希并结束旧意图后，新的操作再次经过审批才执行。

真实进程退出另外由集成测试完成：子进程写入文件后 `os._exit(77)`，父进程创建全新 Agent，发现持久化未完成意图并拒绝再次写入。这是本机故障注入，不能推导为分布式系统 exactly-once。

## 真实接口检查的保留失败

`provider-smoke/protocol.json` 在请求前冻结两次只读会话的输入及配置，复用原冻结分页任务的源码夹具，不进行代码修复。模型 ID 固定为 `deepseek-ai/DeepSeek-V4.1-Flash`，硅基流动地址、temperature=0.2、top_p=0.95、Thinking=4096、输出=4096、超时=180 秒、4 个工具步、传输尝试最多 1 次。工具仅 `read_file/search/list_files`，审批 never，单独新工作区与会话。

第一次模型请求返回 HTTP402 `Sorry, your account balance is insufficient` 后立即停止，没有第二次会话、没有更换 Key 或模型、没有替换失败结果。轨迹、原生请求和 `failure.json` 保留；Authorization 和 Key 不归档，provider reasoning 只保留长度与哈希。未获得生成用量，相关成本未知，不能算成零。

## 边界

- 请求计量是明确标识的序列化 JSON UTF-8 字节保守估计，不是该模型 tokenizer 的精确 Token 数；真实 usage 另报。
- 压缩为机械证据摘要，不是经过任务评测验证的 LLM 语义摘要。旧输出的有限预览不保证保留全部 traceback；必要时要重新读取或重新获得批准运行测试。
- 当前用户指令和必要协议内容装不下时拒绝请求，不静默截断。跨会话历史用户指令仍可能累积过长，尚未实现自动语义合并。
- 新知识支持仓库和会话作用域，不支持多租户/全局用户记忆。语义冲突仅以明确 fact key 做版本替换，不能自动判定所有自然语言矛盾。
- 未确认的候选、失效及撤销记录不召回。旧 Markdown 笔记继续兼容并标为 unverified legacy，不等同于已验证知识。
- 未完成风险操作需要显式核对；没有自动重建旧网页审批卡片或自动续跑旧模型循环，不自动复用审批，也不保证任意 Shell 外部副作用 exactly-once。
- 沿用应用层权限与可选 AppContainer；未放宽 Shell、路径、测试文件保护或审批规则。
- 本轮没有新的真实修复成功率、Recall 改善或成本下降结论；历史 30 题/全部 144 次数据保持不变。

## 复现与证据

实现、配置及操作者命令见 [长任务治理说明](../../docs/long-task-upgrade.md)。[证据包](evidence.zip)与 [SHA256](evidence.sha256)包含原始 Demo、失败的真实接口检查、JUnit、运行时源码和清单；[验证汇总](summary.json)便于机器读取。

```powershell
python -X utf8 scripts/demo_long_task.py --output artifacts/my-new-long-task-demo
python -m pytest tests/test_long_task_context.py tests/test_knowledge_lifecycle.py tests/test_operation_recovery.py -q
python -m pytest -q --junitxml=artifacts/my-new-full-tests.xml
```

真实只读检查会调用用户 API。设置进程环境 `SILICONFLOW_API_KEY` 后，必须使用新目录，不覆盖本轮余额失败：

```powershell
python -X utf8 scripts/check_long_task_provider.py --output artifacts/my-new-provider-smoke
```
