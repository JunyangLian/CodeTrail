# 最终评测的离线核对与重新运行

30道不同题包含原有20道与新增10道，每道计划3次。首批90条包含54条余额不足失败；凭据恢复又追加54次，全部修复执行数为144。安全模型测试另计，不进入修复率。原始失败不覆盖，不能把追加值替换回首批表格。

## 公开资产与私有金标

公开任务、可见验收、必要证据标注、源码SHA256及运行器位于 `benchmarks/final_agent_v1/`、`scripts/`。完整轨迹在评测证据包中。参考补丁仅用于冻结前检查“故障失败、上游修复通过”，不发布、不安装到Agent工作区，也不是公开重跑的依赖。

发布后使用 `scripts/reproduce_final_agent_eval.py` 复制已公开冻结协议，校验源码/任务/测试哈希后调用原运行器。不要从零调用资产构建器或首次私有冻结流程，它们需要控制器私有参考资产。

## 离线复核

在发布提交的干净副本安装Python 3.10+及项目测试依赖，将 `benchmarks/results/final-agent-90-2026-10-25/evidence.zip` 解压到项目根目录。证据包包含原批次、追加批次、外部验收、完整请求响应、工具轨迹、差异、源码快照、JUnit和SHA256清单。先核对 `evidence.sha256`，再按 `EVIDENCE_MANIFEST.json` 校验逐文件SHA256；不需要API Key。

```powershell
python -X utf8 scripts/check_repair_baseline.py
python -X utf8 scripts/verify_final_agent_release.py
python -X utf8 scripts/replay_repair_demo.py --run artifacts/final-agent-90-2026-10-25/runs/01-dependency_order-r1 --destination artifacts/my-offline-demo
```

回放使用保存的真实模型改动，重新运行原始验收，预期修复前失败、修复后通过。它不是新的Agent推理或新增独立任务。

## 使用自己的凭据重跑

在本地进程环境中设置 `SILICONFLOW_API_KEY`，不要写入脚本、命令行参数、Git或评测数据。仅使用硅基流动 `deepseek-ai/DeepSeek-V4.1-Flash`。先检查账户余额和模型列表，远端别名可变，不保证与本轮权重逐字一致。

```powershell
python -X utf8 -m pytest tests -q
python -u -X utf8 scripts/reproduce_final_agent_eval.py --output artifacts/my-new-90
```

`--prepare-only` 只校验并准备目录、不调用API。目录拒绝覆盖；需要执行已准备目录时使用 `python scripts/run_final_agent_eval.py --output artifacts/my-new-90 --run`。90次耗时和费用取决于服务，未知用量不可按零计。完整配置在protocol.json：温度0.2、top_p0.95、Thinking4096、输出4096、20工具步、180秒socket超时、900秒单运行墙钟、并发3，审批规则和验收固定。

新增实验必须另建输出目录。现有留出题已经评测；若根据这些结果改进Harness，后续应称其为已知回归集，不能继续声称未见留出。历史GLM、官方DeepSeek与本轮服务、版本及预算语义不同，只能作描述性对照。

## 本地网页

```powershell
python -X utf8 scripts/run_final_demo.py --check
python -X utf8 scripts/run_final_demo.py
```

该专用入口只读环境Key、不加载旧dotenv，固定硅基流动原生Tool Calling，保留网页审批。网页任务提示见 `examples/final-eval-demo/README.md`。展示不是正式评测，不能将手动演示加入指标。

## 持续回归与安全边界

静态冻结的是源码、任务、验收、证据标注和参数；远端模型、API余额、网络及Windows文件占用属于动态环境，另记事件。安全测试独立报告：脚本守卫、真实模型README攻击、500次状态IO和3个机械记忆专项分开统计。许可代码仍以当前用户执行，没有OS沙箱。已批准代码读取公开假canary的失败必须保留。
