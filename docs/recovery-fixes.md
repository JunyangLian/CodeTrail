# 完成门槛与模型传输恢复

这次改动针对原60次真实评测暴露的“宣布继续修复却结束”“改后未重测”“读取超时直接终止”。原始任务、金标、60条结果和证据包均保持原样。

## 实际改动

1. 普通进度文字不再作为最终回答，仍要求显式协议。指定 `--verify-command` 的可写任务，最后一次修改后必须通过对应命令的实际工具执行，退出码为0才允许完成。修改后旧验证失效，测试失败时反馈要求检查实现契约与返回类型。
2. 验证仍使用普通 `run_shell` 及审批。拒绝审批、工具预算耗尽或模型持续输出无效格式，都会保留未完成状态。失败或停止的运行不写入成功任务的持久记忆。
3. OpenAI兼容传输捕获直接读取超时、断连及不完整响应；对408、429、5xx进行有限重试，401不重试。最多3次尝试，重试前检查剩余预算，429等待上限10秒。每次传输状态、耗时和是否重试均进入trace，失败传输的Token不按0计算。
4. 网页默认socket等待180秒；CLI用 `--openai-timeout` 指定。重试前的总预算为配置等待的2倍，但socket的阻塞行为不等同于严格墙钟截止。冻结回归另有每任务1200秒外部进程截止。

## 验证口径

全量软件测试242项通过，包含17项新增回归：验证失败后修复、验证后再次修改、审批拒绝、预算结束、只读任务、无须改代码的正确实现、超时恢复、限流等待、鉴权不重试、逐次传输追踪等。软件测试数量不计为独立Agent任务。

真实验证使用6道已知问题题目，各2次，全新工作区和会话，独立外部验证器再次重测。模型、温度和工具预算沿用原设置；超时和Harness机制同时变化，是一次组合干预。题目来自已观察失败，既不是独立留出集，也不是重新完成全部20题。结果见[单独回归报告](../benchmarks/results/repair-regression-v2-2026-10-24/report.md)。

运行开始前冻结指纹：`85b3669931dda1baf99beb40445b88f11beac19ea65cfbc2dd8cc7b495731fc3`。

## 诊断边界

额外进行过一次保存请求的非流式参数诊断，90秒超时。之后保存提示词的流式诊断被自动审批拒绝，未执行；安全替代只发送固定文本 `Reply with the single word OK.`，流式接口正常返回。后者不包含项目源码，不执行任何工具，也不计入12次回归。它只能证明短请求连通，不能证明长提示词的流式恢复效果；因此本次没有未经验证地把正式客户端切换为流式，也没有宣称关闭思考参数已生效。

有限重试无法保证外部模型服务可用，且可能增加等待与计费。剩余超时必须保留为失败；不得把这次回归的比例替换原始功能43/60、流程42/60。

## 本地复现

```powershell
python scripts/check_repair_baseline.py
python scripts/run_repair_regression_v2.py --check
python scripts/run_repair_regression_v2.py --output artifacts/recovery-v2-new
python scripts/audit_repair_regression_v2.py --output artifacts/recovery-v2-new --audit-output artifacts/recovery-v2-new-audit
```

需要自己的本地Key，会产生真实接口调用；输出目录必须不存在，禁止覆盖记录。v2证据包需与原v1证据包共同保留：前者包含本轮完整源码及12次运行，后者保存原始运行时和60次基线。原始 `run_repair_suite.py --check` 仅在原始源码快照内运行；当前资产检查器允许明确报告运行时变化，但拒绝金标变化。

```powershell
python scripts/prepare_suite_demo.py --task boltons_isoparse --destination examples/new-recovery-demo
python -m pico.web --cwd examples/new-recovery-demo --verify-command "python -B -m unittest discover -v" --model-timeout 180 --open
```

示例保留故障起点，准备脚本不复制参考补丁。无Key时可用 `scripts/replay_repair_demo.py` 在独立目录回放已保存的模型修复，明确标记为零次新模型运行。
