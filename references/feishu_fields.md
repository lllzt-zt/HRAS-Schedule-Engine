# HRAS 飞书多维表格 字段映射参考（v4）

> ⚠️ 字段 ID 因飞书 Base 配置而异。
> 使用前先将此文件与 `feishu_config.json` 中的实际 ID 对照更新。
> 字段的**逻辑含义和用途**是固定的，ID 值根据你的飞书表实际情况填入。
>
> v4 数据源切换为排期新表 `tbll8oH6LyRHFxUP`。
> 业务验收（开始/结束）不占用PM时间，不纳入引擎调度。

## 语义键 → 字段说明

| 语义键 (config key) | 名称 | 类型 | 用途 |
|---|---|---|---|
| `name` | 交付项名称 | text | 任务标识 |
| `module` | 所属模块 | select | 优先级分组：绩效/人事/考勤/薪酬/流程平台/HRONE基础建设 |
| `phase` | 所属阶段 | select | 阶段一/阶段二/阶段三/持续迭代 |

### 需求阶段字段（v4新增/变更）

| 语义键 (config key) | 名称 | 类型 | 用途 | v4变化 |
|---|---|---|---|---|
| `clarify_start` | 澄清开始 | datetime | 需求澄清阶段开始 | **v4新增** |
| `clarify_end` | 澄清结束 | datetime | 需求澄清阶段结束 | **v4新增** |
| `tech_review_start` | 评审开始 | datetime | 方案评审阶段开始 | **v4新增** |
| `tech_review_end` | 评审结束 | datetime | 方案评审阶段结束 | **v4新增** |
| `design_start` | 需求开始 | datetime | 需求设计阶段起点 | 保留 |
| `design_end` | 需求结束 | datetime | 需求设计阶段终点 | 保留 |
| **`tech_review`** | **技术评审** | datetime | 排期起点，研发开始=技术评审+1wd | **v4更名**(原review_date) |

> v4规则：**仅首Task的技术评审日期由PM输入。** 后续Task的技术评审为空是正常状态，由引擎排期生成。

### 固定输入

| 语义键 (config key) | 名称 | 类型 | 用途 |
|---|---|---|---|
| `iterations` | 迭代数 | number | 工时计算因子 |
| `standard_dev_md` | 标准研发人天 | number | 研发工时。引擎计算公式覆盖此值 |
| `dev_product_ratio` | 产研比 | text | "1:2" 或 "1:3"，用于自动推导标准研发人天 |
| `reserved_canceled` | 规划预留取消 | checkbox | 标记为已取消/预留，不参与排期 |

### 资源配置

| 语义键 (config key) | 名称 | 类型 | 用途 |
|---|---|---|---|
| `dev_slots` | 投入研发 | text | 引擎动态分配，写回时覆盖此值 |
| `test_slots` | 投入测试 | text | 引擎动态分配，写回时覆盖此值 |
| `dev_count` | 研发人数 | number | 并行研发人数 |
| `test_count` | 测试人数 | number | 并行测试人数 |
| `product_owner` | 产品负责人 | user(multi) | 产品经理（PM） |
| `product_acceptance_owner` | 产品验收负责人 | user(multi) | 应与产品负责人一致 |

### 需更新的排期字段（v4新增4组）

| 语义键 (config key) | 名称 | 类型 | v4变化 |
|---|---|---|---|
| `old_clarify_start` | 澄清开始(排期结果) | datetime | **v4新增** |
| `old_clarify_end` | 澄清结束(排期结果) | datetime | **v4新增** |
| `old_review_start` | 评审开始(排期结果) | datetime | **v4新增** |
| `old_review_end` | 评审结束(排期结果) | datetime | **v4新增** |
| `old_design_start` | 需求开始(排期结果) | datetime | 保留 |
| `old_design_end` | 需求结束(排期结果) | datetime | 保留 |
| `old_dev_start` | 研发开始 | datetime | 不变 |
| `old_dev_end` | 研发结束 | datetime | 不变 |
| `old_test_start` | 测试开始 | datetime | 不变 |
| `old_test_end` | 测试结束 | datetime | 不变 |
| `old_accept_start` | 产品验收开始 | datetime | 不变 |
| `old_accept_end` | 产品验收结束 | datetime | 不变 |

### 公式字段（引擎不计算，直接读取）

| 语义键 (config key) | 名称 | 类型 | 公式 |
|---|---|---|---|
| `standard_test_md` | 标准测试人天 | number (formula) | **迭代数 × 产测比第二数值 × 5**（引擎计算，忽略Base值） |
| `dev_test_ratio` | 产测比 | text | `"1:X"`格式，**必填**，为空则引擎报错终止排期 |
| `standard_accept_md` | 产品验收人天 | number (formula) | 迭代数 × 2.5 |

### 已移除字段（v4）

| 字段 | 原因 |
|:----|:-----|
| `standard_design_md` | 不再需要，新逻辑无"插队"概念 |
| `remaining_design_md` | 不再需要，验收与需求阶段由PM Scheduler统一调度 |

## 节假日表

| 语义键 | 说明 |
|---|---|
| (节假日表使用独立字段，配置在 `holidays.json` 中) | 法定假期日期、调休日 |

## 工时公式

| 指标 | 公式 |
|---|---|
| 标准测试人天 | 迭代数 × 6 |
| 产品验收人天 | 迭代数 × 2.5 |
| 产品人天 | 迭代数 × 8 |
| 研发人天（日历） | NETWORKDAYS(研发开始, 研发结束, 节假日) × 研发人数 |
| 测试人天（日历） | NETWORKDAYS(测试开始, 测试结束, 节假日) × 测试人数 |
| **需求阶段工期（v4）** | **working_days(澄清起止) + working_days(评审起止) + working_days(需求起止)** |

## 资源池编号映射

### 研发池 (6人 + 2绩效独立)
| 编号 | 说明 |
|:----:|------|
| 1-6 | 普通研发池 |
| 101(K1), 102(K2) | 绩效独立研发，空闲时共享给普通研发 |

### 测试池 (4人)
| 编号 | 说明 |
|:----:|------|
| 1-4 | 所有任务共享 |
