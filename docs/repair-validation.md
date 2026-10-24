# 两类真实修复验证与失败回归

日期：2026-10-24。模型 `zai-org/GLM-5.3-Flash`，Chat Completions，temperature=0.2，单次请求超时 120 秒，最多 10 次工具调用，输出预算 2048。每次从故障实现开始，使用全新工作目录和会话。

## 固定协议

题目与验收测试在首次模型运行前冻结于 `benchmarks/repair_validation_tasks.json`。这是自行设计的小型任务集，不是第三方基准或真实用户仓库的随机抽样。两类题目各重复两次，所有结果保留。参考补丁仅用于模型运行前检查测试是否可用，没有复制进 Agent 工作区。

- **分页边界**：页码从 1 开始、末页、空列表、向上取整、非法页码和页大小。只允许修改 `pagination.py`，8 个测试。
- **跨文件查询契约**：服务层与仓储参数顺序、复用 ID 规范化、查找失败与输入不变性。只允许修改 `repository.py` 与 `service.py`，8 个测试；规范化实现和测试文件保持不变。

工具仅允许读取、patch、写文件和运行测试。写操作审批只放行指定实现文件；shell 仅放行 `python -B -m unittest discover -v`，没有通用 shell 自动审批。外部验证器独立运行同一测试集，并核对测试数、原始及最终文件哈希和代码差异。

两项验收分别报告：

1. **功能验收通过**：测试文件不变、修改范围合法，外部验证器的 8 个测试全部通过。
2. **完整流程通过**：在功能验收通过基础上，Agent 正常结束，且其轨迹中最后一次指定测试命令退出码为 0。

## 原始四次结果（未替换失败）

| 任务 | 次数 | 功能验收 | 完整流程 | 工具步数 | 时长 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 分页边界 | 1 | 8/8 通过 | 通过 | 8 | 149.047 秒 | 三处修改后自行重测成功 |
| 分页边界 | 2 | 未通过 | 未通过 | 10 | 146.609 秒 | malformed patch 缺少 new_text，多次拒绝后预算耗尽；代码未变化 |
| 跨文件查询 | 1 | 8/8 通过 | 未通过 | 10 | 166.750 秒 | 两文件已修复；模型没有在预算内重新运行测试，外部测试通过 |
| 跨文件查询 | 2 | 8/8 通过 | 通过 | 8 | 82.937 秒 | 两文件修复后自行重测成功 |

原始结果是 **功能验收 3/4、完整流程 2/4**，唯一任务数为 2。不能以最后一次成功替换早期失败，也不能把 16 个测试断言当成 16 个独立修复任务。

原始报告将“没有任何代码变更”宽泛记为 `protected_file_or_scope`，审计重新检查确认：受保护文件并未被改动，具体原因是 `no_code_change`。保留原始字段，并在审计中单独给出精确分类，没有修改旧结果。

## 观察到的问题与小修复

分页第二轮的 XML 风格 patch 将旧文本和新文本混在 `old_text` 中，缺失 `new_text`。校验正确拦截了执行，但错误反馈仍使用 XML 示例，模型反复提交同样错误。

本次只改进 `patch_file` 参数错误时的反馈：明确说明两个字段必须分开，给出 JSON 示例和换行转义说明。继续拒绝畸形调用，保留审批和常规参数校验；不自动猜测或执行补丁。未修改解析器、任务规则、模型、测试、步数预算或生产默认工具权限。

新增两项工具回归：畸形 patch 返回可解析 JSON 示例且不改文件；参数修正后仍走正常执行路径。相关批次最初 109 项中 108 通过，一项因历史欢迎页名称断言过期失败；更新为 CodeTrail 后，该项及工具执行器 5 项合计 6 项重跑通过。属于分批验证，不写成一次全量测试通过。

## 改进后的单独回归

仅对分页题额外进行 **1 次定向回归**，使用同一题目、测试、模型、temperature 和预算。结果：先出现一次无效 patch，收到新的 JSON 反馈后修正参数，随后 8/8 测试通过，轨迹有重测退出码 0；共 7 次工具调用、75.391 秒。

该运行与原始 4 次结果分别存档。一次定向回归不能证明稳定的成功率提升，也不能由两次时长推断成本降低。

## 独立核对与证据

`scripts/audit_repair_validation.py` 已对原始 4 次和定向 1 次全部重新运行外部测试，核对测试哈希、manifest 哈希、轨迹哈希、最终文件清单、修改范围和完整流程判定；结果与原始运行一致。

- [完整审计报告](repair-validation-2026-10-24.json)：逐次测试输出、差异、失败位置、模型参数及哈希。
- [原始版本指纹](repair-baseline-source-2026-10-24.json)：四次原始运行结束后、生产反馈改动前采集。
- 原始工作区与 trace：`artifacts/repair-validation-2026-10-24/`。
- 改进后的定向回归：`artifacts/repair-feedback-regression-2026-10-24/`。

## 自己演示

已准备两个带故意缺陷的独立示例目录：`examples/repair-demo-pagination_boundaries` 和 `examples/repair-demo-user_lookup_contract`。它们保留初始故障，不直接替换成参考修复。

在项目根目录启动其中一个：

```powershell
python -X utf8 -m pico.web --cwd examples/repair-demo-pagination_boundaries --port 8767 --model-timeout 120 --verify-command "python -B -m unittest discover -v"
```

打开本机 8767 端口，将该案例 README 中的任务复制到输入框，选择逐步确认，预算可设 10。跨文件案例将 `--cwd` 换为对应目录。输入框的通用建议不会自动识别这两个新案例，应使用 README 中的准确任务。

需要重复时生成新的目录，避免覆盖手动修改：

```powershell
python scripts/prepare_repair_demo.py pagination_boundaries --output examples/pagination-demo-next
python scripts/prepare_repair_demo.py user_lookup_contract --output examples/lookup-demo-next
```

完整固定验证可用 `python scripts/run_repair_validation.py --output artifacts/repair-validation-next` 重新执行；会产生真实模型调用费用，已有输出目录会拒绝覆盖。
