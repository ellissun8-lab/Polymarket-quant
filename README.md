````markdown
# std0-quant

> **让 AI 负责发现 Alpha，让确定性系统负责保护资金。**

**Agent 驱动的自动化 Alpha 研究 + 确定性交易执行治理系统**

`std0-quant` 是一套面向 Polymarket 市场微观结构研究的可审计量化系统。

核心设计是严格分离：

- AI / Agent 研究与策略发现
- Factor / Alpha 生成与验证
- 确定性风险控制
- SHADOW 执行
- Measured Venue Execution Evidence
- Production Eligibility Governance
- LIVE 真实资金执行

AI 可以研究、生成候选和产出证据，但不能直接持有真实交易权限，也不能绕过确定性的风险与生产治理。

> **当前状态：Research / Validation / SHADOW / Execution Evidence / Production Governance 已实现。**
>
> **LIVE Execution 当前明确保持 STOP / NOT AUTHORIZED。**

---

## Architecture

```mermaid
flowchart TD
    A[Research Brain / Agent] --> B[Factor Factory]
    B --> C[OOS / Null / Baseline Validation]
    C --> D[Factor Registry]

    D --> E[Alpha Factory]
    E --> F[Strategy Candidate]

    F --> G[Deterministic Risk Gate]
    G --> H[OrderIntent]

    H --> I[Batch SHADOW]
    I --> J[Strategy Shadow Run]

    J --> K[Execution Validation Provenance]
    K --> L[Execution Validation Policy]

    L --> M[Execution Governance Bridge]
    M --> N[Measured Venue Execution Evidence]

    N --> O[Production Eligibility Governance]
    O --> P[LIVE]

    P:::blocked

    classDef blocked fill:#fdd,stroke:#d33,stroke-width:2px;
````

核心原则：

```text
Research Brain THINK
        ↓
Deterministic System ACT
```

LLM 不进入毫秒级执行路径。

AI Agent 不允许：

* 持有 LIVE credentials
* 加载 private key
* 直接向 venue 下单
* 绕过 Risk Gate
* 修改冻结的研究定义
* 直接修改 Factor Registry 状态
* 自动执行 Production Promotion
* 直接进入 LIVE

---

## Governance Model

系统遵循：

```text
Learn
  ↓
Candidate
  ↓
Validate
  ↓
Version
  ↓
Deploy
```

而不是：

```text
Learn → Production
```

研究成功、回测成功、SHADOW 成功，都不能直接授权真实资金执行。

核心状态严格区分：

```text
SHADOW PASS
    !=
EXECUTION PASS
    !=
PRODUCTION_ELIGIBLE
```

---

## Current Status

| Layer                                      | Status   | Notes                                |
| ------------------------------------------ | -------- | ------------------------------------ |
| Episode / FirstOpposite / Y30              | CLOSED   | Frozen research semantics            |
| Prospective Research Plumbing              | CLOSED   | Point-in-time / leakage controls     |
| Factor Factory                             | CLOSED   | Factor generation + validation       |
| Factor Registry                            | CLOSED   | Explicit lifecycle governance        |
| Alpha Factory                              | CLOSED   | Factor → strategy composition        |
| Strategy Candidate                         | CLOSED   | Deterministic strategy artifact      |
| Risk Gate                                  | CLOSED   | Deterministic authorization boundary |
| Batch SHADOW                               | CLOSED   | No real venue submission             |
| Strategy Shadow Run                        | CLOSED   | Auditable SHADOW orchestration       |
| Execution Validation Provenance            | CLOSED   | Evidence identity + hash binding     |
| Execution Validation Policy v1             | CLOSED   | PASS intentionally unreachable       |
| Execution Governance Bridge                | CLOSED   | Registry / execution governance      |
| Measured Venue Execution Evidence v1       | CLOSED   | Strict venue evidence model          |
| Production Eligibility Gate v1             | CLOSED   | ELIGIBLE intentionally unreachable   |
| Measured Venue Telemetry Import Adapter v1 | CLOSED   | Strict offline JSONL ingestion       |
| LIVE Execution                             | **STOP** | Not authorized                       |

---

## Agentic Research

Research Brain / Agent 负责自动化研究与候选发现。

Agent 可以：

* 分析研究数据
* 生成 Factor Candidate
* 运行 OOS 验证
* 运行 Null Test
* 比较 Baseline
* 分析 Temporal Stability
* 生成 Strategy Candidate
* 产出 Evidence Artifact
* 提议新版本

Agent 不可以：

* 修改 Frozen Definitions
* 绕过验证策略
* 直接 Promote Factor
* 修改 Production Eligibility
* 加载 LIVE Credentials
* 提交真实订单

AI 层保持灵活。

涉及资金的动作保持确定性、可回放和可审计。

---

## Factor Factory

Factor Factory 将研究假设转换为版本化 Factor Artifact。

主要验证包括：

* Out-of-Sample Evaluation
* Train Prevalence Baseline
* Permutation Null Test
* Temporal Stability
* Brier Score
* Log Loss
* Macro AUC
* Weighted AUC
* Provenance
* Artifact Hash

Factor 生命周期：

```text
CANDIDATE
   ↓
VALIDATED
   ↓
PRODUCTION_ELIGIBLE
```

其中：

```text
CANDIDATE → VALIDATED
```

需要研究验证与时间稳定性证据。

而：

```text
VALIDATED → PRODUCTION_ELIGIBLE
```

需要独立的执行验证证据。

Research PASS 本身不能完成 Production Promotion。

---

## Alpha Factory

Alpha Factory 消费验证后的 Factor，并构造 Strategy Candidate。

主链：

```text
Factor Registry
      ↓
Alpha Factory
      ↓
Strategy Candidate
      ↓
Deterministic Risk Gate
      ↓
OrderIntent
```

Research Layer 本身不会直接触发资金执行。

---

## Deterministic Risk Boundary

所有可执行决策最终必须进入确定性的 Risk Gate。

执行链被拆分为：

```text
Strategy Reasoning
        ↓
Risk Authorization
        ↓
OrderIntent
        ↓
Execution Transport
        ↓
Venue Events
        ↓
Fill Accounting
        ↓
Execution Evidence
        ↓
Production Governance
```

这样可以让整个执行决策链：

* 可回放
* 可验证
* 可审计
* 可追溯
* Fail Closed

---

## SHADOW Execution

当前系统支持完整 SHADOW 执行路径。

SHADOW 用于验证：

* Strategy → Risk → OrderIntent
* Batch Execution
* Order State
* Queue / Fill Logic
* Latency Model
* Execution Integration
* Provenance

但不会向真实 venue 提交订单。

Synthetic SHADOW ACK 会被显式标记，并且不能冒充真实 execution evidence。

因此：

```text
SHADOW SUCCESS
```

不等于：

```text
REAL EXECUTION QUALITY
```

更不等于：

```text
PRODUCTION_ELIGIBLE
```

---

## Measured Venue Execution Evidence

Measured Venue Execution Evidence v1 用于描述外部采集的真实 venue telemetry。

Evidence 会绑定：

* Strategy Identity
* Risk Identity
* OrderIntent
* Venue Event Timeline
* Venue Order ID
* Fill Quantity
* Cumulative Filled Quantity
* Remaining Quantity
* Receive Timestamp
* Venue Timestamp
* Execution Provenance
* Source Artifact
* Evidence Artifact Hash

系统会 Fail Closed 地拒绝：

* Duplicate Intent
* Duplicate Event
* SHADOW Synthetic ACK
* 不一致 Venue Order ID
* 非单调 Receive Timeline
* Fill Accounting 不一致
* Cumulative Fill 超过 Intent Quantity
* 非 Fill Event 修改累计成交数量
* 不完整或冲突的 Execution Provenance

Evidence Layer 本身不会：

* 下单
* 加载凭证
* 修改 Registry
* 自动 Promotion
* 直接产生 Production Eligibility

---

## Measured Venue Telemetry Import Adapter

Telemetry Import Adapter 提供一个严格的离线输入边界：

```text
raw external JSONL bytes
        ↓
strict UTF-8 decode
        ↓
strict JSONL parse
        ↓
schema validation
        ↓
duplicate-key validation
        ↓
numeric validation
        ↓
normalization
        ↓
MeasuredVenueExecutionSource
+
MeasuredVenueExecutionObservation[]
```

`source_artifact_hash` 直接基于：

```text
SHA256(raw_input_bytes)
```

并在 decode / normalize 之前计算。

这证明的是：

```text
Content Identity
```

而不是：

```text
Trusted Venue Origin
```

Import Adapter 当前：

* 只接受 raw `bytes`
* 严格 UTF-8
* 拒绝空输入
* 拒绝空行
* 拒绝非法 JSON
* 拒绝非 object
* 递归拒绝 Duplicate JSON Key
* 拒绝缺字段
* 拒绝 Extra Fields
* 拒绝 Numeric String Coercion
* 拒绝 Boolean 冒充 Number
* 保留原始 Record / Event 顺序
* 不自动排序
* 拒绝 Duplicate Intent ID
* 拒绝 Duplicate Event ID
* 拒绝已知 SHADOW-only Evidence
* 不访问网络
* 不加载 Credentials
* 不提交订单
* 不修改 Registry
* 不执行 Promotion
* 不产生 Execution PASS

---

## Production Eligibility Governance

Production Eligibility 和 Research Validation 是两套不同的治理链路。

```text
Validated Factor
      +
Strategy Identity
      +
Risk Identity
      +
Execution Validation Decision
      +
Measured Execution Evidence
      +
Provenance
      ↓
Production Eligibility Decision
```

当前 v1 中：

```text
Execution Validation Policy v1
```

不能产生：

```text
PASS
```

同时：

```text
Production Eligibility Gate v1
```

不能产生：

```text
ELIGIBLE
```

这是故意设计的安全边界。

只有未来定义并验证真正的：

```text
Measured Venue Execution
        ↓
Execution PASS
        ↓
Production Eligibility
```

正向路径之后，Production Promotion 才可能被开放。

因此以下任何结果都不能单独授权真实资金交易：

* Backtest PASS
* Research PASS
* Factor VALIDATED
* Simulation PASS
* SHADOW PASS

---

## Frozen Research Semantics

Episode / FirstOpposite / Y30 定义已经冻结。

核心规则：

* 同方向 BUY gap `<= 3000 ms` 合并
* `3000 ms` 合并
* `> 3000 ms` 拆分
* Initial Direction 取最早 BUY
* 同秒 Up / Down 首次 BUY 歧义显式标记
* FirstOpposite 是 Parent Episode 初始方向相反的第一次 BUY
* Y30 使用 FirstOpposite End 作为 `t0`
* Y30 正样本窗口为：

```text
(t0, t0 + 30s]
```

即：

* `t0` 不包含
* `+30s` 包含

生命周期不完整时：

```text
CENSORED
```

而不是自动标记为：

```text
0
```

系统同时坚持 Point-in-Time Integrity，不对原始数据无法支持的时间精度做虚假推断。

---

## Testing & Auditability

系统采用三层测试：

```text
Focused Tests
      ↓
Adjacent Regression
      ↓
Full Repository Regression
```

关键 Fail-Closed 行为包括：

* Provenance Mismatch
* Factor Identity Mismatch
* Strategy Identity Mismatch
* Risk Identity Mismatch
* Duplicate Execution Event
* Invalid Fill Accounting
* SHADOW Evidence 冒充 Measured Evidence
* Invalid Telemetry Schema
* Duplicate JSON Keys
* Implicit Numeric Coercion
* Execution PASS Unreachable
* Production ELIGIBLE Unreachable

关键 Artifact 通过 Hash 绑定。

目标是让：

```text
Research
Execution
Evidence
Governance
```

全部可以被独立审计和重建。

---

## Repository Layout

```text
src/std0_quant/
├── alpha/
├── execution/
├── factors/
├── registry/
├── research/
├── risk/
└── ...

tests/
├── execution/
└── ...

release/
manifest/
bootstrap/
```

其中 Execution Layer 主要覆盖：

* Execution Contracts
* SHADOW Integration
* Strategy Shadow Run
* Execution Validation
* Provenance
* Execution Governance
* Measured Venue Evidence
* Telemetry Import
* Production Eligibility

---

## What This Project Demonstrates

这个项目主要展示：

* Agentic Quant Research
* AI-assisted Factor Discovery
* Factor Factory
* Alpha Factory
* Point-in-Time Research
* Temporal Validation
* Deterministic Risk Guardrails
* Trading System Architecture
* SHADOW Execution
* Execution Provenance
* Measured Venue Telemetry
* Production Governance
* Reproducible Research Infrastructure
* AI 与真实资金执行权限隔离

它不是一个：

```text
AI 自动赚钱机器人
```

也不应被理解为：

* 已上线 LIVE Trading
* 已证明长期盈利
* 已证明稳定 Sharpe
* 已证明 Production Latency
* 已完成 Production Trading Authorization

---

# Historical AWS Recorder Migration Provenance

仓库同时保留历史 AWS Recorder Migration Bundle：

```text
std0-quant-aws-20260825T170402Z
```

这一部分用于保留基础设施迁移与研究数据采集 provenance，不代表当前 Production Execution 能力。

迁移验证包括：

* Frozen Migration Manifest
* SHA256 Verification
* Stale PID 排除
* Old Session 排除
* Raw Runtime State 排除
* Session Stitching 禁止
* Clean Ubuntu Extraction
* Python 3.14.4 Bootstrap
* Project Import
* Full Pytest
* Frozen Governance
* Binance Public Connectivity
* Gamma Public Connectivity
* CLOB Public Connectivity
* Verify 阶段不启动 Recorder

最终 verifier 达到：

```text
READY_TO_START_NEW_AWS_SESSION
```

并返回：

```text
EXIT=0
```

同时明确：

```text
O3 starts at 0/86400
```

旧 Windows Session 与新 AWS Session 禁止拼接。

---

## Verify Historical Migration Bundle

```bash
git clone https://github.com/howei-ai/Polymarket-quant.git

cd Polymarket-quant/release

sha256sum -c std0-quant-aws-20260825T170402Z.tar.gz.sha256

mkdir -p ~/std0-quant-migration

tar -xzf std0-quant-aws-20260825T170402Z.tar.gz \
  -C ~/std0-quant-migration

cd ~/std0-quant-migration/std0-quant-aws-20260825T170402Z

sha256sum -c manifest/sha256sums.txt

bash bootstrap/bootstrap_ubuntu.sh

bash bootstrap/verify_before_start.sh
```

验证应停止在：

```text
READY_TO_START_NEW_AWS_SESSION
```

不要继续启动 Recorder。

---

## Safety

本仓库属于量化研究、执行验证与生产治理软件。

本仓库不授权：

* 启动真实资金交易
* 提交真实 venue order
* 自动 Promotion 到 Production
* AI Agent 持有 LIVE Credentials
* AI Agent 绕过 Risk Gate
* AI Agent 直接控制真实资金

**LIVE Execution 当前明确保持 STOP。**

只有未来独立满足：

```text
Measured Execution Validation
        +
Production Governance
        +
Operational Controls
        +
Venue Authorization
        +
Credential Isolation
        +
Risk Controls
```

之后，才可能讨论进入真实 Production Execution。

```
```
