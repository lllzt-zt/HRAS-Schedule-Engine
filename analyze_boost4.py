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
r = raw['data']['data']; fids = raw['data']['field_id_list']; rids = raw['data']['record_id_list']
idx = {fid:i for i,fid in enumerate(fids)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

td_list = []
for i,rec in enumerate(r):
    cv=v(rec,F.get('reserved_canceled','fldF6tKrhx'))
    if cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='\u662f': continue
    if s(rec,F.get('phase','fldSuDoxFN')) != '\u4e00\u671f': continue
    pm_v=v(rec,F.get('product_owner','fldsoiimKC'))
    pm=pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    td_list.append({
        'name':v(rec,F.get('name','fldD5A1CvQ')) or '','module':s(rec,F.get('module','fldPJrW9qs')),
        'tech_review':d(rec,F.get('tech_review','fldMxPbqBt')),
        'clarify_start':d(rec,F.get('clarify_start','fldSRgjNL3')),'clarify_end':d(rec,F.get('clarify_end','fldXvhBatN')),
        'tech_review_start':d(rec,F.get('tech_review_start','fldJkPovnb')),'tech_review_end':d(rec,F.get('tech_review_end','fldRvi3Miv')),
        'design_start':d(rec,F.get('design_start','fld5PTJ6XN')),'design_end':d(rec,F.get('design_end','fldaMhsTR5')),
        'iterations':float(v(rec,F.get('iterations','fld5BFCMTn')) or 1),'dev_count':2,'test_count':2,
        'dev_product_ratio':str(v(rec,F.get('dev_product_ratio','fldFPiQasw')) or ''),
        'product_clarify_md':float(v(rec,F.get('product_clarify_md','fld0fMXr6m')) or 0),
        'demand_md':float(v(rec,F.get('demand_md','fldhE38q8v')) or 0),
        'review_md':float(v(rec,F.get('tech_review_md','fldm3qBBJx')) or 0),
        'pm_name':pm,'dev_slots':'','test_slots':'',
        'dev_man_days':float(v(rec,F.get('standard_dev_md','fldOPeRb5q')) or 0),
        'test_man_days_bitable':float(v(rec,F.get('standard_test_md','fldRWW5yQT')) or 0),
        'accept_man_days_bitable':float(v(rec,F.get('standard_accept_md','fldxIxJkPI')) or 0),
        'original_index':i,'record_id':rids[i] if i<len(rids) else '',
    })

def run_sched(td_list, cal, today, boost_tasks=None):
    """boost_tasks: {name: dev_count}"""
    tasks = []
    for td in td_list:
        t=Task.from_dict(td); t.original_index=td.get('original_index',999)
        t.product_clarify_md=td['product_clarify_md']; t.demand_md=td['demand_md']; t.review_md=td['review_md']
        if boost_tasks and t.name in boost_tasks:
            t.dev_count=boost_tasks[t.name]; t.dev_workers=boost_tasks[t.name]
            if t.dev_man_days and t.dev_man_days>0: t.dev_wd=max(1,math.ceil(t.dev_man_days/t.dev_workers))
            else: t.dev_wd=0
        t._calc_req_wd(); tasks.append(t)
    
    pm_tl=defaultdict(list)
    for t in tasks: pm_tl[t.pm_name or '_none'].append(t)
    for pm,pts in pm_tl.items():
        wtr=[pp for pp in pts if pp.tech_review is not None]
        if wtr: min(wtr,key=lambda x:x.tech_review).is_anchor=True
    
    active=[t for t in tasks if not(t.is_anchor and t.tech_review and t.tech_review<today)]
    for t in active:
        if t.is_anchor and t.tech_review is None and t.design_end:
            if t.review_md and t.review_md>0:
                rs=cal.next_working_day(t.design_end); re=cal.add_working_days(rs,math.ceil(t.review_md))
                t.tech_review_start=rs; t.tech_review_end=re; t.tech_review=cal.next_working_day(re)
            else: t.tech_review=cal.next_working_day(t.design_end)
        if t.is_anchor and t.clarify_start is None and t.design_start:
            tc=max(1,math.ceil(t.iterations*3)); ce=cal.prev_working_day(t.design_start); cs=ce
            for _ in range(tc-1): cs=cal.prev_working_day(cs)
            t.clarify_start=cs; t.clarify_end=ce
        if not t.is_anchor and t.tech_review is not None:
            if t.design_start and t.design_end: t.new_req_start=t.design_start; t.new_req_end=t.design_end
            if t.clarify_start and t.clarify_end: t.new_clarify_start=t.clarify_start; t.new_clarify_end=t.clarify_end
    
    sched=schedule_all(active,cal,today,max_gap_days=5)
    v4=next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c4' in t.name),None)
    v5=next((t for t in sched if t.pm_name=='\u6881\u666f\u60a6' and '\u7248\u672c5' in t.name),None)
    if v4 and v5 and v4.new_dev_end and v5.new_dev_start:
        md5=cal.next_working_day(v4.new_dev_end)
        if v5.tech_review: md5=max(md5,cal.next_working_day(v5.tech_review))
        if v5.new_dev_start<md5:
            v5.new_dev_start=md5; v5.new_dev_end=cal.add_working_days(md5,v5.dev_wd)
            v5.new_test_start=cal.next_working_day(v5.new_dev_end)
            v5.new_test_end=cal.add_working_days(v5.new_test_start,v5.test_wd)
            v5.new_accept_start=cal.next_working_day(v5.new_test_end)
            v5.new_accept_end=cal.add_working_days(v5.new_accept_start,v5.accept_wd)
    return sched

# Baseline
base = run_sched(td_list, cal, today)

# Find when 101/102 are freed (绩效version4 dev end, version5 dev end, perf AI Agent dev end)
perf_tasks = [t for t in base if t.pm_name=='梁景悦' and t.module=='绩效']
perf_dev_ends = [t.new_dev_end for t in perf_tasks if t.new_dev_end]
release_date = max(perf_dev_ends) if perf_dev_ends else date(2026, 9, 1)
print(f'绩效任务研发结束日: {[str(d) for d in perf_dev_ends]}')
print(f'101/102释放日 (最晚绩效dev结束): {release_date}')
print(f'释放日+1wd = {cal.next_working_day(release_date)}')

# Find tasks that start dev AT or AFTER release date, sorted by priority
candidates = []
for t in base:
    if t.new_dev_start and t.new_dev_start >= release_date and t.dev_man_days and t.dev_man_days > 0:
        candidates.append(t)
candidates.sort(key=lambda t: (-t.dev_man_days, MODULE_PRIORITY.get(t.module, 99)))

print(f'\n释放后可加速任务候选 (按dev_md↓ + 模块优先级↓):')
for i, t in enumerate(candidates):
    print(f'  {i+1}. [{t.module}] {t.name[:45]:45s} dev_md={t.dev_man_days:.0f} 当前dev_wd={t.dev_wd}wd')

# Run scenarios: boost top 1, 2, 3, 4 tasks
print(f'\n{"="*100}')
print(f'多场景对比：释放后按优先级加速N个任务')
print(f'{"="*100}')
print(f'{"场景":<20} {"最后完成日":<16} {"刘观福":<14} {"谢蓉":<14} {"梁景悦":<14} {"谭雨薇":<14} {"提速":<10}')
print(f'{"-"*20} {"-"*16} {"-"*14} {"-"*14} {"-"*14} {"-"*14} {"-"*10}')

scenarios = [('基准(2人)', {}), ('加速 1个', {candidates[0].name: 3} if candidates else {})]
if len(candidates) >= 2:
    scenarios.append(('加速 2个', {c.name: 3 for c in candidates[:2]}))
if len(candidates) >= 3:
    scenarios.append(('加速 3个', {c.name: 3 for c in candidates[:3]}))
if len(candidates) >= 4:
    scenarios.append(('加速 4个', {c.name: 3 for c in candidates[:4]}))

base_latest = max((t.new_accept_end or date.min for t in base), default=date.min)

for label, boost in scenarios:
    sched = run_sched(td_list, cal, today, boost if boost else None)
    latest = max((t.new_accept_end or date.min for t in sched), default=date.min)
    days_saved = (base_latest - latest).days if latest < base_latest else 0
    
    def pe(pm):
        pm_t=[t for t in sched if t.pm_name==pm and t.new_accept_end]
        return str(max(t.new_accept_end for t in pm_t)) if pm_t else '-'
    
    print(f'{label:<20} {str(latest):<16} {pe("刘观福"):<14} {pe("谢蓉"):<14} {pe("梁景悦"):<14} {pe("谭雨薇"):<14} {"+"+str(days_saved)+"天" if days_saved>0 else "-":<10}')

# Show which tasks got accelerated in each scenario
print(f'\n{"="*100}')
print(f'受影响任务明细 (dev_wd变化)')
print(f'{"="*100}')
for label, boost in scenarios:
    if not boost: continue
    sched = run_sched(td_list, cal, today, boost)
    print(f'\n{label}:')
    for t_b in base:
        t_c = next((x for x in sched if x.name==t_b.name), None)
        if t_c and t_c.dev_wd != t_b.dev_wd:
            end_b = t_b.new_accept_end
            end_c = t_c.new_accept_end
            end_str = f'验收:{end_b}->{end_c}' if end_b != end_c else ''
            print(f'  {t_b.name[:45]:45s} dev_wd:{t_b.dev_wd:2d}->{t_c.dev_wd:2d}  {end_str}')
