# 中文记忆召回与跨平台验证命令

本次二次开发修复两个可复现问题，没有新增运行依赖或改变模型 API 接入。

## 中文记忆召回

原来的分词规则只识别英文、数字和下划线。现在同时提取连续汉字词段和相邻双字片段，统一用于工作记忆及长期记忆。例如，“连接池怎么配置”能够匹配“数据库连接使用连接池”。

连续词段不拆成单字，减少常用单字噪声；独立单字仍可精确匹配。这是词法召回，不提供同义词理解或简繁转换。英文标识符匹配、tag/关键词/时间排序和文件摘要新鲜度校验保持不变。

测试覆盖：纯中文检索、无关笔记排除、中英混合查询、英文标识符大小写、中文 tag、长期记忆落盘后重载。

代码：`pico/features/memory.py`；测试：`tests/test_memory.py`。

## 跨平台验证命令

原 verifier 使用 `shell=True` 执行字符串，POSIX 单引号在 Windows cmd 中不能提供相同的参数引用。现在支持参数列表，以 `shell=False` 直接执行；路径、空格、引号和特殊字符作为参数原样传递。

简单的旧 `python -c` / `python3 -c` 字符串仍然兼容。`python`、`python3`、`python.exe`、`python3.exe` 使用当前 `sys.executable`，显式解释器路径保留。旧管道、重定向和组合命令仍交给 shell，它们的跨平台兼容性由任务作者负责。

退出码、标准输出和标准错误继续进入任务结果；输出按 UTF-8 读取，Python 子进程也使用 UTF-8 标准流。内置 12 个任务已迁移成参数列表，逐题确认验证代码及其他任务字段未变。

Windows shell 的默认环境白名单补充 `SYSTEMROOT` 和 `WINDIR`，使 Python 子进程能够初始化系统随机数。其余环境变量仍按白名单过滤，密钥排除与日志脱敏断言保持。两个原先依赖 POSIX 命令/引号的安全用例改用按平台引用的当前 Python 解释器。

测试覆盖：旧单/双引号、四个解释器别名、显式解释器路径、空格路径、中文和特殊字符、非零退出码、非法参数形状、组合命令保留及 evaluator 集成。

代码：`pico/evaluation/evaluator.py`、`pico/runtime.py`、`benchmarks/coding_tasks.json`；测试：`tests/test_verifier.py`、`tests/test_allowed_tools.py`、`tests/test_evaluator.py`、`tests/test_pico.py`、`tests/test_safety_invariants.py`。

## 本地验证

在项目根目录执行：

```text
python -X utf8 -m pytest tests/test_memory.py tests/test_verifier.py tests/test_allowed_tools.py tests/test_evaluator.py -q -p no:cacheprovider
python -X utf8 -m pytest tests -q -p no:cacheprovider
```

此环境的 pytest 创建缓存目录时曾陷入重试，以上命令禁用缓存，仍正常执行所有测试断言。另修正两个测试假设：核对记录的当前 locale；相对路径测试使用临时仓库，避免污染项目目录。

目前只在 Windows 实跑，尚未通过 Linux CI 验收。

2026-10-23 定向验证：记忆、验证器、allowed-tools，以及两个修正后的 evaluator 集成用例，共 **35 passed**。其中固定 benchmark 完整运行 **12/12 通过**。测试使用预设模型输出，没有调用付费模型。

全量首轮为 **160 passed / 2 failed**（535.27 秒）；失败的是旧 POSIX 命令与引号的两个安全用例。修正引用方式并补充 Windows 必要环境变量后，重跑两个失败用例及 security/checkpoint 检查，得到 **8 passed**（3.50 秒）。没有再重复整套耗时压力实验，因此不把分批结果描述为一次全量 162/162 通过。
