# V3.2 Final Release Report（最终发布决策与项目冻结）

- 日期：2026-10-19（final round P5）
- 基线：`c30a4f3`（V3.2 Release Candidate 输入）→ 本轮收尾后打 RC 标签
- 证据组合：V3.1（S1 16 + S2 120 观测）、V3.2 DEV（144 观测）、P1 有效性审计、P2 消融（24 观测）、P3 独立 holdout（96 观测，8 新属）
- 状态机纪律：本轮所有候选**保持 ACCEPT_CANDIDATE / DEV-validated，未发布**；active strategy 全程 promptv1

## 六项最终判定

### 1. ContextBuilder 是否值得保留 → **YES（提交人工审批，暂不发布）**

| 证据 | 结果 |
|---|---|
| DEV 主实验（144 观测，3 重复） | 无效指针 A=4/72 vs B=0/72；质量层 72/72 双臂满分 |
| P1.2 删证据负例审计 | 排除证据删除假说：双臂引用画像逐项相同（516=516）、grader mismatch 双臂 0、B receipt omitted=0 |
| P2.2 消融（24 观测） | C（仅加证据块）0/12 vs D（token 等量填充）1/12——方向支持机制、不宣称统计结论 |
| 独立 holdout（96 观测） | 双臂 48/48 满分、0 无效指针、0 安全事件 → **零回归** |
| 成本 | 点估计 +12.7%（观测级）/ +13.5%（任务级配对）/ +10.4%（holdout）；**任务级配对 bootstrap CI [−3.79%, +28.65%] 含 0，不显著** |

结论：能力收益有方向性多源证据，成本增加统计上不可区分于 0 但方向为正——**ACCEPT_CANDIDATE（contextv1, `CAND-contextv1-pointer-guard`），等待人工发布审批**。

### 2. promptv2 是否仍有独立价值 → **有限，保持 DEV-validated**

promptv2（提示词级指针卫生）与 contextv1（输入组织）针对同一失败类。contextv1 具备 receipt 可审计性优势。单因素纪律下两者叠加效果**未测**——不推荐在 contextv1 审批前并行发布 promptv2；保留为 contextv1 被否决时的备选路径。

### 3. 是否有明确安全回归 → **无**

critical false pass 全程 0（S1/S2/V3.2/holdout 四轮）；快照字节保真事件（P4 发现）为行尾问题、内容逐行等价，且属评测基础设施而非运行时安全；10 项稳定性核查全绿（见 FINAL_REGRESSION_REPORT）。

### 4. Token 成本增加是否合理 → **可接受（按修正后口径）**

P1.3 审计**纠正了 V3.2 报告的统计表述**："任务级 CI 不重叠"不成立（原打印区间实际重叠；正确单位下配对 CI 含 0）。修正口径：成本点估计 +12.7~13.5%，与 0 无统计可区分性（n=24 聚类），绝对增量 ~650 tok/观测。以收益方向性 + 成本不确定性并存的状态交付审批。

### 5. Failure Attribution 是否达到可交付水平 → **是**

8 类历史失败全部完成 12 字段证据链终验（`attribution_final.json`）：**4 confirmed**（checkpoint 预算重置、ScriptedPlanner 混入、scorer 定义错误、context 崩溃）+ **3 experimentally_supported**（503、无效指针、模型返回异常）+ **1 no_field_instance_mechanism_tested**（工具 schema——诚实标注无实战实例）。规则只播种、升级必经单因素干预的框架经受了本轮审计。

### 6. 哪些结论仍仅有 DEV 支持 → **清单**

- 无效指针臂间差异 4→0 本身（holdout 双零无法判别，Wilson CI 重叠）；
- 细粒度机制（证据块 vs token 体积）——消融方向性支持但 n=12/臂；
- promptv2 的一切（未在真实 API 上进入正式 A/B）；
- 任何"策略改善"表述——全部为方向性/计数证据，无统计显著性宣称（S2 双口径纪律延续）。

## 最终交付物

| 文件 | 内容 |
|---|---|
| `docs/V3_2_AB_VALIDITY_AUDIT.md` | P1：16 字段配置审计 + 删证据负例排除 + 统计重分析 |
| `docs/V3_2_FAILURE_ATTRIBUTION_FINAL.md` | P2：8 类归因终验 + C/D 消融 |
| `docs/V3_2_INDEPENDENT_HOLDOUT_REPORT.md` | P3：8 新属独立 holdout（隔离+冻结+96 观测） |
| `docs/V3_2_FINAL_REGRESSION_REPORT.md` | P4：10 项稳定性核查 + 快照保真事件 |
| `V3_2_FINAL_RELEASE_MANIFEST.json` | 全部交付物与关键账本的 SHA256 |
| `V3_2_INTERVIEW_EVIDENCE.md` | 可核验的真实贡献清单 |

## 最终检查（全部通过）

- 全量 pytest：**206/206 passed**（exit 0，逐套件计数见 P4 报告）
- ruff：src/tests/scripts 全绿（快照目录按 pyproject exclude 排除）
- Secret scan：CLEAN（222 tracked files）
- 账本完整性：V3.2 144 行唯一格键、holdout 96 行唯一格键、VOID 0 行混入；S2 121 行（120 obs + 1 repair）与归档 SHA256 一致
- 来源隔离：holdout 8 来源六项检查全 PASS（accession/序列/池文本/组名/多样性）
- 历史回归：V2 v2.1.1 快照 parity 4/4（本轮修复后）
- Git：本轮全部新增文件待提交（见 RC commit）

## 项目冻结状态

**V3.2 = Release Candidate（RC-FINAL）**。未通过人工发布审批前：

- active strategy 保持 **promptv1**（未被任何自动流程切换）；
- `CAND-contextv1-pointer-guard` 维持 validated cap（模拟审批阻断已实测，见 V3.2 P6）；
- 待人工决策：① contextv1 发布审批；② promptv2 处置；③（可选）V3.1 飞轮候选评审。

授权与安全遗留：SiliconFlow key 此前在聊天中暴露过（用户已豁免轮换，key 为免费层）；`.env` 不入库纪律保持。
