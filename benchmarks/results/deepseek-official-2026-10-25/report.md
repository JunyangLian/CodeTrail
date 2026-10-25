# DeepSeek官方V4.1 Flash测试

使用官方 `https://api.deepseek.com`，请求模型ID `deepseek-flash`。官方文档将其映射到V4.1-Flash-1017；接口返回的模型字段为别名，不能据此验证部署权重版本。Key只保存本地，证据不含Key。

结论：官方组合的7次传输对照均在约1.0–3.4秒完成；最终思考模式真实修复仍仅4/12。接口等待已明显改善，协议和修复正确性仍需完善。

## 同一提示词的传输延迟

7次重放同一个已公开8356字符提示词，不执行返回工具。默认思考档位high；串行流式3次、三并发流式3次、非流式1次。
| 请求 | 完成 | 首可见秒 | 总秒 | 思考Token |
|---|---|---:|---:|---:|
| 01-serial-before-r1 | True | 0.813 | 1.000 | 8 |
| 02-parallel-r1 | True | 0.969 | 1.187 | 15 |
| 03-parallel-r2 | True | 1.015 | 1.250 | 0 |
| 04-parallel-r3 | True | 1.610 | 2.235 | 48 |
| 05-serial-after-r2 | True | 1.313 | 1.563 | 22 |
| 06-serial-after-r3 | True | 1.032 | 1.250 | 24 |
| 07-nonstream-r1 | True | 3.422 | 3.422 | 435 |

此前GLM/硅基流动对同一提示词，6次流式中1次约78秒完成、5次240秒截止，另1次非流式180秒读取超时。本轮DeepSeek的较短思考输出是实际观测；这同时更换服务、模型、思考默认值和Token预算，不能单独归因于服务商。DeepSeek的8192上限包含思考，而原硅基流动3072不含思考；temperature=0.2虽然仍发送，但DeepSeek思考模式忽略它。

## 真实修复：每轮6题、每题2次

题目、测试、审批、隔离工作区与会话不变，各轮在开始前冻结；失败未筛除、替换或合并。所有36次结果均由独立外部验证器重新验收。
| 配置 | 功能/12 | 完整流程/12 | 请求失败次数 | 无效回复次数 | 平均任务秒 |
|---|---:|---:|---:|---:|---:|
| before_adapter | 1 | 1 | 0 | 201 | 71.259 |
| dsml_only | 2 | 2 | 0 | 1 | 47.068 |
| thinking_chat | 4 | 4 | 0 | 31 | 66.918 |

- `before_adapter`：复用原Anthropic兼容适配，关闭思考，3072输出预算；模型大量输出DSML，原文本协议无法识别。
- `dsml_only`：同一模式下规范化一个完整DSML调用，拒绝多调用、重复参数、截断及非JSON表达式；保留原始可见回复。仍观察到混合标记、实现修复不完整与验证失败。
- `thinking_chat`：官方Chat Completions，默认启用思考/high，8192包含思考的输出预算，并沿用DSML兼容。API格式、模式、预算同时变化，是一次组合配置验证。
- 每次写入仍经过允许文件列表和原有审批；结束前必须通过指定测试。更快的HTTP响应不等于任务修复正确。

## 分题结果

| 题目 | 首轮流程/2 | DSML轮流程/2 | 思考轮流程/2 |
|---|---:|---:|---:|
| boltons_splitlines | 0/2 | 0/2 | 0/2 |
| boltons_xfrange | 0/2 | 0/2 | 0/2 |
| humanize_metric | 0/2 | 0/2 | 0/2 |
| humanize_size | 0/2 | 0/2 | 0/2 |
| inventory_reservation | 1/2 | 2/2 | 2/2 |
| utc_storage_contract | 0/2 | 0/2 | 2/2 |

## 失败保留与证据

- `before_adapter`：冻结 `9dc637d70b1bc039e126c5079216a0ab4574d50b74be30ec8767a7e06af97c31`；完整轨迹、差异、首次外部验证位于 `artifacts/deepseek-validation-2026-10-25`；独立重测在 `artifacts/deepseek-independent-audit-2026-10-25/rechecks/before_adapter`。
  - `01-humanize_size-r1`：protocol_retry_limit，修改文件[]，外部退出码1。
  - `02-humanize_metric-r2`：protocol_retry_limit，修改文件[]，外部退出码1。
  - `03-humanize_metric-r1`：protocol_retry_limit，修改文件[]，外部退出码1。
  - `04-utc_storage_contract-r1`：verification_incomplete，修改文件['store.py', 'timestamps.py']，外部退出码1。
  - `05-boltons_splitlines-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `06-boltons_splitlines-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `07-inventory_reservation-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `08-utc_storage_contract-r2`：verification_incomplete，修改文件['store.py', 'timestamps.py']，外部退出码1。
  - `09-boltons_xfrange-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `10-boltons_xfrange-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `11-humanize_size-r2`：protocol_retry_limit，修改文件['formatter.py']，外部退出码1。
- `dsml_only`：冻结 `c794e589bb09ec32e336d32d152c329a5657d4451f6c0738e93304c951af443a`；完整轨迹、差异、首次外部验证位于 `artifacts/deepseek-validation-dsml-2026-10-25`；独立重测在 `artifacts/deepseek-independent-audit-2026-10-25/rechecks/dsml_only`。
  - `01-humanize_size-r1`：verification_incomplete，修改文件['formatter.py']，外部退出码1。
  - `02-humanize_metric-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `03-humanize_metric-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `04-utc_storage_contract-r1`：verification_incomplete，修改文件['store.py', 'timestamps.py']，外部退出码1。
  - `05-boltons_splitlines-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `06-boltons_splitlines-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `08-utc_storage_contract-r2`：verification_incomplete，修改文件['store.py', 'timestamps.py']，外部退出码1。
  - `09-boltons_xfrange-r2`：verification_incomplete，修改文件['sequence_core.py']，外部退出码1。
  - `10-boltons_xfrange-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `11-humanize_size-r2`：verification_incomplete，修改文件[]，外部退出码1。
- `thinking_chat`：冻结 `469aa61c74b4383e25034aab60ea109ceb7def70515ec0fb53cfb02480c2feb9`；完整轨迹、差异、首次外部验证位于 `artifacts/deepseek-validation-thinking-2026-10-25`；独立重测在 `artifacts/deepseek-independent-audit-2026-10-25/rechecks/thinking_chat`。
  - `01-humanize_size-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `02-humanize_metric-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `03-humanize_metric-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `05-boltons_splitlines-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `06-boltons_splitlines-r2`：verification_incomplete，修改文件[]，外部退出码1。
  - `09-boltons_xfrange-r2`：verification_incomplete，修改文件['sequence_core.py']，外部退出码1。
  - `10-boltons_xfrange-r1`：verification_incomplete，修改文件[]，外部退出码1。
  - `11-humanize_size-r2`：verification_incomplete，修改文件[]，外部退出码1。

## 限制与复现

三轮均使用已经看过的6道问题题目，配置是根据失败逐步调整的，不是独立留出评测；36次运行不等于36道独立题，也不更新原20题60次总体成功率。所有Token均按实际响应和失败传输记录；失败请求未知用量不计为0。
每轮保留source_snapshot.zip，内含当时准确源码及清单；当前生产代码升级后旧freeze不应与当前源码混同。原v2严格检查应在它的原始源码快照内执行。真实重跑须使用与后端匹配的本地Key配置，归档从不含凭据。
当前配置可用 `python scripts/run_deepseek_validation.py --freeze --output artifacts/new-deepseek-test`，再运行同脚本 `--run --output artifacts/new-deepseek-test`。输出目录不可覆盖；运行会产生真实API费用。

官方依据：[模型名与地址](https://api-docs.deepseek.com/)、[思考模式与temperature语义](https://api-docs.deepseek.com/guides/thinking_mode/)、[Anthropic兼容能力](https://api-docs.deepseek.com/guides/anthropic_api/)。

## 软件测试与本地配置

最终全量自动化测试267/267通过，耗时542.66秒；详见full-tests.xml。旧版本被取消的测试尝试不计为通过。网页8765已切为deepseek-flash、Chat Completions、8192总输出预算，原历史保留。Key仅在被Git忽略的本地配置中。
