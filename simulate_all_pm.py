import sys, json, math
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")

from schedule_engine import (
    Task, WorkingDayCalendar, ResourcePool, PhaseScheduler, Phase,
    MODULE_PRIORITY, schedule_all, load_feishu_config, DEFAULT_FEISHU_FIELDS
)
from datetime import date, timedelta
from collections import defaultdict
from heapq import heappush, heappop

today = date(2026, 7, 16)

# ========== Load Data ==========
with open("holidays.json") as f:
    hd = json.load(f)
cal = WorkingDayCalendar(hd.get("holidays",[]), hd.get("makeup_days",[]))

F = load_feishu_config("feishu_config.json")
if not F:
    F = DEFAULT_FEISHU_FIELDS

import subprocess
result = subprocess.run(
    "lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user",
    capture_output=True, text=True, timeout=30, shell=True
)
raw = json.loads(result.stdout)
records = raw['data']['data']
fid_list = raw['data']['field_id_list']
rid_list = raw['data']['record_id_list']
idx = {fid: i for i, fid in enumerate(fid_list)}

def field_index(fid):
    return idx.get(fid)

def _val(rec, fid):
    i = field_index(fid)
    if i is not None and i < len(rec):
        return rec[i]
    return None

def _num(rec, fid, default=0):
    v = _val(rec, fid)
    if v is None:
        return default
    try: return float(v)
    except: return default

def _date(rec, fid):
    v = _val(rec, fid)
    if v and isinstance(v, str):
        return v[:10]  # Return date string, not date object (from_dict expects string)
    return None

def _select(rec, fid):
    v = _val(rec, fid)
    if v and isinstance(v, list) and len(v) > 0:
        return v[0]
    return ''

# ========== Transform ==========
task_dicts = []
excluded = []

for i, rec in enumerate(records):
    record_id = rid_list[i] if i < len(rid_list) else ""
    
    completed_val = _val(rec, F.get("reserved_canceled", "fldF6tKrhx"))
    is_completed = (completed_val and isinstance(completed_val, list) and 
                    len(completed_val) > 0 and completed_val[0] == '\u662f')
    if is_completed:
        name = _val(rec, F.get("name", "fldD5A1CvQ")) or ""
        excluded.append(("已完成=是", name))
        continue
    
    phase = _select(rec, F.get("phase", "fldSuDoxFN"))
    if phase != '\u4e00\u671f':
        excluded.append(("非一期("+phase+")", str(_val(rec, F.get("name","fldD5A1CvQ")) or "")))
        continue
    
    name = _val(rec, F.get("name", "fldD5A1CvQ")) or ""
    module = _select(rec, F.get("module", "fldPJrW9qs"))
    pm_v = _val(rec, F.get("product_owner", "fldsoiimKC"))
    pm_name = pm_v[0].get("name","") if isinstance(pm_v, list) and pm_v else ""
    tr = _date(rec, F.get("tech_review", "fldMxPbqBt"))
    cs = _date(rec, F.get("clarify_start", "fldSRgjNL3"))
    ce = _date(rec, F.get("clarify_end", "fldXvhBatN"))
    rs = _date(rec, F.get("tech_review_start", "fldJkPovnb"))
    re = _date(rec, F.get("tech_review_end", "fldRvi3Miv"))
    ds = _date(rec, F.get("design_start", "fld5PTJ6XN"))
    de = _date(rec, F.get("design_end", "fldaMhsTR5"))
    
    task_dicts.append({
        "record_id": record_id,
        "name": name,
        "module": module,
        "phase": phase,
        "tech_review": tr,
        "clarify_start": cs, "clarify_end": ce,
        "tech_review_start": rs, "tech_review_end": re,
        "design_start": ds, "design_end": de,
        "iterations": _num(rec, F.get("iterations", "fld5BFCMTn"), 1),
        "dev_count": int(_num(rec, F.get("dev_count", "fldjrzlD4T"), 2)),
        "test_count": int(_num(rec, F.get("test_count", "fldXYCa4gc"), 2)),
        "dev_product_ratio": str(_val(rec, F.get("dev_product_ratio", "fldFPiQasw")) or ""),
        "dev_test_ratio": str(_val(rec, F.get("dev_test_ratio", "flduOhVcYR")) or ""),
        "product_clarify_md": _num(rec, F.get("product_clarify_md", "fld0fMXr6m"), 0),
        "demand_md": _num(rec, F.get("demand_md", "fldhE38q8v"), 0),
        "review_md": _num(rec, F.get("tech_review_md", "fldm3qBBJx"), 0),
        "pm_name": pm_name,
        "dev_slots": str(_val(rec, F.get("dev_slots", "fldCeLIjfG")) or ""),
        "test_slots": str(_val(rec, F.get("test_slots", "fldFaworUZ")) or ""),
        "dev_man_days": _num(rec, F.get("standard_dev_md", "fldOPeRb5q"), 0),
        "test_man_days_bitable": _num(rec, F.get("standard_test_md", "fldRWW5yQT"), 0),
        "accept_man_days_bitable": _num(rec, F.get("standard_accept_md", "fldxIxJkPI"), 0),
        "old_dev_start": _date(rec, F.get("old_dev_start", "fldu4esCvi")),
        "old_dev_end": _date(rec, F.get("old_dev_end", "fldpXsPTvE")),
        "old_test_start": _date(rec, F.get("old_test_start", "fldlCFwt7C")),
        "old_test_end": _date(rec, F.get("old_test_end", "fld98ZTVMF")),
        "old_accept_start": _date(rec, F.get("old_accept_start", "fldgv6n2rx")),
        "old_accept_end": _date(rec, F.get("old_accept_end", "flduVm6fRo")),
        "original_index": i,
    })

print(f"已排除: {len(excluded)} 个任务")
for reason, name in excluded:
    print(f"  [{reason}] {name}")
print()

# ========== Create Task objects ==========
tasks = []
for td in task_dicts:
    task = Task.from_dict(td)
    task.original_index = td.get("original_index", 999)  # not in from_dict
    tasks.append(task)

# ========== Boost: 班次考勤 dev=4人 test=3人 ==========
banci = next((t for t in tasks if '班次' in t.name and '考勤' in t.name), None)
if banci:
    old_dev = banci.dev_wd
    old_test = banci.test_wd
    banci.dev_count = 4
    banci.dev_workers = 4
    if banci.dev_man_days and banci.dev_man_days > 0:
        banci.dev_wd = max(1, math.ceil(banci.dev_man_days / banci.dev_workers))
    banci.test_count = 3
    banci.test_workers = 3
    if banci.test_man_days and banci.test_man_days > 0:
        banci.test_wd = max(1, math.ceil(banci.test_man_days / banci.test_workers))
    print(f"[加速] {banci.name[:40]:40s} dev:{old_dev}->{banci.dev_wd}wd(4人) test:{old_test}->{banci.test_wd}wd(3人)")

# ========== Fix: 绩效任务使用A/B(101/102)标识，非绩效用1-6 ==========
for t in tasks:
    if t.pm_name == '梁景悦' and t.module == '绩效':
        t.dev_slots = "独立团队"  # 标记为绩效独立团队，使用101/102
        t.is_perf_dev = True
        if t.dev_workers != 2:
            t.dev_workers = 2
        print(f"[绩效资源] {t.name[:35]:35s} -> 使用绩效资源A/B")
    else:
        # 清除非绩效任务的旧槽位值，让引擎重新分配
        if t.dev_slots and t.dev_slots not in ("", "None", "未分配"):
            t.dev_slots = ""
        t.is_perf_dev = False

# ========== Per-PM anchor ==========
pm_tasks_list = defaultdict(list)
for t in tasks:
    pm_tasks_list[t.pm_name or "_none"].append(t)

for pm, pts in pm_tasks_list.items():
    with_tr = [t for t in pts if t.tech_review is not None]
    if with_tr:
        earliest = min(with_tr, key=lambda t: t.tech_review)
        earliest.is_anchor = True
        print(f"PM={pm}: 首Task={earliest.name[:30]} tech_review={earliest.tech_review}")
    else:
        with_dates = [t for t in pts if t.clarify_end is not None]
        if with_dates:
            earliest = min(with_dates, key=lambda t: t.clarify_end)
            earliest.is_anchor = True
            print(f"PM={pm}: 首Task(无评审有需求期)={earliest.name[:30]} 澄清结束={earliest.clarify_end}")
        else:
            print(f"PM={pm}: 无首Task，全部由引擎生成")

# ========== 模块优先级验证 ==========
print(f"\n{'='*60}")
print("模块优先级验证")
print('=' * 60)
module_issues = []
for t in tasks:
    if not t.module or t.module == "":
        module_issues.append(f"{t.name[:40]:40s} 模块为空")
    elif t.module not in MODULE_PRIORITY:
        module_issues.append(f"{t.name[:40]:40s} 模块=\"{t.module}\" 未定义优先级")

if module_issues:
    print("⚠️ 以下任务存在模块优先级问题:")
    for issue in module_issues:
        print(f"  {issue}")
    print("❌ 模块优先级不完整，无法继续排期")
    print("  请在 MODULE_PRIORITY 中添加缺失的模块定义后重试")
    raise SystemExit("模块优先级未解决，排期终止")
else:
    print("✅ 所有任务模块优先级正常")
    for pm, pts in sorted(pm_tasks_list.items()):
        if pm == "_none": continue
        # 按模块优先级排序该PM的后续Task
        subs = [(t.module, MODULE_PRIORITY.get(t.module, 99), t.name) for t in pts if not t.is_anchor]
        subs.sort(key=lambda x: x[1])
        if subs:
            order_str = " → ".join(f"{m}({p})" for m, p, _ in subs)
            print(f"  {pm}: 后续Task排序 {order_str}")

# ========== 首Task确认 ==========
print(f"\n{'='*60}")
print("首Task确认 — 请确认以下每个PM的首Task是否正确")
print('=' * 60)
for pm, pts in sorted(pm_tasks_list.items()):
    if pm == "_none": continue
    anchor = next((t for t in pts if t.is_anchor), None)
    if anchor:
        tr_str = str(anchor.tech_review) if anchor.tech_review else "无(从需求阶段推导)"
        print(f"  {pm}: [{anchor.name[:40]:40s}] 技术评审={tr_str}")
    else:
        print(f"  {pm}: 无首Task（全部由引擎生成）")

print()
# Check if user passed --confirmed flag to skip interactive prompt
confirmed = '--confirmed' in sys.argv
compare_mode = '--compare' in sys.argv
if not confirmed:
    print("运行方式: python simulate_all_pm.py --confirmed [--compare]")
    print("  --confirmed  确认首Task无误后执行完整排期")
    print("  --compare    生成带新旧对比(提前/延后)的变更版报告")
    print("请在确认首Task无误后，添加 --confirmed 参数运行")
    raise SystemExit("请先运行 discovery.py 确认首Task，再添加 --confirmed 参数执行排期")
print("✅ 首Task已确认，开始排期")

active = []
for t in tasks:
    if t.is_anchor and t.tech_review and t.tech_review < today:
        print(f"  排除历史首Task: {t.name[:30]}")
        continue
    active.append(t)

# v4: For anchor tasks without tech_review, derive it from req phase dates
for t in active:
    if t.is_anchor and t.tech_review is None and t.design_end:
        if t.review_md and t.review_md > 0:
            # 推算业务评审起止（不含在Base中，从需求结束+1wd推算）
            t.tech_review_start = cal.next_working_day(t.design_end)
            t.tech_review_end = cal.add_working_days(t.tech_review_start, math.ceil(t.review_md))
            t.tech_review = cal.next_working_day(t.tech_review_end)
            print(f"  首Task无tech_review, 推算业务评审 {t.tech_review_start}~{t.tech_review_end} 技术评审={t.tech_review}")
        else:
            t.tech_review = cal.next_working_day(t.design_end)
            print(f"  首Task无tech_review, 从需求结束推导: {t.name[:30]} tech_review={t.tech_review}")

# Fix 3: Anchor clarify backward calc (from design_start)
for t in active:
    if t.is_anchor and t.clarify_start is None and t.design_start:
        total_cal = max(1, math.ceil(t.iterations * 3))
        non_pm = max(1, math.ceil(total_cal / 3))
        ce = cal.prev_working_day(t.design_start)
        cs = ce
        for _ in range(total_cal - 1):
            cs = cal.prev_working_day(cs)
        t.clarify_start = cs
        t.clarify_end = ce
        print(f"  [clarify推导] {t.name[:30]} 澄清 {cs}~{ce} (总{total_cal}wd)")

# ========== Fix: 确保版本4是梁景悦的锚点，版本5不是 ==========
# 1900-01-01被过滤后，版本5的tech_review可能为None而版本4有7/17，
# 但后续处理可能错乱。显式设置确保正确：
v4_anchor = next((t for t in active if t.pm_name == '梁景悦' and '版本4' in t.name), None)
v5_not = next((t for t in active if t.pm_name == '梁景悦' and '版本5' in t.name), None)
# 重置梁景悦的所有绩效任务的is_anchor
for t in active:
    if t.pm_name == '梁景悦' and t.module == '绩效':
        t.is_anchor = False
# 设版本4为首Task
if v4_anchor and v4_anchor.tech_review:
    v4_anchor.is_anchor = True
    print(f"[锚点] 版本4({v4_anchor.tech_review})是梁景悦的首Task")

# Fix 4: PM Scheduler skip non-anchor tasks with existing tech_review
# NOTE: 不保留任何后续Task的Base tech_review，全部由PM Scheduler按模块优先级重新生成
for t in active:
    if not t.is_anchor and t.tech_review is not None:
        t.tech_review = None  # 清除Base值，让PM Scheduler按优先级重新生成
        t.new_clarify_start = None
        t.new_clarify_end = None
        t.new_req_start = None
        t.new_req_end = None
        t.new_review_start = None
        t.new_review_end = None
        print(f"  [清除旧值] {t.name[:30]} 旧tech_review已清除，由PM Scheduler重新生成")

# ========== 特殊逻辑: 版本5需求与版本4一致，研发按4→5→AI Agent串行 ==========
v4 = next((t for t in active if t.pm_name == '梁景悦' and '版本4' in t.name), None)
v5 = next((t for t in active if t.pm_name == '梁景悦' and '版本5' in t.name), None)
ai_agent_ver = next((t for t in active if t.pm_name == '梁景悦' and 'AI Agent' in t.name), None)

if v4 and v5:
    # 版本5的需求阶段日期与版本4完全一致
    v5.new_clarify_start = v4.new_clarify_start or v4.clarify_start
    v5.new_clarify_end = v4.new_clarify_end or v4.clarify_end
    v5.new_req_start = v4.new_req_start or v4.design_start
    v5.new_req_end = v4.new_req_end or v4.design_end
    v5.new_review_start = v4.new_review_start or v4.tech_review_start
    v5.new_review_end = v4.new_review_end or v4.tech_review_end
    v5.tech_review = v4.tech_review or date(2026, 7, 17)
    # 版本5的PM工时=0（需求已与版本4一起完成）
    v5.product_clarify_md = 0
    v5.demand_md = 0
    v5.review_md = 0
    v5._calc_req_wd()  # 重新计算req_wd=0
    
    # 研发顺序：版本4 → 版本5，串行逻辑在post-process中处理
    print(f"[特殊] 版本5的需求阶段与版本4一致: {v5.new_clarify_start}~{v5.new_review_end}")
    print(f"[特殊] 版本5研发工时={v5.dev_wd}wd, (与版本4→版本5→AI Agent串行)")

print(f"\n参与排期: {len(active)} 个任务\n")

# ========== Run schedule_all ==========
scheduled = schedule_all(active, cal, today, max_gap_days=5)

# ========== Post-process: 绩效研发串行（版本4→版本5→AI Agent） ==========
def _shift_dev_chain(t_prev, t_next, cal):
    """Shift t_next's dev/test/accept to start after t_prev's dev ends."""
    if not t_prev.new_dev_end or not t_next.new_dev_start:
        return False
    expected_start = cal.next_working_day(t_prev.new_dev_end)
    if t_next.new_dev_start < expected_start:
        print(f"[serial] {t_prev.name[:30]} dev_end={t_prev.new_dev_end} -> {t_next.name[:30]} dev_start={t_next.new_dev_start}->{expected_start}")
        t_next.new_dev_start = expected_start
        t_next.new_dev_end = cal.add_working_days(expected_start, t_next.dev_wd)
        t_next.new_test_start = cal.next_working_day(t_next.new_dev_end)
        t_next.new_test_end = cal.add_working_days(t_next.new_test_start, t_next.test_wd)
        t_next.new_accept_start = cal.next_working_day(t_next.new_test_end)
        t_next.new_accept_end = cal.add_working_days(t_next.new_accept_start, t_next.accept_wd)
        return True
    return False

# Find 绩效 tasks in scheduled
liang_tasks = [t for t in scheduled if t.pm_name == '梁景悦' and t.module == '绩效']
# Order: 版本4 → 版本5 → AI Agent (by original index)
v4 = next((t for t in liang_tasks if '版本4' in t.name), None)
v5 = next((t for t in liang_tasks if '版本5' in t.name), None)
ai_agent_ver = next((t for t in liang_tasks if 'AI Agent' in t.name), None)

if v4 and v5:
    _shift_dev_chain(v4, v5, cal)
if v5 and ai_agent_ver:
    _shift_dev_chain(v5, ai_agent_ver, cal)

# ========== Print results ==========
print()
header = f"{'PM':<8} {'模块':<8} {'角色':<6} {'任务':<32}  {'技术评审':<12} {'需求阶段(澄清~需求~评审)':<36} {'研发':<22} {'测试':<22} {'验收':<22}"
sep = "=" * min(170, len(header))
print(sep)
print(header)
print(sep)

pm_order = ["刘观福","肖维","许大庆","谢蓉","梁景悦","谭雨薇"]
for pm in pm_order:
    pm_t = [t for t in scheduled if t.pm_name == pm]
    pm_t.sort(key=lambda t: (0 if t.is_anchor else 1, 
                             MODULE_PRIORITY.get(t.module, 99),
                             t.original_index))
    
    print(f"\n{pm} ({len(pm_t)}个):")
    for t in pm_t:
        role = "首" if t.is_anchor else "后续"
        
        def fd(d):
            return str(d) if d else "-"
        
        cs = t.new_clarify_start or t.clarify_start
        ce = t.new_clarify_end or t.clarify_end
        req_s = t.new_req_start or t.design_start
        req_e = t.new_req_end or t.design_end
        rvs = t.new_review_start or t.tech_review_start
        rve = t.new_review_end or t.tech_review_end
        
        # 需求阶段 = 澄清开始 ~ 业务评审结束（三段合一）
        demand_phase_start = cs or req_s or rvs
        demand_phase_end = rve or req_e or ce  # 到业务评审结束
        demand_phase_str = f"{fd(demand_phase_start)}~{fd(demand_phase_end)}" if demand_phase_start and demand_phase_end else "-"
        
        tr_str = fd(t.tech_review)
        
        dev_str = f"{fd(t.new_dev_start)}~{fd(t.new_dev_end)}"
        test_str = f"{fd(t.new_test_start)}~{fd(t.new_test_end)}"
        accept_str = f"{fd(t.new_accept_start)}~{fd(t.new_accept_end)}"
        tr_str = fd(t.tech_review)
        
        print(f"  {role:<6} {t.module[:8]:<8} {t.name[:32]:<32} {tr_str:<12} {demand_phase_str:<36} {dev_str:<22} {test_str:<22} {accept_str:<22}")
    
    for t in pm_t:
        for w in t.warnings:
            print(f"  ! {t.name[:30]}: {w}")

# ========== Generate HTML report (sorted by tech_review) ==========
from schedule_engine import generate_html
scheduled_sorted = sorted(scheduled, key=lambda t: (
    t.tech_review.toordinal() if t.tech_review else 999999,
    t.pm_name or '',
    t.name
))
generate_html(scheduled_sorted, cal, today, "schedule_report.html", max_gap_days=5, show_comparison=compare_mode)
if compare_mode:
    print(f"\nHTML报告(对比版)已生成: schedule_report.html")
else:
    print(f"\nHTML报告(初版)已生成: schedule_report.html")

# ========== Slot label conversion: 101->A, 102->B (before writeback & util section) ==========
for t in scheduled:
    if t.dev_slots:
        t.dev_slots = t.dev_slots.replace("101", "A").replace("102", "B")
    # 绩效任务: "独立团队" → "A,B"（写回飞书也用A/B）
    if t.is_perf_dev and t.dev_slots == "独立团队":
        t.dev_slots = "A,B"

# Post-process HTML to show A/B in slot display
with open("schedule_report.html", "r", encoding="utf-8") as f:
    html_content = f.read()
# Only replace slot-related 101/102 (not dates)
html_content = html_content.replace("101,102", "A,B").replace("101<", "A<")
html_content = html_content.replace(">101", ">A").replace(",101", ",A")
html_content = html_content.replace(">102", ">B").replace(",102", ",B").replace("102<", "B<")
# 绩效投入研发: 独立团队 → A,B
html_content = html_content.replace("独立团队", "A,B")
with open("schedule_report.html", "w", encoding="utf-8") as f:
    f.write(html_content)

# ========== Append Resource Utilization Section ==========
print("生成人员负载分析板块...")

# Compute timespan
all_starts = []
all_ends = []
for t in scheduled:
    cs = t.new_clarify_start or t.clarify_start
    ds = t.new_req_start or t.design_start
    for d in [cs, ds, t.new_dev_start, t.new_test_start, t.new_accept_start]:
        if d: all_starts.append(d)
    ce = t.new_clarify_end or t.clarify_end
    de = t.new_req_end or t.design_end
    rve = t.new_review_end or t.tech_review_end
    for d in [ce, de, rve, t.new_dev_end, t.new_test_end, t.new_accept_end]:
        if d: all_ends.append(d)

period_start = min(all_starts) if all_starts else today
period_end = max(all_ends) if all_ends else today
total_wd = cal.working_days_between(period_start, period_end) + 1

# PM workload: req phase + acceptance
pm_data = defaultdict(lambda: {"busy_days": set(), "name": ""})
for t in scheduled:
    pm = t.pm_name or "未分配"
    pm_data[pm]["name"] = pm
    # Req phase (clarify_start ~ review_end)
    rs = t.new_clarify_start or t.clarify_start
    re = t.new_review_end or t.tech_review_end or t.new_req_end or t.design_end
    if rs and re:
        d = rs
        while d <= re:
            if cal.is_working_day(d):
                pm_data[pm]["busy_days"].add(d)
            d += timedelta(days=1)
    # Acceptance
    if t.new_accept_start and t.new_accept_end:
        d = t.new_accept_start
        while d <= t.new_accept_end:
            if cal.is_working_day(d):
                pm_data[pm]["busy_days"].add(d)
            d += timedelta(days=1)

# Dev slot workload
dev_slots = {s: {"busy_days": set(), "label": f"研发槽位{s}"} for s in range(1, 7)}
dev_slots[101] = {"busy_days": set(), "label": "研发资源A"}
dev_slots[102] = {"busy_days": set(), "label": "研发资源B"}

# Test slot workload
test_slots = {s: {"busy_days": set(), "label": f"测试槽位{s}"} for s in range(1, 5)}

for t in scheduled:
    # Dev slots
    if t.new_dev_start and t.new_dev_end:
        ds_str = t.dev_slots
        if ds_str and ds_str not in ("未分配", "None", ""):
            try:
                raw_slots = str(ds_str).split(",")
                slots = []
                for s in raw_slots:
                    s = s.strip()
                    if s == 'A': slots.append(101)
                    elif s == 'B': slots.append(102)
                    else: slots.append(int(s))
                d = t.new_dev_start
                while d <= t.new_dev_end:
                    if cal.is_working_day(d):
                        for s in slots:
                            if s in dev_slots:
                                dev_slots[s]["busy_days"].add(d)
                    d += timedelta(days=1)
            except: pass
    
    # Test slots
    if t.new_test_start and t.new_test_end:
        ts_str = t.test_slots
        if ts_str and ts_str not in ("未分配", "None", ""):
            try:
                raw_ts = str(ts_str).split(",")
                slots = []
                for s in raw_ts:
                    s = s.strip()
                    if s == 'A': slots.append(101)
                    elif s == 'B': slots.append(102)
                    else: slots.append(int(s))
                d = t.new_test_start
                while d <= t.new_test_end:
                    if cal.is_working_day(d):
                        for s in slots:
                            if s in test_slots:
                                test_slots[s]["busy_days"].add(d)
                    d += timedelta(days=1)
            except: pass

def pct(a, b):
    if b <= 0: return 0
    return round(a / b * 100, 1)

# Generate HTML section
roles_html = {
    "pm": '<optgroup label="产品负责人(PM)">',
    "dev": '<optgroup label="研发槽位">',
    "test": '<optgroup label="测试槽位">',
}
role_options = {"pm": "", "dev": "", "test": ""}

items = []

# PM items
for pm, data in sorted(pm_data.items()):
    busy = len(data["busy_days"])
    idle = total_wd - busy
    items.append(("pm", data["name"], busy, idle, pct(busy, total_wd)))
    role_options["pm"] += f'<option value="pm-{pm}">PM {pm}</option>'
roles_html["pm"] += '</optgroup>'

# Dev items
for sid in sorted(dev_slots.keys()):
    busy = len(dev_slots[sid]["busy_days"])
    idle = total_wd - busy
    label = dev_slots[sid]["label"]
    items.append(("dev", label, busy, idle, pct(busy, total_wd)))
    role_options["dev"] += f'<option value="dev-{sid}">{label}</option>'
roles_html["dev"] = roles_html["dev"].replace('<optgroup', '</optgroup><optgroup')

# Test items
for sid in sorted(test_slots.keys()):
    busy = len(test_slots[sid]["busy_days"])
    idle = total_wd - busy
    label = test_slots[sid]["label"]
    items.append(("test", label, busy, idle, pct(busy, total_wd)))
    role_options["test"] += f'<option value="test-{sid}">{label}</option>'

all_options = '<option value="all">全部人员</option>'
all_options += '<optgroup label="研发槽位">' + role_options["dev"] + '</optgroup>'
all_options += '<optgroup label="测试槽位">' + role_options["test"] + '</optgroup>'

util_css = '''
<style>
.util-section{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.util-section h2{font-size:18px;margin:0 0 12px 0;color:#2d3436}
.util-section .period-info{font-size:13px;color:#636e72;margin:0 0 12px 0}
.util-filters{display:flex;gap:16px;margin-bottom:12px;flex-wrap:wrap}
.util-filters label{font-size:13px;color:#636e72;display:flex;align-items:center;gap:6px}
.util-filters select{padding:4px 8px;border:1px solid #dfe6e9;border-radius:6px;font-size:13px;color:#2d3436;background:#fff;outline:none;cursor:pointer}
.util-filters select:focus{border-color:#0984e3}
.util-table{width:100%;border-collapse:collapse;font-size:13px}
.util-table th{background:#f8f9fa;padding:10px 12px;text-align:left;font-size:12px;color:#636e72;font-weight:600;border-bottom:2px solid #dfe6e9;white-space:nowrap}
.util-table td{padding:10px 12px;border-bottom:1px solid #f0f0f0;font-size:13px}
.util-table .util-row:hover{background:#f8f9ff}
.util-table .role-badge{display:inline-block;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;color:#fff}
.role-dev{background:#00b894}
.role-test{background:#0984e3}
.util-table .num-cell{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.util-table .idle-cell{text-align:right;color:#b2bec3}
.util-table .pct-cell{text-align:right;font-weight:600}
.util-bar{display:inline-flex;height:16px;border-radius:3px;overflow:hidden;width:160px;vertical-align:middle}
.util-bar .busy-bar{min-width:3px}
.util-bar .idle-bar{background:#ecf0f1;min-width:3px}
</style>
'''

util_html = f'''
<div class="util-section">
<h2>人员负载分析</h2>
<p class="period-info">统计周期: {period_start} ~ {period_end} · 共{total_wd}个工作日 · 投入/空闲统计</p>
<div class="util-filters">
  <label>角色筛选:
    <select id="roleFilter" onchange="filterUtil()">
      <option value="all">全部角色</option>
      <option value="dev">研发</option>
      <option value="test">测试</option>
    </select>
  </label>
  <label>人员筛选:
    <select id="personFilter" onchange="filterUtil()">
      {all_options}
    </select>
  </label>
</div>
<table class="util-table">
<thead><tr>
  <th>角色</th><th>人员/资源</th><th style="text-align:right">投入(天)</th><th style="text-align:right">空闲(天)</th><th style="text-align:right">利用率</th><th>投入/空闲分布</th>
</tr></thead>
<tbody>
'''

colors = {"dev": {"bg": "#00b894", "text": "#00b894"}, "test": {"bg": "#0984e3", "text": "#0984e3"}}
for role, name, busy, idle, ratio in items:
    if role == 'pm':
        continue
    bar_w = max(4, round(busy / max(1, total_wd) * 196))
    idle_w = max(4, 196 - bar_w)
    c = colors.get(role, {"bg": "#636e72", "text": "#636e72"})
    bar = f'<span class="util-bar"><span class="busy-bar" style="background:{c["bg"]};width:{bar_w}px"></span><span class="idle-bar" style="width:{idle_w}px"></span></span>'
    
    role_cn = {"dev": "研发", "test": "测试"}.get(role, role)
    badge = f'<span class="role-badge role-{role}">{role_cn}</span>'
    util_html += f'<tr class="util-row" data-role="{role}" data-person="{name}">'
    util_html += f'<td>{badge}</td><td>{name}</td>'
    util_html += f'<td class="num-cell">{busy}</td>'
    util_html += f'<td class="idle-cell">{idle}</td>'
    util_html += f'<td class="pct-cell" style="color:{c["text"]}">{ratio}%</td>'
    util_html += f'<td>{bar}</td></tr>'

util_html += '''
</tbody></table>
</div>
<script>
function filterUtil() {
  var role = document.getElementById('roleFilter').value;
  var person = document.getElementById('personFilter').value;
  var rows = document.querySelectorAll('.util-row');
  rows.forEach(function(r) {
    var rRole = r.getAttribute('data-role');
    var rPerson = r.getAttribute('data-person');
    var roleMatch = (role === 'all' || rRole === role);
    var personMatch = (person === 'all');
    if (!personMatch) {
      var p = person.split('-');
      if (p.length === 2) {
        personMatch = (p[0] === rRole && p[1] === rPerson);
      }
    }
    r.style.display = (roleMatch && personMatch) ? '' : 'none';
  });
}
</script>
'''

# Append util section: extract Generated by, insert util, then put Generated by after
with open("schedule_report.html", "r", encoding="utf-8") as f:
    content = f.read()
# Remove any content after last </html> first (stray content from previous runs)
last_html = content.rfind("</html>")
if last_html > 0:
    content = content[:last_html + 7]
# Extract Generated by paragraph
gen_start = content.find('<p style="text-align:center;padding:20px;color:#636e72;font-size:12px">')
gen_end = content.find("</p>", gen_start) + 4 if gen_start > 0 else -1
if gen_start > 0 and gen_end > gen_start:
    generated_by = content[gen_start:gen_end]
    content = content[:gen_start] + content[gen_end:]
else:
    generated_by = '<p style="text-align:center;padding:20px;color:#636e72;font-size:12px">Generated by HRAS Schedule Engine v3 · WorkBuddy</p>'
# Replace last </body></html> with util section + Generated by + closing tags
content = content.replace("</body></html>", util_css + util_html + "\n" + generated_by + "\n</body></html>", 1)
with open("schedule_report.html", "w", encoding="utf-8") as f:
    f.write(content)
print(f"人员负载分析板块已追加到报告")

base_token = "BKBKbUWXtas7tSshzoccyZa3ndb"
table_id = "tblGyhmFz9YQH2Z1"

def _fmt_date(d):
    if d is None: return "1900-01-01"
    if isinstance(d, date): return d.isoformat()
    return str(d)[:10]

def _fmt_slot(slot_str):
    if not slot_str or slot_str == "None" or slot_str == "未分配": return "未分配"
    return slot_str

def _build_patch(task):
    patch = {}
    # Req phase date fields
    req_pairs = [("new_clarify_start","clarify_start"),("new_clarify_end","clarify_end"),
                 ("new_review_start","tech_review_start"),("new_review_end","tech_review_end"),
                 ("new_req_start","design_start"),("new_req_end","design_end")]
    for nk, ck in req_pairs:
        fid = F.get(ck)
        if fid: patch[fid] = _fmt_date(getattr(task, nk, None))
    # Tech review
    tr_fid = F.get("tech_review")
    if tr_fid: patch[tr_fid] = _fmt_date(task.tech_review)
    # Dev/test/accept dates
    for nk, ok in [("new_dev_start","old_dev_start"),("new_dev_end","old_dev_end"),
                   ("new_test_start","old_test_start"),("new_test_end","old_test_end"),
                   ("new_accept_start","old_accept_start"),("new_accept_end","old_accept_end")]:
        fid = F.get(ok)
        if fid: patch[fid] = _fmt_date(getattr(task, nk, None))
    # Slots
    ds_fid = F.get("dev_slots")
    if ds_fid: patch[ds_fid] = _fmt_slot(task.dev_slots)
    ts_fid = F.get("test_slots")
    if ts_fid: patch[ts_fid] = _fmt_slot(task.test_slots)
    # Dev man-days
    dm_fid = F.get("standard_dev_md")
    if dm_fid and task.dev_man_days is not None:
        patch[dm_fid] = round(task.dev_man_days, 1)
    # Test man-days (calculated from 产测比)
    tm_fid = F.get("standard_test_md")
    if tm_fid and task.test_man_days is not None:
        patch[tm_fid] = round(task.test_man_days, 1)
    # Acceptance man-days
    am_fid = F.get("standard_accept_md")
    if am_fid and task.accept_man_days is not None:
        patch[am_fid] = round(task.accept_man_days, 1)
    return patch

writeback_cmds = []
for t in scheduled:
    if not t.record_id: continue
    patch = _build_patch(t)
    if not patch: continue
    json_str = json.dumps(patch, ensure_ascii=False)
    cmd = (f'lark-cli base +record-upsert '
           f'--base-token {base_token} '
           f'--table-id {table_id} '
           f'--record-id {t.record_id} '
           f'--as user '
           f'--json \'{json_str}\'')
    writeback_cmds.append(cmd)

with open("writeback_commands.sh", "w", encoding="utf-8") as f:
    f.write("#!/bin/bash\n")
    for cmd in writeback_cmds:
        f.write(cmd + "\n")
    f.write("echo 'Writeback complete'\n")

print(f"写回命令已生成: writeback_commands.sh ({len(writeback_cmds)}条记录)")
print()

# ========== Execute writeback ==========
import subprocess, os
print(f"\n执行写回脚本...")
r = subprocess.run("bash writeback_commands.sh", capture_output=True, text=True, timeout=120, shell=True)
print(r.stdout[:2000])
if r.stderr:
    print(f"STDERR: {r.stderr[:500]}")
# Check how many succeeded
success = r.stdout.count('"updated": true')
print(f"\n写回完成: {success}/{len(writeback_cmds)}条成功")
print("模拟完成")
print(sep)
