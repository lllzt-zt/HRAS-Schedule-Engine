# Project Scheduling - HRAS 项目排期引擎 v3

时间驱动跨模块并行排期引擎，为 HRAS 敏捷项目自动生成最优排期。

## 核心能力

- **时间驱动模拟**：逐工作日推进，同时为所有就绪任务分配资源
- **跨模块并行**：优先级仅仲裁同槽位竞争，空闲资源不囤积
- **双软间隔**：dev→test 和 test→accept 均设 max-gap 约束，超时生成警告
- **绩效资源共享**：绩效空闲时 101-102 对普通任务开放，入队后立即回收
- **内置数据管道**：`--raw-bitable` 直接接受飞书 JSON，自动转换

## 快速开始

```bash
# 1. 拉取飞书数据
lark-cli base +record-list \
  --base-token BKBKbUWXtas7tSshzoccyZa3ndb \
  --table-id tbl8a4OME0aXJpb1 \
  --field-id fldD5A1CvQ --field-id fldMxPbqBt --field-id fld5BFCMTn \
  ... (完整字段列表见 SKILL.md) \
  --limit 200 --format json --as bot > tasks.json

# 2. 运行引擎
python scripts/schedule_engine.py \
  --tasks tasks.json --holidays holidays.json \
  --raw-bitable --max-gap 5 --today 2026-07-13 \
  --output schedule_report.html

# 3. 写回飞书
lark-cli base +record-upsert --base-token <token> --table-id <id> \
  --record-id <rid> --json '<fields>' --as bot
```

## 文件结构

```
├── SKILL.md              # WorkBuddy Skill 定义（完整工作流、约束规则、排查手册）
├── schedule_logic.md     # 排期逻辑详细说明
├── scripts/
│   └── schedule_engine.py # Python 引擎（57KB，时间驱动模拟）
└── references/
    └── feishu_fields.md   # 飞书字段映射
```

## 调度规则

### 优先级
```
绩效 > 人事 > 考勤 > 薪酬 > 流程平台 > HRONE基础建设
```

### 资源池
| 池 | 槽位 | 说明 |
|---|------|------|
| 普通研发 | 1-6 | 严格按分配，不跨池 |
| 绩效研发 | 101,102 | 空闲共享给普通任务 |
| 共享测试 | 1-6 | 含绩效兜底分配 |

### 约束条件
1. PM 验收不与设计期或验收重叠（P0 硬约束）
2. 同槽位不可同日多任务
3. dev→test / test→accept 软间隔 max-gap=5wd
4. 绩效无测试槽位时自动使用共享池
5. 需求评审 < 当天的历史任务排除
