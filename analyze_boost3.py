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

def prepare_and_run(td_list, cal, today, after_date=None, boost_count=2):
    """
    after_date: free 101/102 after this date
    boost_count: how many tasks get the freed slots (each +1 dev)
    """
    # First run baseline to see which tasks start dev after after_date
    tasks_base = []
    for td in td_list:
        t = Task.from_dict(td); t.original_index = td.get('original_index',999)
        t.product_clarify_md = td['product_clarify_md']
        t.demand_md = td['demand_md']; t.review_md = td['review_md']
        t._calc_req_wd(); tasks_base.append(t)
    
    pm_tl = defaultdict(list)
    for t in tasks_base: pm_tl[t.pm_name or '_none'].append(t)
    for pm, pts in pm_tl.items():
        wtr = [pp for pp in pts if pp.tech_review is not None]
        if wtr: min(wtr, key=lambda x: x.tech_review).is_anchor = True
    active = [t for t in tasks_base if not (t.is_anchor and t.tech_review and t.tech_review < today)]
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
    
    sched_base = schedule_all(active, cal, today, max_gap_days=5)
    
    # Serial dev v4->v5
    v4 = next((t for t in sched_base if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c4' in t.name), None)
    v5 = next((t for t in sched_base if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c5' in t.name), None)
    if v4 and v5 and v4.new_dev_end and v5.new_dev_start:
        md5 = cal.next_working_day(v4.new_dev_end)
        if v5.tech_review: md5 = max(md5, cal.next_working_day(v5.tech_review))
        if v5.new_dev_start < md5:
            v5.new_dev_start = md5; v5.new_dev_end = cal.add_working_days(md5, v5.dev_wd)
            v5.new_test_start = cal.next_working_day(v5.new_dev_end)
            v5.new_test_end = cal.add_working_days(v5.new_test_start, v5.test_wd)
            v5.new_accept_start = cal.next_working_day(v5.new_test_end)
            v5.new_accept_end = cal.add_working_days(v5.new_accept_start, v5.accept_wd)
    
    if not after_date or boost_count <= 0:
        return sched_base
    
    # Find tasks whose dev starts AFTER after_date (freed 101/102)
    candidates = []
    for t in sched_base:
        if t.new_dev_start and t.new_dev_start >= after_date and t.dev_man_days and t.dev_man_days > 0:
            candidates.append(t)
    
    # Sort by dev_man_days desc, then module priority
    candidates.sort(key=lambda t: (-t.dev_man_days, MODULE_PRIORITY.get(t.module, 99)))
    
    # Pick top N to boost
    boosted_names = [t.name for t in candidates[:boost_count]]
    
    # Now re-run with boost
    tasks = []
    for td in td_list:
        t = Task.from_dict(td); t.original_index = td.get('original_index',999)
        t.product_clarify_md = td['product_clarify_md']
        t.demand_md = td['demand_md']; t.review_md = td['review_md']
        if t.name in boosted_names:
            t.dev_count = 3
            t.dev_workers = 3
            if t.dev_man_days and t.dev_man_days > 0:
                t.dev_wd = max(1, math.ceil(t.dev_man_days / t.dev_workers))
            else: t.dev_wd = 0
        t._calc_req_wd(); tasks.append(t)
    
    pm_tl2 = defaultdict(list)
    for t in tasks: pm_tl2[t.pm_name or '_none'].append(t)
    for pm, pts in pm_tl2.items():
        wtr = [pp for pp in pts if pp.tech_review is not None]
        if wtr: min(wtr, key=lambda x: x.tech_review).is_anchor = True
    active2 = [t for t in tasks if not (t.is_anchor and t.tech_review and t.tech_review < today)]
    for t in active2:
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
    
    sched = schedule_all(active2, cal, today, max_gap_days=5)
    
    # Serial dev v4->v5 again
    v4b = next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c4' in t.name), None)
    v5b = next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c5' in t.name), None)
    if v4b and v5b and v4b.new_dev_end and v5b.new_dev_start:
        md5b = cal.next_working_day(v4b.new_dev_end)
        if v5b.tech_review: md5b = max(md5b, cal.next_working_day(v5b.tech_review))
        if v5b.new_dev_start < md5b:
            v5b.new_dev_start = md5b; v5b.new_dev_end = cal.add_working_days(md5b, v5b.dev_wd)
            v5b.new_test_start = cal.next_working_day(v5b.new_dev_end)
            v5b.new_test_end = cal.add_working_days(v5b.new_test_start, v5b.test_wd)
            v5b.new_accept_start = cal.next_working_day(v5b.new_test_end)
            v5b.new_accept_end = cal.add_working_days(v5b.new_accept_start, v5b.accept_wd)
    
    print(f'  释放后加速任务(按人天>模块优先级):')
    for t in candidates[:boost_count]:
        print(f'    +1人: {t.name[:45]:45s} dev_md={t.dev_man_days:.0f} 模块={t.module}')
    
    return sched, boosted_names

print('=' * 100)
print('101/102释放后分配方案分析')
print('绩效任务释放101/102时间 ≈ 9/18')
print('规则: 分配给9/18后开始研发的任务，dev_man_days大的优先，同人天模块优先级高的优先')
print('=' * 100)

# 绩效释放日期 from 梁景悦's dev schedule
# 版本4 dev 7/20~7/31, 版本5 dev 8/03~8/14, 绩效AI Agent dev 8/17~8/28
# So 101/102 fully freed by 8/28~9/01 (after 绩效AI Agent dev ends)
# Let's use 9/01 as a conservative estimate

# Baseline
sched_base = prepare_and_run(task_dicts, cal, today)
print(f'\nA: 基准(2人)')
latest = max((t.new_accept_end or date.min for t in sched_base), default=date.min)
print(f'  最后完成日: {latest}')

# Boost 1 task after 9/01
sched_b1, boosted1 = prepare_and_run(task_dicts, cal, today, after_date=date(2026, 9, 1), boost_count=1)
latest_b1 = max((t.new_accept_end or date.min for t in sched_b1), default=date.min)
print(f'\nB: 释放后+1个3人任务')
print(f'  最后完成日: {latest_b1}')

# Boost 2 tasks after 9/01
sched_b2, boosted2 = prepare_and_run(task_dicts, cal, today, after_date=date(2026, 9, 1), boost_count=2)
latest_b2 = max((t.new_accept_end or date.min for t in sched_b2), default=date.min)
print(f'\nC: 释放后+2个3人任务')
print(f'  最后完成日: {latest_b2}')

print(f'\n{"="*100}')
print('各PM最后完成日对比')
print(f'{"="*100}')
print(f'{"PM":<10} {"基准(2人)":<16} {"+1个3人":<16} {"+2个3人":<16}')
print(f'{"-"*10} {"-"*16} {"-"*16} {"-"*16}')

for pm in ['刘观福','肖维','许大庆','谢蓉','梁景悦','谭雨薇']:
    def get_end(sched):
        pm_t = [t for t in sched if t.pm_name==pm and t.new_accept_end]
        return str(max(t.new_accept_end for t in pm_t)) if pm_t else '-'
    print(f'{pm:<10} {get_end(sched_base):<16} {get_end(sched_b1):<16} {get_end(sched_b2):<16}')

print(f'\n受影响的任务（dev_wd变化）:')
for t_b in sched_base:
    t_c = next((x for x in sched_b2 if x.name==t_b.name), None)
    if t_c and t_c.dev_wd != t_b.dev_wd:
        print(f'  {t_b.name[:45]:45s} dev_wd: {t_b.dev_wd}->{t_c.dev_wd}')
