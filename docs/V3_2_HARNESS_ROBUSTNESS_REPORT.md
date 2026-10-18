# V3.2 Harness 稳健性报告（P3）

> 测试：`tests/integration/test_harness_robustness.py`（10 领域，含正负例）｜ 全过

## 1. 十个注入领域与结果

| # | 领域 | 注入 | 断言（行为级） | 发现 |
|---|---|---|---|---|
| 1 | 长工具结果膨胀 | 80 项 statistics facts | 上下文有界 + 超限披露 + locator 存活 | 通过（修复后） |
| 2 | 证据位于头/中/尾 | 30KB bulk 前置 | locator 仍处渲染头部 1500 字符内 | 通过 |
| 3 | Tool Schema 歧义 | 缺失 schema 查询 + 参数违例 | 结构化 schema 错误归因（tool_schema_or_argument） | 通过 |
| 4 | 工具结果缺失/损坏 | **不存在的 packet dir** | 结构化 failed + "packet not accessible" + 审计轨迹 | **发现真实缺陷并修复**：`node_build_packet` 原 `except ValueError` 漏 OSError/KeyError → 崩溃改为安全失败 |
| 5 | Checkpoint 恢复一致性 | 跨进程 get_state | facts/tool_trace/status 完全一致 | 通过 |
| 6 | 预算跨进程恢复 | 账本播种 guard | 播种后 reserve 仍守上限 | 通过 |
| 7 | 压缩丢 Locator | 20KB 填充 + 800 预算 | 压缩后 locator 仍在渲染中 | 通过 |
| 8 | 证据幻觉 | `genome.fa:1` 注入 | model_capability 归因 + 替代解释（非武断） | 通过 |
| 9 | Scorer/Oracle 错误注入 | grader_error | dataset_or_scorer_error 独立类别 | 通过 |
| 10 | 不完整工具调用 | 空 arguments verify 调用 | schema 拒绝记录 → 重试合法参数 → 全轨迹 | 通过 |

## 2. 行为级验证（非"有答案即可"）

每项测试验证：是否调用了正确工具（白名单断言）、参数是否合法（schema 拒绝路径）、是否引用真实证据（locator 存活）、是否遵守状态与权限（卫兵/拒发路径）、能否安全失败（failed 状态 + last_error + 事件轨迹）、Trace 完整（events/tool_trace 断言）。

## 3. 负例

- 缺失目录 → failed（非崩溃）；空 args → 拒绝（非执行）；违例参数 → 结构化错误（非静默）；scorer 损坏 → 独立归因（非 agent 顶罪）。

## 4. 冻结功能保护

全部 161+ 测试（含 145 V3.1 + 16 V3.2 新增）并行通过；S1/S2 收据与快照零改动。
