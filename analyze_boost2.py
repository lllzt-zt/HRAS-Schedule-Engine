import sys, json, math, subprocess
sys.path.insert(0, 'D:/WorkBuddyPlace/Project-scheduling')
from schedule_engine import Task, WorkingDayCalendar, MODULE_PRIORITY, schedule_all, load_feishu_config, DEFAULT_FEISHU_FIELDS
from datetime import date, timedelta
from collections import defaultdict

today = date(2026, 7, 16)
with open('holidays.json') as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get('holidays',[]), hd.get('makeup_days',[]))
F = load_feishu_config('feishu_config.json') or DEFAULT_FEISHU_FIELDS

result = subprocess.run('lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user', capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']; fid_list = raw['data']['field_id_list']; rid_list = raw['data']['record_id_list']
idx = {fid: i for i, fid in enumerate(fid_list)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

task_dicts = []
for i, rec in enumerate(records):
    cv = v(rec, F.get('reserved_canceled','fldF6tKrhx'))
    if cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='\u662f': continue
    if s(rec, F.get('phase','fldSuDoxFN')) != '\u4e00\u671f': continue
    pm_v = v(rec, F.get('product_owner','fldsoiimKC'))
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    tr_s = d(rec, F.get('tech_review','fldMxPbqBt'))
    task_dicts.append({
        'record_id': rid_list[i] if i<len(rid_list) else '',
        'name': v(rec,F.get('name','fldD5A1CvQ')) or '',
        'module': s(rec,F.get('module','fldPJrW9qs')),
        'phase': '\u4e00\u671f', 'tech_review': tr_s,
        'clarify_start': d(rec,F.get('clarify_start','fldSRgjNL3')),
        'clarify_end': d(rec,F.get('clarify_end','fldXvhBatN')),
        'tech_review_start': d(rec,F.get('tech_review_start','fldJkPovnb')),
        'tech_review_end': d(rec,F.get('tech_review_end','fldRvi3Miv')),
        'design_start': d(rec,F.get('design_start','fld5PTJ6XN')),
        'design_end': d(rec,F.get('design_end','fldaMhsTR5')),
        'iterations': float(v(rec,F.get('iterations','fld5BFCMTn')) or 1),
        'dev_count': 2, 'test_count': 2,
        'dev_product_ratio': str(v(rec,F.get('dev_product_ratio','fldFPiQasw')) or ''),
        'product_clarify_md': float(v(rec,F.get('product_clarify_md','fld0fMXr6m')) or 0),
        'demand_md': float(v(rec,F.get('demand_md','fldhE38q8v')) or 0),
        'review_md': float(v(rec,F.get('tech_review_md','fldm3qBBJx')) or 0),
        'pm_name': pm, 'dev_slots': '', 'test_slots': '',
        'dev_man_days': float(v(rec,F.get('standard_dev_md','fldOPeRb5q')) or 0),
        'test_man_days_bitable': float(v(rec,F.get('standard_test_md','fldRWW5yQT')) or 0),
        'accept_man_days_bitable': float(v(rec,F.get('standard_accept_md','fldxIxJkPI')) or 0),
        'original_index': i,
    })

def prepare_and_run(td_list, cal, today, boost_map=None):
    """boost_map: {task_name: new_dev_count}"""
    tasks = []
    for td in td_list:
        t = Task.from_dict(td); t.original_index = td.get('original_index',999)
        t.product_clarify_md = td['product_clarify_md']
        t.demand_md = td['demand_md']; t.review_md = td['review_md']
        if boost_map and t.name in boost_map:
            t.dev_count = boost_map[t.name]
            t.dev_workers = boost_map[t.name]
            # Recalculate dev_wd with new dev_count
            if t.dev_man_days and t.dev_man_days > 0:
                t.dev_wd = max(1, math.ceil(t.dev_man_days / t.dev_workers))
            else:
                t.dev_wd = 0
        t._calc_req_wd()
        tasks.append(t)
    
    pm_tl = defaultdict(list)
    for t in tasks: pm_tl[t.pm_name or '_none'].append(t)
    for pm, pts in pm_tl.items():
        wtr = [pp for pp in pts if pp.tech_review is not None]
        if wtr: min(wtr, key=lambda x: x.tech_review).is_anchor = True
    
    active = [t for t in tasks if not (t.is_anchor and t.tech_review and t.tech_review < today)]
    for t in active:
        if t.is_anchor and t.tech_review is None and t.design_end:
            if t.review_md and t.review_md > 0:
                rs = cal.next_working_day(t.design_end)
                re = cal.add_working_days(rs, math.ceil(t.review_md))
                t.tech_review_start = rs; t.tech_review_end = re
                t.tech_review = cal.next_working_day(re)
            else: t.tech_review = cal.next_working_day(t.design_end)
        if t.is_anchor and t.clarify_start is None and t.design_start:
            tc = max(1, math.ceil(t.iterations * 3))
            ce = cal.prev_working_day(t.design_start); cs = ce
            for _ in range(tc-1): cs = cal.prev_working_day(cs)
            t.clarify_start = cs; t.clarify_end = ce
        if not t.is_anchor and t.tech_review is not None:
            if t.design_start and t.design_end: t.new_req_start=t.design_start; t.new_req_end=t.design_end
            if t.clarify_start and t.clarify_end: t.new_clarify_start=t.clarify_start; t.new_clarify_end=t.clarify_end
    
    sched = schedule_all(active, cal, today, max_gap_days=5)
    
    # Serial dev v4->v5
    v4 = next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c4' in t.name), None)
    v5 = next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c5' in t.name), None)
    if v4 and v5 and v4.new_dev_end and v5.new_dev_start:
        md5 = cal.next_working_day(v4.new_dev_end)
        if v5.tech_review: md5 = max(md5, cal.next_working_day(v5.tech_review))
        if v5.new_dev_start < md5:
            v5.new_dev_start = md5; v5.new_dev_end = cal.add_working_days(md5, v5.dev_wd)
            v5.new_test_start = cal.next_working_day(v5.new_dev_end)
            v5.new_test_end = cal.add_working_days(v5.new_test_start, v5.test_wd)
            v5.new_accept_start = cal.next_working_day(v5.new_test_end)
            v5.new_accept_end = cal.add_working_days(v5.new_accept_start, v5.accept_wd)
    return sched

# Baseline
sched_base = prepare_and_run(task_dicts, cal, today)

# Identify last task per PM (by latest accept end)
pm_last = {}
for t in sched_base:
    pm = t.pm_name or '_'
    if pm not in pm_last or (t.new_accept_end and (not pm_last[pm][1] or t.new_accept_end > pm_last[pm][1])):
        pm_last[pm] = (t.name, t.new_accept_end, t.dev_wd, t.dev_man_days)

print('=' * 90)
print('每位PM的末尾任务（加速收益最大的目标）')
print('=' * 90)
for pm, (name, end, wd, md) in sorted(pm_last.items()):
    if pm == '_': continue
    wd3 = math.ceil(md/3) if md else wd
    wd4 = math.ceil(md/4) if md else wd
    save3 = wd - wd3
    save4 = wd - wd4
    print(f'\n{pm}: [{name[:40]:40s}] 当前dev_wd={wd:2d}wd')
    print(f'  3人: {wd3}wd (省{save3}wd)  4人: {wd4}wd (省{save4}wd)')

# Test: boost last task per PM to 3
boost_map_3 = {name: 3 for pm, (name, *_) in pm_last.items()}
sched_3 = prepare_and_run(task_dicts, cal, today, boost_map=boost_map_3)

# Test: boost last task per PM to 4
boost_map_4 = {name: 4 for pm, (name, *_) in pm_last.items()}
sched_4 = prepare_and_run(task_dicts, cal, today, boost_map=boost_map_4)

print(f'\n{"="*90}')
print('方案对比')
print(f'{"="*90}')
print(f'{"方案":<12} {"最后完成日":<16} {"刘观福":<14} {"肖维":<14} {"许大庆":<14} {"谢蓉":<14} {"梁景悦":<14} {"谭雨薇":<14}')
print(f'{"-"*12} {"-"*16} {"-"*14} {"-"*14} {"-"*14} {"-"*14} {"-"*14} {"-"*14}')

pm_order = ['刘观福','肖维','许大庆','谢蓉','梁景悦','谭雨薇']

for label, sched in [('A: 基准(2人)', sched_base), ('B: 末尾3人', sched_3), ('C: 末尾4人', sched_4)]:
    latest = max((t.new_accept_end or date.min for t in sched), default=date.min)
    pm_ends = []
    for pm in pm_order:
        pm_t = [t for t in sched if t.pm_name==pm]
        pe = max((t.new_accept_end or date.min for t in pm_t if t.new_accept_end), default=None)
        pm_ends.append(str(pe) if pe else '-')
    print(f'{label:<12} {str(latest):<16} {pm_ends[0]:<14} {pm_ends[1]:<14} {pm_ends[2]:<14} {pm_ends[3]:<14} {pm_ends[4]:<14} {pm_ends[5]:<14}')

# Show impacted tasks
print(f'\n{"="*90}')
print('任务级对比（有变化的）')
print(f'{"="*90}')
for t_b in sorted(sched_base, key=lambda x: x.new_accept_end or date.max):
    t_3 = next((x for x in sched_3 if x.name==t_b.name), None)
    t_4 = next((x for x in sched_4 if x.name==t_b.name), None)
    if not t_3 or not t_4: continue
    
    changes = []
    if t_3.new_accept_end != t_b.new_accept_end:
        changes.append(f'3人验收:{t_b.new_accept_end}->{t_3.new_accept_end}')
    if t_4.new_accept_end != t_b.new_accept_end:
        changes.append(f'4人验收:{t_b.new_accept_end}->{t_4.new_accept_end}')
    
    if changes:
        wd_info = f'({t_b.dev_wd}wd->{t_3.dev_wd}wd->{t_4.dev_wd}wd)'
        print(f'  {t_b.name[:45]:45s} {wd_info:20s} | {"; ".join(changes)}')
