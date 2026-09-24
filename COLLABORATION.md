# 团队协作与冲刺文档 — Supplier Comparison Agent

- **团队**: Show Me Your Token
- **赛道**: Public track
- **项目**: Supplier Comparison(供应商比较 AI Agent)
- **Team Code**: `DAG1YLPM`
- **人数**: 4 人
- **截止时间**: 2026-09-28 09:00 SGT(9 月 28 日上午 9 点前提交)

---

## 一、协作模式

- **每人本地开发**:各自 `git clone` 仓库 + 建立独立 `venv`(`python -m venv .venv`),
  共用同一份网关 `.env`(内含团队共享的 `LLM_GATEWAY_API_KEY`)。
- **Lightsail 机器(`56.10.70.203`)= 部署机**:只用于跑线上站点给评审看,不在上面做日常开发。
- **网关共享**:`LLM_GATEWAY_URL` 与 API key 全队共享;注意网关会对高频请求返回 `403`(限流),
  代码里已有 `invoke_with_retry` 线性退避(3s / 6s / 9s),不要并发猛刷。
- **`.env` 绝不入库**:仓库只提交 `.env.example`(占位符),真实 key 私下传递。

---

## 二、四人分工与文件归属边界(避免合并冲突)

每个模块只由一位负责人改动,跨模块协作走 PR review,尽量不碰别人的文件。

| 角色 | 负责人 | 拥有的文件 / 职责 | 对应 rubric |
|------|--------|-------------------|-------------|
| **P1 Agent 核心 / 后端** | 待定 | `gateway_client.py`、`agent.py`、`security.py`(注入防护) | #5 安全 |
| **P2 数据与打分** | 待定 | `mock_data.py`、`tools.py`(只读工具)、`scoring.py`、输出校验 | #5.3 只读工具、#5.4 输出校验 |
| **P3 前端与部署** | 待定 | `app.py`、`templates/`、"Why this supplier?" 面板、部署到 Lightsail + Static IP | #6.A 展示 |
| **P4 QA / 可观测 / 提交** | 待定 | `observability.py`(决策日志)、`eval/`(golden + adversarial)、`tests/`、`README`、write-up PDF、demo 视频、提交到 #submission | #6.A 日志、#6.B eval |

> 归属边界说明:P1 定义系统提示词与手动 JSON tool-call 协议;P2 只提供只读数据与确定性打分;
> P3 只消费 `agent.compare()` 的结构化结果做展示,不改后端逻辑;P4 负责质量闸门与最终提交物。

---

## 三、Sprint 计划(单个 4 天冲刺,9/24 – 9/28)

| 日期 | 目标 | 关键交付 |
|------|------|----------|
| **Day 1 — 9/24(周四)** | 环境搭建 + 骨架 | 全员 clone + venv + `.env` 打通网关;确定目录结构与文件归属;各模块空壳 + 接口签名对齐 |
| **Day 2 — 9/25(周五)** | 各模块实现 | P1 网关客户端 + 注入防护;P2 mock 数据 + 打分;P3 Flask + 页面;P4 决策日志 + eval 骨架 |
| **Day 3 — 9/26(周六)** | 集成 + 首次部署 + 对抗 eval 通过 | 端到端跑通;首次部署到 Lightsail;对抗案例(注入供应商不得夺冠)eval 全绿 |
| **Day 4 — 9/27(周日)** | 打磨 + 代码冻结(晚间)+ write-up + 录 demo + Static IP | 修 bug、完善展示;**晚间代码冻结**;写 write-up PDF;录 demo 视频;绑定 Static IP,确认线上站点稳定 |
| **9/28(周一)09:00 前** | 提交 | 在 Slack `#submission` 提交全部材料(见第五节) |

**每日站会(15 分钟)**:每天固定时间(建议早 10:00)开 15 分钟 standup,每人讲三件事:
昨天做了什么、今天做什么、有什么阻塞。阻塞当场指派负责人跟进。

---

## 四、评审 rubric 映射

| 判分维度 | 覆盖模块 | 说明 |
|----------|----------|------|
| #4 Human-in-the-loop | `agent.py`、`tools.py` | **只推荐不下单**;无任何写 / 执行 / 下单工具 |
| #5 安全 | `security.py`、`agent.py` | 供应商文本包在 `<supplier_data>` 内当数据、注入检测、只读工具(#5.3)、输出校验(#5.4) |
| #6.A 可观测 / 展示 | `observability.py`、`app.py`、`templates/` | 决策日志 `decisions.jsonl` + "Why this supplier?" 逐维度面板 |
| #6.B 评测 | `eval/cases.py`、`eval/run_eval.py` | golden(正常必胜)+ adversarial(注入不得夺冠)案例与通过率 |

> 重点强调:**#4(HITL = 只推荐不下单)、#5(安全 / 注入防护)、#6(可观测 + eval)** 是本项目的核心亮点。

---

## 五、提交清单(提交到 Slack `#submission`,9/28 09:00 SGT 前)

- [ ] **Team Code**:`DAG1YLPM`
- [ ] **Project Name**:Supplier Comparison Agent
- [ ] **GitHub URL**:仓库地址(含 `supplier-compare-agent/` 目录)
- [ ] **视频 URL**:demo 视频(YouTube 或 MP4 链接)
- [ ] **Write-up PDF**:方案说明文档
- [ ] **线上站点 URL**:Lightsail 上的 live site(Static IP)

> ⚠️ 注意:提交材料**不齐可能被拒**,务必在截止前逐项对照打勾。

---

## 六、Definition of Done(完成定义)

1. 端到端能在 **Lightsail** 上跑通(线上站点可访问、可比较、可展示 rationale)。
2. **三件提交物齐全**:GitHub URL + demo 视频 + write-up PDF(外加线上站点 URL)。
3. **eval 通过**:golden 与 adversarial 案例全绿(注入供应商不夺冠、injection_flag 正确)。
4. **决策日志有输出**:每次比较都在 `decisions.jsonl` 追加一行可审计记录。
