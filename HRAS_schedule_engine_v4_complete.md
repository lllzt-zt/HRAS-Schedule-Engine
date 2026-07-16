# HRAS 排期引擎 v4 完整逻辑手册

> 本文档涵盖引擎所有逻辑：数据管道、过滤规则、日历、字段、工时、资源池、调度算法、PM调度、验收冲突、写回、校验、报告。
> 适用于 v3→v4 完整变更确认。

---

## 第一章：数据管道

### 1.1 数据源

| 数据 | Base Token | 表 ID | 说明 |
|:----:|:----------:|:-----:|------|
| 任务主表 | `BKBKbUWXtas7tSshzoccyZa3ndb` | `tbll8oH6LyRHFxUP` | 后续排期表（含需求阶段字段）|
| 节假日表 | 同上 | `tblMm7ZxaIHNPWlG` | 📅配置表 |

> 业务验收（开始/结束字段）不占用PM时间，属于业务方独立角色任务，不纳入排期引擎调度。引擎只处理产品验收。

### 1.2 拉取命令

```bash
# 拉取任务数据（24+字段）
lark-cli base +record-list \
  --base-token $(jq -r '.base_token' feishu_config.json) \
  --table-id $(jq -r '.task_table_id' feishu_config.json) \
  --field-id ...(所有字段ID)... \
  --limit 200 --format json --as bot > tasks.json
```

### 1.3 数据铁律

> **每次排期必须重新拉取飞书在线表最新数据。严禁从历史会话目录直接读取缓存文件。**

三步串行不可跳过：
1. ✅ 拉飞书数据
2. ✅ 运行引擎
3. ✅ 写回飞书

### 1.4 数据转换

`transform_bitable_to_tasks()` 引擎函数：
- 将飞书 `+record-list` 原始 JSON 按字段ID映射解析
- PM字段（用户类型）取 `[0]["name"]`
- `dev_slots == "独立团队"` → `is_perf_dev = True`
- `phase == "持续迭代"` → `is_continuous = True`
- 排除已取消（reserved_canceled）的记录

---

## 第二章：过滤规则

### 2.1 数据转换时过滤

| 条件 | 处理 |
|------|:----:|
| `reserved_canceled` 有值 | 跳过 |

> v4规则：**不再过滤`技术评审`为空的Task**。首Task由PM给定技术评审日期，后续Task的技术评审为空是正常状态（由引擎排期生成）。

### 2.2 main() 中过滤

| 条件 | 处理 |
|------|:----:|
| `reserved_canceled` 有值 | 排除 |
| `tech_review < today` **且** 为首Task **且** 非持续迭代 | 排除（历史锚点Task）|
| `tech_review` 为 null 的后续Task | **不跳过**，由引擎排期生成 |
| 持续迭代且有评审日期 | 参与正常排期（占用资源池） |

> **首Task识别（v4按PM分组）**：
> 每个PM独立识别自己的首Task。扫描该PM下所有任务，有技术评审日期且最早（≥today）的为该PM的首Task。
> 该PM下其余任务（即使Base中有技术评审值但比首Task晚）均为后续Task，由引擎重新计算。
> **不再因为评审为空而排除。**

---

## 第三章：工作日历

### `WorkingDayCalendar` 类

```python
is_working_day(d):
  调休补班 → True
  周六/周日 → False
  法定假日 → False
  否则 → True
```

节假日数据来源：飞书📅配置表的 `法定假期日期` 和 `调休日` 字段。

**注意：引擎不校验节假日数据本身的准确性，需要确保飞书Base中节假日与国务院官方通知一致。**

---

## 第四章：字段模型（v4）

### 4.1 完整字段列表

| 引擎变量 | 飞书字段 | 类型 | 用途 | v4变化 |
|---------|---------|:----:|------|:------:|
| `name` | 交付项名称 | text | 任务标识 | 不变 |
| `module` | 所属模块 | select | 优先级分组 | 不变 |
| `phase` | 所属阶段 | select | 阶段标识 | 不变 |
| **`clarify_start`** | **澄清开始** | date | 需求澄清开始 | **新增** |
| **`clarify_end`** | **澄清结束** | date | 需求澄清结束 | **新增** |
| **`tech_review_start`** | **评审开始** | date | 方案评审开始 | **新增** |
| **`tech_review_end`** | **评审结束** | date | 方案评审结束 | **新增** |
| `design_start` | 需求开始 | date | 需求设计开始 | 保留 |
| `design_end` | 需求结束 | date | 需求设计结束 | 保留 |
| **`tech_review`** | **技术评审** | date | 需求阶段最后时间 | **更名**(原需求评审) |
| `iterations` | 迭代数 | number | 工时计算核心因子 | 不变 |
| `dev_product_ratio` | 产研比 | text | "1:2"或"1:3" | 不变 |
| `dev_count` | 研发人数 | number | 并行研发人数 | 不变 |
| `test_count` | 测试人数 | number | 并行测试人数 | 不变 |
| `dev_slots` | 投入研发 | text | 引擎分配的研发槽位 | 不变 |
| `test_slots` | 投入测试 | text | 引擎分配的测试槽位 | 不变 |
| `product_owner` | 产品负责人 | user | PM | 不变 |
| `product_acceptance_owner` | 产品验收负责人 | user | PM验收人 | 不变 |
| `reserved_canceled` | 规划预留取消 | checkbox | 是否排除 | 不变 |
| `standard_dev_md` | 标准研发人天 | number | 公式字段（对比用） | 不变 |
| `standard_test_md` | 标准测试人天 | number | 公式字段 | 不变 |
| `standard_accept_md` | 产品验收人天 | number | 公式字段 | 不变 |
| `standard_design_md` | 标准产品设计人天 | number | 公式字段 | 不变 |
| `remaining_design_md` | ~~冲突后剩余设计人天~~ | **v4移除**(不再需要) |

### 4.2 排期结果字段

| 引擎变量 | 飞书字段 | v4变化 |
|---------|---------|:------:|
| **`new_req_start`** | 澄清/评审/需求开始 | **新增**(引擎排期结果) |
| **`new_req_end`** | 澄清/评审/需求结束 | **新增**(引擎排期结果) |
| `old_dev_start/end` | 研发开始/结束 | 不变 |
| `new_dev_start/end` | 研发开始/结束(新排期) | 不变 |
| `old_test_start/end` | 测试开始/结束 | 不变 |
| `new_test_start/end` | 测试开始/结束(新排期) | 不变 |
| `old_accept_start/end` | 验收开始/结束 | 不变 |
| `new_accept_start/end` | 验收开始/结束(新排期) | 不变 |

---

## 第五章：工时计算

### 5.1 研发人天

| 产研比 | 计算公式 |
|:------:|:--------:|
| `1:2` | 迭代数 × 10 |
| `1:3` | 迭代数 × 15 |
| 无 | 0（跳过研发阶段） |

**对比逻辑**：计算值与Base中 `standard_dev_md`（标准研发人天）对比，不一致则标记 `dev_man_days_changed = True`

### 5.2 测试人天

```
优先读取 Base standard_test_md（公式字段，>0时使用）
空值降级：迭代数 × 6.0
```

### 5.3 验收人天

```
优先读取 Base standard_accept_md（公式字段，>0时使用）
空值降级：迭代数 × 2.5
```

### 5.4 需求阶段工期（v4新增）

```
req_wd = working_days(clarify_start, clarify_end)
       + working_days(tech_review_start, tech_review_end)
       + working_days(design_start, design_end)
```

> `req_wd` 的具体换算公式（从迭代数计算）后续补充。

### 5.5 工作日转换

| 阶段 | 公式 | 最低值 |
|:----:|:----:|:------:|
| 研发工作日 | `ceil(dev_man_days / dev_workers)` | max(1, ...) |
| 测试工作日 | `ceil(test_man_days / test_workers)` | max(1, ...) |
| 验收工作日 | `ceil(accept_man_days / 1)` | max(1, ...) |
| 需求工作日 | `req_wd`（固定工期） | — |

- `dev_workers`：绩效独立=2，普通=`dev_count`（默认2）
- `test_workers`：默认2
- 验收始终 1 人串行（PM本人）

---

## 第六章：资源池

### 6.1 资源池定义

| 池 | 槽位 | 容量 | 说明 |
|:--:|:----:|:----:|------|
| **研发池(普通)** | 1,2,3,4,5,6 | 6人 | 非绩效任务使用 |
| **研发池(绩效独立)** | K1=101, K2=102 | 2人(动态共享) | 绩效独占，空闲时开放给普通 |
| **测试池** | 1,2,3,4 | 4人 | 所有任务共享 |
| **PM时间线** | 无槽位 | 1人 | 需求阶段+验收串行，独占PM |

### 6.2 ResourcePool 核心方法

```python
class ResourcePool:
    slots: list          # 槽位ID列表
    busy: dict           # slot_id -> set(busy_dates)
    
    is_free(slot_ids, d, cal)          # 指定槽位在某日是否空闲
    are_all_free(slot_ids, start, end, cal)  # 指定槽位在整段是否空闲
    occupy(slot_ids, start, end, cal)  # 标记槽位占用
    earliest_free_window(...)           # 找最早可用窗口
```

### 6.3 研发资源分配

**主函数**：`PhaseScheduler.step_with_extra()`

```
绩效任务：
  → 固定使用 [101, 102]（K1, K2）
  → 两个槽位必须同时空闲，否则跳过

非绩效任务：
  → 无预分配槽位 → _find_free_slots() 从 1-6+K1/K2 动态分配
  → 有预分配槽位 → 尝试首选槽位
  → 首选槽位忙 → 尝试借用 K1/K2 中的一个（若绩效无等待任务）
```

### 6.4 动态槽位分配 `_find_free_slots()`

```python
研发阶段：
  非绩效: pool = [1,2,3,4,5,6] + [101,102](绩效空闲可共享)
  绩效:   固定 [101, 102]
测试阶段：
  pool = [1,2,3,4]（所有任务共享）
  
needed = dev_workers 或 test_workers
从 pool 取 C(n, needed) 组合，返回第一个全闲的
```

### 6.5 K1/K2 每日共享判断

```python
# 检查 101/102 今日是否空闲
perf_free = [s for s in [101,102] if dev_pool.is_free([s], sim_day, cal)]
# 检查等待队列中是否有绩效任务
perf_queued = any(t.is_perf_dev for ... in ready_queue)
# 无绩效等待 → 非绩效可借用空闲的 K1/K2
extra_slots = perf_free if not perf_queued else []
```

### 6.6 PM时间线（v4新增）

PM有两条任务类型，**互斥执行**：

| 类型 | 占用PM | 说明 |
|:----:|:------:|------|
| 需求阶段 | ✅ 独占 | 澄清+评审+需求设计 |
| 验收 | ✅ 独占 | 产品验收 |

**不占用PM**（可以并行）：
- 研发（走研发池）
- 测试（走测试池）

---

## 第七章：调度算法

### 7.1 主循环：时间驱动逐日模拟

```
sim_day = today
for _ in range(1825):
  1. 跳过非工作日
  2. 检入新就绪研发任务（tech_review + 1wd ≤ sim_day）
  3. 研发阶段调度（含绩效槽位共享）
  4. 检入研发→测试（dev_ends < sim_day）
  5. 测试阶段调度
  6. 检入测试→验收（test_ends < sim_day）
  7. 验收阶段调度（含PM Scheduler）
  8. 检查全部完成
  9. sim_day += 1
```

### 7.2 研发阶段调度

```
就绪条件: tech_review + 1wd ≤ sim_day
处理顺序: 绩效 > 人事 > 考勤 > 薪酬 > 流程平台 > HRONE基础建设
          （优先级仅仲裁同槽位竞争，空闲资源不囤积）

资源分配：
  绩效：独占 [101,102]
  非绩效：_find_free_slots() 动态分配
```

### 7.3 测试阶段调度

```
就绪条件: dev_end + 1wd ≤ sim_day
软间隔约束(max-gap=5wd): 超时提升紧急度，不阻塞
槽位: 引擎动态分配 1-4
```

### 7.4 验收阶段调度（含PM Scheduler）v4

**验收就绪条件**: `test_end + 1wd ≤ sim_day`

**PM Scheduler 流程**（每个模拟日执行）：

```
步骤 ① 检入：
  扫描该PM所有Task → 标记新就绪的验收(test_end + 1wd ≤ sim_day)

步骤 ② PM空闲时：
  a. 收集所有待办：
     - 已就绪的验收（按开始时间=test_end+1wd）
     - 未开始的需求阶段（按计划开始时间）
  b. 按"开始时间"排序（最早的先做）
  c. 开始时间相同 → 需求阶段优先
  d. 取第一个执行

步骤 ③ PM忙碌时：
  a. 不打断当前任务
  b. 等当前任务完成后，回到 ②
```

### 7.5 验收就绪时的两种场景

```
                  ┌─ 1.1 PM空闲
验收A就绪 ──┤
                  │
                  └─ 1.3 PM在做其他Task B的需求阶段
                        → 不打断Task B
                        → 等Task B需求阶段结束（B的技术评审）+ 1wd
                        → 验收A开始
```

#### 场景 1.1：PM空闲 🟢

| 条件 | 验收A就绪时，PM空闲 |
|:----:|:------------------:|
| 操作 | ① 验收A立刻开始<br>② 验收A结束 → PM空闲 → 回到PM Scheduler |
| 传播 | 验收A结束 ≥ B计划需求开始 → B需求阶段顺延 |

#### 场景 1.3：PM在做Task B需求阶段 🔴

| 条件 | 验收A就绪时，PM在做B的需求阶段(B≠A) |
|:----:|:----------------------------------:|
| 操作 | ① **不打断** B的需求阶段<br>② 等B需求阶段结束（B技术评审）<br>③ B技术评审 + 1wd → 验收A开始 |
| 传播 | 验收A结束 → PM空闲 → 回到PM Scheduler |

### 7.6 后续Task的链式依赖

```
Task B的需求阶段开始：
  = A的技术评审 + 1wd（PM最早空闲时间）
  （A的研发/测试不占PM时间）

Task C的需求阶段开始：
  = PM Scheduler 在验收A结束后按优先级规则决定
  通常：验收A结束、验收B还没就绪 → 开始reqC
```

### 7.7 传播效应

```
验收A占用PM时间 → Task B需求阶段后移
→ Task B技术评审后移（联动）
→ Task B研发开始后移（技术评审+1wd，公式不变）
→ Task B测试后移 → Task B验收后移
→ Task C需求阶段后移 → ... 以此类推
```

---

## 第八章：输入规则（v4新增）

### 8.1 首Task（Task A）

PM只给定：
- 完整需求周期（clarify_start/end, tech_review_start/end, design_start/end）
- 技术评审日期（tech_review_A）

**需求阶段开始时间计算**：
```
如果从tech_review_A倒推req_wd_A后 ≥ today:
  req_start_A = tech_review_A - req_wd_A（倒推）
否则:
  req_start_A = today → tech_review_A顺延
```

### 8.2 后续Task（Task B/C/...）

PM不给定任何日期，全部由引擎生成：
- 需求阶段开始 = PM Scheduler 决定
- 技术评审 = 需求阶段结束日（联动）
- 研发/测试/验收 = 按引擎原有公式

---

## 第九章：写回飞书

### 9.1 写回方式

引擎生成 `writeback_commands.sh` 脚本，每条使用：
```bash
lark-cli base +record-upsert --base-token {token} --table-id {tid} \
  --record-id {rid} --as user --json '{...}'
```

### 9.2 写回字段

| 飞书字段 | 来源 | 空值处理 |
|---------|------|:--------:|
| **澄清/评审/需求开始/结束(x6)** | 引擎排期结果 | `1900-01-01` |
| **技术评审** | 引擎排期结果 | `1900-01-01` |
| 研发开始/结束(x2) | 引擎计算结果 | `1900-01-01` |
| 测试开始/结束(x2) | 引擎计算结果 | `1900-01-01` |
| 验收开始/结束(x2) | 引擎计算结果 | `1900-01-01` |
| 投入研发 | 引擎分配槽位 | `未分配` |
| 投入测试 | 引擎分配槽位 | `未分配` |
| 标准研发人天 | 产研比计算值 | — |

### 9.3 排除任务清空

被过滤的任务 → 清空所有排期字段为：
- 日期：`1900-01-01`
- 槽位：`未分配`
- 人天：`0`

---

## 第十章：校验规则

### 10.1 写回后自动校验

| 校验项 | 预期 | 严重级别 |
|--------|------|:-------:|
| 阶段顺序 | 研发开始 ≤ 研发结束 < 测试开始 ≤ 测试结束 < 验收开始 ≤ 验收结束 | error |
| 评审锚点 | 研发开始 ≥ 技术评审 + 1 工作日 | warning |
| 研发槽位互斥 | 同槽位同日期无重叠 | error |
| 测试槽位互斥 | 同槽位同日期无重叠 | error |
| PM验收互斥 | 同一PM验收时间段不重叠 | error |
| PM设计冲突 | 验收不覆盖需求阶段 | warning |

### 10.2 新增校验项（v4）

| 校验项 | 预期 | 严重级别 |
|--------|------|:-------:|
| 需求阶段顺序 | 澄清≤澄清结束≤评审开始≤评审结束≤需求开始≤需求结束 | error |
| 需求→技术评审 | 技术评审 = 需求阶段结束日 | error |

---

## 第十一章：报告结构

### 11.1 概览统计卡片

| 统计项 | 说明 |
|--------|------|
| 有效任务 | 参与排期任务总数 |
| D/T/A 提前/延后 | 各阶段与旧排期对比 |
| 警告总数 | 规则警告总数 |
| 资源冲突 | 0 = 无冲突 |

### 11.2 模块任务表（可折叠）

按模块分组，每行包含（v4新增列已标注）：

| 列 | 来源 | 变化 |
|:--:|:----:|:----:|
| 任务名称 | Base读取 | 不变 |
| 评审(技术评审) | Base读取 | 不变 |
| **需求阶段** | **engine计算** | **v4新增** |
| **Δ需求** | **engine对比** | **v4新增** |
| 研发人天 | engine计算 | 不变 |
| 投入研发 | engine分配 | 不变 |
| 研发日期 | engine计算 | 不变 |
| Δ研发 | 与旧排期对比 | 不变 |
| 测试人天 | Base读取 | 不变 |
| 投入测试 | engine分配 | 不变 |
| 测试日期 | engine计算 | 不变 |
| Δ测试 | 与旧排期对比 | 不变 |
| 验收人天 | Base读取 | 不变 |
| 验收日期 | engine计算 | 不变 |
| Δ验收 | 与旧排期对比 | 不变 |
| 负责人 | Base读取 | 不变 |
| 备注 | engine生成 | 不变 |

### 11.3 负载明细

| 类型 | 内容 | 变化 |
|:----:|:----:|:----:|
| 研发负载 | Dev 1-6 + K1/K2 时间线 | 不变 |
| 测试负载 | Tester 1-4 时间线 | 不变 |
| PM验收分布 | 按PM分组的验收时间线 | 不变 |
| **PM需求阶段负载** | **按PM分组的需求阶段时间线** | **v4新增** |

### 11.4 Δ 显示规则

```
提前 >2天 → 绿色
延后 >2天 → 红色
±2天以内 → 灰色
```

---

## 第十二章：模块优先级（仅用于同槽竞争）

```
绩效(0) > 人事(1) > 考勤(2) > 薪酬(3) > 流程平台(4) > HRONE基础建设(5)
```

> 优先级**仅仲裁同槽位竞争**，空闲资源不囤积。
> 低优先级Task若其所需槽位空闲，立即分配。

---

## 第十三章：引擎参数

| 参数 | 默认 | 说明 |
|------|:----:|------|
| `--tasks` | 必填 | 飞书JSON或引擎格式JSON |
| `--holidays` | 必填 | 节假日JSON |
| `--raw-bitable` | false | 输入为飞书原始格式 |
| `--feishu-config` | 无 | feishu_config.json路径 |
| `--max-gap` | 5 | dev→test+test→accept软间隔约束 |
| `--today` | 当天 | 参考日期 |
| `--output` | schedule_report.html | 报告路径 |
| `--writeback` | false | 启用自动写回飞书 |
| `--skill-matrix` | 无 | 技能矩阵JSON（可选） |

---

## 第十四章：常见陷阱

| # | 问题 | 根因 | 解决 |
|:-:|:----|:----|:-----|
| P1 | 研发人天仍为旧值 | 产研比字段未更新 | 改表→重拉→重跑 |
| P2 | 旧槽位残留导致冲突检测不准 | 写回只更新日期未更新槽位 | v3已修复：写回同步更新槽位 |
| P3 | 空值清除静默失败 | API写""不生效 | 日期用`1900-01-01`，槽位用`未分配` |
| P4 | 任务名称匹配歧义 | 模糊匹配前缀 | 完全匹配→去空格→前8字→长子串 |
| P5 | 源表被重命名 | `+record-list`报not_found | `+block-list`查可用表ID |
| P6 | 无产研比任务残留槽位 | dev_wd=0仍占槽位 | dev_wd=0时清空dev_slots和日期 |
