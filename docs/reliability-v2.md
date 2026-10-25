# CodeTrail：Recall诊断与Windows执行隔离

本轮在2026-10-25历史评测提交 `d2bb48c9b4f803d5a544c244f688a76378750d06` 之后完善运行时，不新增代码修复题、不重新调优留出、不调用模型重测。历史30题、验收、证据金标、144条运行、原13/14守卫和6/6模型攻击报告均保留。新安全专项与软件回归另存版本，不写回旧成功率。

## Recall@5到底低在哪里

旧指标是“Agent前5个去重成功read/search文件中，覆盖冻结必要文件的比例”，不是独立检索器候选排名质量。分母按每次执行累计必要文件，采用微平均：

| 切片 | 命中/必要文件观察 | 旧Recall@5 |
|---|---:|---:|
| 全144次，主报告原值 | 141/265 | 53.21% |
| 首批90次 | 60/168 | 35.71% |
| 凭据恢复追加54次 | 81/97 | 83.51% |
| 无HTTP402的90次描述性切片 | 137/168 | 81.55% |

最后一行仅帮助定位余额中断的影响，不能从主分母删除54次失败，更不能作为新模型或Harness收益。少量余额中断运行也已取得部分证据，因此不能简单把它们全算作零命中。

另发现先前四道boltons定位题存在标注错误：`required_files`直接复用了所有可修改文件，把不在可见测试本地静态导入链上的config.py、transport.py、validation.py也标为必要证据。静态导入链和原始冻结集合在 `recall-diagnosis.json` 并列记录。必要证据与可修改范围不是一个概念。此次只发布勘误，不修改原金标、不发布一个事后更漂亮的替代分数。

排除余额故障后仍有14次未覆盖全部冻结必要文件；其中两次dotenv_override只读main.py，没读预先标记的variables.py契约，虽然功能通过，也不能声称证据完备。其余遗漏多与上述四题错误标注有关，部分文件在前5个之后才读到。逐次缺失文件、后读文件及功能结果见 `retrieval-misses.csv`。

实现上强调从失败断言/符号搜索再读定位范围，必要时跟踪真实调用和导入，避免把整个目录读完。没有开展新的受控模型复测，因此**本轮未证明Recall或修复率提升**。下一版必要证据应在未来运行前独立标注，并把检索器排名、Agent访问覆盖和上下文送达分别定义；当前留出若用于改进，应降级为已知回归集。

## AppContainer路径的执行边界

本机没有Docker/Podman或WSL发行版。Windows路径使用每次新建的零能力AppContainer配置和受限Job Object，通过内核 `TokenIsAppContainer` 核验后才恢复子进程。仅允许精确命令 `python -B -m unittest discover -v`，原有写入和命令审批仍生效。设计依据见[微软AppContainer启动说明](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)和[隔离机制](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)。

- 不挂载宿主工作区。只复制过滤后的文件到新配置私有目录，排除.env/known.env、密钥扩展名、运行时状态和已知环境敏感值，拒绝符号链接、重解析点、硬链接及超大输入。
- 每次复制独立CPython与标准库，不复制site-packages，不使用用户site或PYTHONPATH。子进程环境使用显式最小列表，不继承API Key。
- 不授予网络能力；检查BFE/MpsSvc运行状态，不修改防火墙或既有ACL。已测试本地TCP/UDP控制监听器，外网出口仍为未评估。
- Job限制1个活动进程、单进程256MiB，关闭时终止任务；等待超时或输出超过64KiB会终止Job。子进程在加入Job并验证内核令牌前保持挂起。
- 不将执行副本的修改同步回宿主；完成后用系统API删除本次唯一私有配置，记录清理状态。快照、内核令牌、错误、超时和清理证据写到 `.pico/sandbox/<id>/audit.json`。
- 隔离启动/校验失败直接拒绝；不自动回退到宿主执行。调用侧需要已有批准才能执行。

这个路径是OS访问控制隔离，仍共享Windows内核，不是虚拟机。AppContainer可能访问系统本来公开授权的资源；本轮只证明受测宿主私有canary边界，不承诺整个系统所有资源不可读。它不支持任意命令、第三方依赖或多进程任务，也没有磁盘用量硬配额。标准库复制有启动成本，尚未测量生产吞吐。宿主并发恶意修改、内核漏洞、所有网络出口、任意Prompt Injection泛化均未评估。

`host`执行路径为兼容模式，仍没有OS隔离；通用CLI/网页默认保持host，界面明确显示执行模式。专用 `scripts/run_final_demo.py` 默认使用appcontainer；`--host-shell` 必须显式选择，不是自动降级。

## 安全观察与失败记录

相同14个冻结守卫使用相同判定、审批和公开canary在AppContainer后端复测通过14/14。旧13/14不变：这说明批准代码读.env的已知场景被新后端阻断；不是让旧失败消失，也不是任意攻击零风险。正常Python测试另作阳性对照，防止把“根本不能执行”误当安全成功。

首次错误码型网络专项结果为6通过、1失败、1错误：TCP超时不是预期的PermissionError，UDP connect本身也不能证明数据送达。原失败完整保留。之后采用独立的受控监听协议：宿主TCP/UDP阳性包必须可达，隔离探测不得到达；实际只收到宿主包。这个结论限于本地监听器，不能外推所有外网出口。不得把新的协议说成初次8项全部通过。

全量回归包含真实OS宿主读写拒绝、内存上限、输出洪泛终止和正常计算。8项最初隔离探针、后续网络协议、14项固定守卫、自动化软件测试按不同分母报告。

## 可靠性改动

1. 普通批量入口遇HTTP401/402/403或返回模型ID不符时停止派发，保留已经启动和未启动槽位。自动化测试验证首个余额失败后剩余89槽位未启动，未伪造失败补齐。
2. 原生消息加入剩余工具步提示，拒绝也计步；最后一步隐藏写工具，并在执行层再次拒绝写入，保留测试机会。没有放宽20步上限或审批。
3. schema展示timeout的真实1–120秒范围；隔离路径展示精确命令枚举；拒绝反馈提醒不要使用管道和替代命令。原生多调用仍严格拒绝并关闭全部真实tool ID，不串行偷执行，不启用文本/DSML后备。

这些改动已做机制回归，但没有新的模型实验，不能报告协议错误、工具步数或Token成本下降。最后一步保留测试仍不保证模型一定调用测试；实际最后修改后验证门禁继续判定完成。

## 运行与复核

```powershell
python -X utf8 -m pytest tests -q --junitxml=artifacts/my-v2-tests.xml
python -X utf8 scripts/check_isolation_guard_regression.py --output artifacts/my-v2-guards
python -X utf8 scripts/check_appcontainer_network.py --output artifacts/my-v2-network
python -X utf8 scripts/run_final_demo.py
```

没有配置模型时也可运行前三项，不使用API Key。AppContainer要求Windows、标准CPython和运行中的系统过滤服务；不满足时明确报错。若要通用网页隔离执行，使用 `python -m pico.web --cwd <workspace> --shell-backend appcontainer --verify-command "python -B -m unittest discover -v"`。

历史冻结运行器会拒绝当前v2源码哈希；重现历史实验应先切到提交d2bb48c的干净副本，或按旧证据中的source_snapshot恢复对应源码，再执行旧复现命令。不能在v2目录里修改旧freeze使其通过。

本轮完整自动化测试实际结果：308项全部通过，耗时678.07秒；JUnit随本轮证据包发布。目标针对性回归51项与后续16项机制/OS测试单列保留，不累计成更多独立测试或修复题。
