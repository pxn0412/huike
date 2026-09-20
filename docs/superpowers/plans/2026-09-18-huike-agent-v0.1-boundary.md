# 慧科 Agent v0.1 边界收敛 Implementation Plan

> **For agentic workers:** Execute the tasks inline with verification checkpoints.

**Goal:** 将产品文档冻结为只包含比赛资料理解、资格核对、有限准备分析和竞赛画像整理的 v0.1 范围。

**Architecture:** Agent v0.1 只包含 01 比赛理解和 02 竞赛画像；03 队伍招募不属于 Agent 能力，但可以作为不调用 AI 的普通产品功能继续进入产品 Demo。03 不进入 Agent Tool、Agent 路由或 Agent 能力验收范围。

**Architecture detail:** v0.1 只配置三个 Tool：`search_competition_evidence`、`read_profile`、`save_confirmed_profile`；当前比赛由会话上下文 `current_competition_id` 和 `current_track` 绑定，当前用户由系统会话绑定。

**Tech Stack:** Markdown 产品文档、腾讯混元智能体 Tool、Git。

---

### Task 1: 更新整体产品边界

**Files:**
- Modify: `00-整体产品逻辑.md`

- [x] 删除或改写 v0.1 中关于队伍招募、自动组队、AI 推荐队友的当前能力表述。
- [x] 明确 v0.1 只包含 01 比赛信息理解、02 竞赛画像，以及资格核对和有限准备分析。
- [x] 将 03 标注为普通产品功能，不纳入 Agent v0.1 的 Tool、路由和能力验收范围；是否进入产品 Demo 单独决定。

### Task 2: 对齐比赛资料理解模块

**Files:**
- Modify: `01-比赛信息智能解析.md`

- [x] 增加 v0.1 验收边界：回答必须包含来源文件、页码、适用赛道；找不到时明确说明。
- [x] 明确资格问题只补问影响判断的必要身份信息。
- [x] 明确官方要求与 AI 建议分开展示。

### Task 2A: 冻结比赛证据 Tool

**Tool:** `search_competition_evidence`

- [x] 输入包含 `competition_id`、问题、`track` 和必要的 `evidence_type`。
- [x] 普通事实查询返回结论、原文、来源文件、页码、适用赛道。
- [x] `evidence_type: qualification` 返回学校、年级、专业、队伍人数、指导教师等资格条件集合。
- [x] 每项条件独立绑定 `value`、`quote`、`source_file`、`page`、适用赛道和状态。
- [x] 使用 `evidence_status: found / insufficient / conflict` 表示资料状态，使用 `reasoning_type: explicit / inferred` 表示证据类型，不使用混合含义的 `confidence`。
- [x] 资格判断由 Agent 将条件与用户信息逐项对照，只补问影响结论的必要信息。

### Task 3: 对齐竞赛画像模块

**Files:**
- Modify: `02-学生竞赛画像.md`

- [x] 保留自然语言追问、改写、用户确认后保存。
- [x] 明确禁止根据专业推断能力、把正在学习写成已经掌握、或未经确认写入画像。
- [x] 移除对 v0.1 队伍招募流程的依赖描述。

### Task 3A: 冻结画像 Tool 与确认凭证

**Tools:** `read_profile`、`save_confirmed_profile`

- [x] `read_profile` 只读取当前登录用户已确认并保存的画像；`user_id` 由系统会话注入，模型不能自行传入或修改。
- [x] `save_confirmed_profile` 只保存结构化画像。
- [x] 保存必须携带由前端或系统在用户明确确认后生成的 `confirmation_token` 或 `confirmation_event`。
- [x] 没有真实确认凭证时拒绝保存；沉默和模型自行构造的确认文字均不算确认。
- [x] 固定流程为：用户描述 → Agent 整理草稿 → 展示草稿 → 用户确认 → 系统生成凭证 → 保存。

### Task 4: 标记后续能力

**Files:**
- Modify: `03-智能组队匹配.md`

- [x] 将文档标题和状态标为 Agent 之外的普通产品功能。
- [x] 明确本文件不属于 Agent v0.1 的 Tool、路由和能力验收范围，但可以作为产品 Demo 的普通功能展示。

### Task 5: 文档一致性检查

- [x] 搜索“v0.1”“自动组队”“推荐队友”“缺口分析”“生成招募”等关键词，确认当前版本没有自相矛盾的能力声明。
- [x] 检查 Agent 主流程和能力验收只覆盖 01+02；产品 Demo 可另行包含不调用 AI 的 03。
- [x] 记录 Git 状态；当前目录此前不是 Git 仓库，且 GitHub 无法连接，因此本计划不声称已完成远程提交或推送。
