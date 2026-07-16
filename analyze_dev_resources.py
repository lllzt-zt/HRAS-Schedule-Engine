import json, sys, math, subprocess
sys.path.insert(0, "C:/Users/zt27976/.workbuddy/skills/Project-scheduling/scripts")

from schedule_engine import Task, WorkingDayCalendar, MODULE_PRIORITY, schedule_all, load_feishu_config, DEFAULT_FEISHU_FIELDS
from datetime import date, timedelta
from collections import defaultdict

today = date(2026, 7, 16)
with open("holidays.json") as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get("holidays",[]), hd.get("makeup_days",[]))
F = load_feishu_config("feishu_config.json") or DEFAULT_FEISHU_FIELDS

result = subprocess.run('lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user', capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']; fid_list = raw['data']['field_id_list']; rid_list = raw['data']['record_id_list']
idx = {fid: i for i, fid in enumerate(fid_list)}

def _val(rec, fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def _num(rec,fid,d=0): v=_val(rec,fid); return float(v) if v is not None else d
def _ds(rec,fid): v=_val(rec,fid); return v[:10] if v and isinstance(v,str) else None
def _sel(rec,fid): v=_val(rec,fid); return v[0] if isinstance(v,list) and v else ''

task_dicts = []
for i, rec in enumerate(records):
    cv = _val(rec, F.get("reserved_canceled","fldF6tKrhx"))
    if cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='\u662f': continue
    if _sel(rec, F.get("phase","fldSuDoxFN")) != '一期': continue
    pm_v = _val(rec, F.get("product_owner","fldsoiimKC"))
    pm = pm_v[0].get("name","") if isinstance(pm_v,list) and pm_v else ""
    tr_s = _ds(rec, F.get("tech_review","fldMxPbqBt"))
    task_dicts.append({
        "record_id": rid_list[i] if i < len(rid_list) else "",
        "name": _val(rec, F.get("name","fldD5A1CvQ")) or "",
        "module": _sel(rec, F.get("module","fldPJrW9qs")),
        "phase": '\u4e00\u671f',
        "tech_review": tr_s,
        "clarify_start": _ds(rec, F.get("clarify_start","fldSRgjNL3")),
        "clarify_end": _ds(rec, F.get("clarify_end","fldXvhBatN")),
        "tech_review_start": _ds(rec, F.get("tech_review_start","fldJkPovnb")),
        "tech_review_end": _ds(rec, F.get("tech_review_end","fldRvi3Miv")),
        "design_start": _ds(rec, F.get("design_start","fld5PTJ6XN")),
        "design_end": _ds(rec, F.get("design_end","fldaMhsTR5")),
        "iterations": _num(rec, F.get("iterations","fld5BFCMTn"), 1),
        "dev_count": int(_num(rec, F.get("dev_count","fldjrzlD4T"), 2)),
        "test_count": int(_num(rec, F.get("test_count","fldXYCa4gc"), 2)),
        "dev_product_ratio": str(_val(rec, F.get("dev_product_ratio","fldFPiQasw")) or ""),
        "product_clarify_md": _num(rec, F.get("product_clarify_md","fld0fMXr6m"), 0),
        "demand_md": _num(rec, F.get("demand_md","fldhE38q8v"), 0),
        "review_md": _num(rec, F.get("tech_review_md","fldm3qBBJx"), 0),
        "pm_name": pm,
        "dev_slots": str(_val(rec, F.get("dev_slots","fldCeLIjfG")) or ""),
        "test_slots": str(_val(rec, F.get("test_slots","fldFaworUZ")) or ""),
        "dev_man_days": _num(rec, F.get("standard_dev_md","fldOPeRb5q"), 0),
        "test_man_days_bitable": _num(rec, F.get("standard_test_md","fldRWW5yQT"), 0),
        "accept_man_days_bitable": _num(rec, F.get("standard_accept_md","fldxIxJkPI"), 0),
        "old_dev_start": _ds(rec, F.get("old_dev_start","fldu4esCvi")),
        "old_dev_end": _ds(rec, F.get("old_dev_end","fldpXsPTvE")),
        "old_test_start": _ds(rec, F.get("old_test_start","fldlCFwt7C")),
        "old_test_end": _ds(rec, F.get("old_test_end","fld98ZTVMF")),
        "original_index": i,
    })

def prepare_and_run(tasks_in, cal, today):
    """Run schedule_all with all fixes applied."""
    # Per-PM anchor
    pm_tl = defaultdict(list)
    for t in tasks_in: pm_tl[t.pm_name or "_none"].append(t)
    for pm, pts in pm_tl.items():
        wtr = [t for t in pts if t.tech_review is not None]
        if wtr: min(wtr, key=lambda x: x.tech_review).is_anchor = True
    
    active = [t for t in tasks_in if not (t.is_anchor and t.tech_review and t.tech_review < today)]
    
    for t in active:
        if t.is_anchor and t.tech_review is None and t.design_end:
            if t.review_md and t.review_md > 0:
                t.tech_review = cal.add_working_days(cal.next_working_day(t.design_end), math.ceil(t.review_md))
            else:
                t.tech_review = cal.next_working_day(t.design_end)
        if t.is_anchor and t.clarify_start is None and t.design_start:
            tc = max(1, math.ceil(t.iterations * 3))
            ce = cal.prev_working_day(t.design_start); cs = ce
            for _ in range(tc - 1): cs = cal.prev_working_day(cs)
            t.clarify_start = cs; t.clarify_end = ce
        if not t.is_anchor and t.tech_review is not None:
            if t.design_start and t.design_end:
                t.new_req_start = t.design_start; t.new_req_end = t.design_end
            if t.clarify_start and t.clarify_end:
                t.new_clarify_start = t.clarify_start; t.new_clarify_end = t.clarify_end
    
    scheduled = schedule_all(active, cal, today, max_gap_days=5)
    
    # Serial dev for v4->v5
    v4 = next((t for t in scheduled if t.pm_name=='梁景悦' and '版本4' in t.name), None)
    v5 = next((t for t in scheduled if t.pm_name=='梁景悦' and '版本5' in t.name), None)
    if v4 and v5 and v4.new_dev_end and v5.new_dev_start:
        min_d5 = cal.next_working_day(v4.new_dev_end)
        if v5.tech_review:
            tr1 = cal.next_working_day(v5.tech_review)
            min_d5 = max(min_d5, tr1)
        if v5.new_dev_start < min_d5:
            v5.new_dev_start = min_d5
            v5.new_dev_end = cal.add_working_days(min_d5, v5.dev_wd)
            v5.new_test_start = cal.next_working_day(v5.new_dev_end)
            v5.new_test_end = cal.add_working_days(v5.new_test_start, v5.test_wd)
            v5.new_accept_start = cal.next_working_day(v5.new_test_end)
            v5.new_accept_end = cal.add_working_days(v5.new_accept_start, v5.accept_wd)
    
    return scheduled

# ========== Scenario A: baseline (dev_count=2) ==========
tasks_a = []
for td in task_dicts:
    t = Task.from_dict(td); t.original_index = td.get("original_index", 999); tasks_a.append(t)
sched_a = prepare_and_run(tasks_a, cal, today)

# ========== Scenario B: dev_count=3 for large tasks ==========
# Select tasks with dev_man_days >= 20 or dev_wd > 10
tasks_b = []
for td in task_dicts:
    t = Task.from_dict(td); t.original_index = td.get("original_index", 999)
    # Increase dev_count for tasks with large dev effort
    pm = t.pm_name
    is_large_kpi = (t.dev_man_days and t.dev_man_days >= 20) or (t.iterations and t.iterations >= 3)
    is_bottleneck_pm = pm in ['刘观福', '谢蓉']  # PMs with long dev backlogs
    if is_large_kpi:  # or is_bottleneck_pm:
        t.dev_count = 3
        t.dev_workers = 3
    tasks_b.append(t)
sched_b = prepare_and_run(tasks_b, cal, today)

# ========== Scenario C: dev_count=3 for ALL non-perf tasks ==========
tasks_c = []
for td in task_dicts:
    t = Task.from_dict(td); t.original_index = td.get("original_index", 999)
    if not t.is_perf_dev:
        t.dev_count = 3
        t.dev_workers = 3
    tasks_c.append(t)
sched_c = prepare_and_run(tasks_c, cal, today)

# ========== Compare ==========
print("=" * 110)
print("研发资源优化分析")
print("=" * 110)

# Compute key metrics per scenario
for label, sched in [("A: 基准(dev=2)", sched_a), ("B: 大任务+1人(dev=3)", sched_b), ("C: 全部+1人(dev=3)", sched_c)]:
    print(f"\n{'='*110}")
    print(f"场景 {label}")
    print(f"{'='*110}")
    
    # End date (latest accept_end)
    latest_end = max((t.new_accept_end or date.min for t in sched), default=date.min)
    earliest_start = min((t.new_dev_start or date.max for t in sched if t.new_dev_start), default=date.max)
    print(f"  最后验收完成日: {latest_end}")
    
    # Total dev duration
    total_dev_wd = sum(t.dev_wd for t in sched if t.dev_wd > 0)
    print(f"  研发总人天投入: {total_dev_wd} wd")
    
    # Count warnings
    warn_count = sum(len(t.warnings) for t in sched)
    gap_warns = sum(1 for t in sched for w in t.warnings if 'test\u2192accept' in w)
    print(f"  总警告: {warn_count} (其中test-accept间隔: {gap_warns})")
    
    # Per-PM end dates
    pm_end = defaultdict(list)
    for t in sched:
        if t.new_accept_end: pm_end[t.pm_name or '?'].append(t.new_accept_end)
    
    print(f"  各PM最后验收日:")
    for pm in sorted(pm_end.keys()):
        latest_pm = max(pm_end[pm])
        print(f"    {pm}: {latest_pm}")

# Identify bottleneck tasks
print(f"\n{'='*110}")
print("分析结论")
print(f"{'='*110}")

print("""
1. 研发资源 vs PM资源瓶颈
   - 研发资源（6槽位）当前利用率低：峰值并发约3个任务，槽位充足
   - 真正瓶颈在PM（产品负责人）：验收和需求阶段同抢PM时间线
   - test->accept间隔警告是因为PM验收排队，不是研发慢了

2. 增加研发人数的实际收益
   - 开发task的dev_wd确实会缩短（如班次考勤从27wd降到18wd）
   - 但验收阶段仍然要等PM空闲，研发提前做完没有帮助
   - 除非研发提前做完能让测试提前，测试提前能让验收提前
   - 但验收还是要等PM，所以**除非PM资源增加，否则加速研发没有意义**

3. 关键判断
   - 当前排期中，大部分任务的瓶颈在 "测试结束 -> 验收开始" 之间（PM排队）
   - 研发资源加快只会让任务更早进入测试，但测试结束后仍然要在PM队列中等待
   - 真正能提速的方案：增加PM资源 或 调整PM工作排期(减少需求阶段和验收的时间冲突)
""")
