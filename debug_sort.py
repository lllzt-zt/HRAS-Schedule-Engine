import sys, json, math, subprocess
sys.path.insert(0, 'D:/WorkBuddyPlace/Project-scheduling')
from schedule_engine import Task, WorkingDayCalendar, MODULE_PRIORITY, schedule_all, load_feishu_config
from datetime import date
from collections import defaultdict

today = date(2026, 7, 16)
with open('holidays.json') as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get('holidays',[]), hd.get('makeup_days',[]))
F = load_feishu_config('feishu_config.json')

result = subprocess.run('lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user', capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
r = raw['data']['data']; fids = raw['data']['field_id_list']
idx = {fid:i for i,fid in enumerate(fids)}
def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

td_list = []
for i,rec in enumerate(raw['data']['data']):
    cv=v(rec,F.get('reserved_canceled','fldF6tKrhx'))
    if cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='是': continue
    if s(rec,F.get('phase','fldSuDoxFN')) != '一期': continue
    pm_v=v(rec,F.get('product_owner','fldsoiimKC'))
    pm=pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    td_list.append({
        'name':v(rec,F.get('name','fldD5A1CvQ')) or '','module':s(rec,F.get('module','fldPJrW9qs')),
        'tech_review':d(rec,F.get('tech_review','fldMxPbqBt')),
        'clarify_start':d(rec,F.get('clarify_start','fldSRgjNL3')),
        'clarify_end':d(rec,F.get('clarify_end','fldXvhBatN')),
        'tech_review_start':d(rec,F.get('tech_review_start','fldJkPovnb')),
        'tech_review_end':d(rec,F.get('tech_review_end','fldRvi3Miv')),
        'design_start':d(rec,F.get('design_start','fld5PTJ6XN')),
        'design_end':d(rec,F.get('design_end','fldaMhsTR5')),
        'iterations':float(v(rec,F.get('iterations','fld5BFCMTn')) or 1),
        'dev_product_ratio':str(v(rec,F.get('dev_product_ratio','fldFPiQasw')) or ''),
        'product_clarify_md':float(v(rec,F.get('product_clarify_md','fld0fMXr6m')) or 0),
        'demand_md':float(v(rec,F.get('demand_md','fldhE38q8v')) or 0),
        'review_md':float(v(rec,F.get('tech_review_md','fldm3qBBJx')) or 0),
        'pm_name':pm,'dev_slots':'','test_slots':'',
        'dev_man_days':float(v(rec,F.get('standard_dev_md','fldOPeRb5q')) or 0),
        'original_index':i,
    })

tasks = []
for td in td_list:
    t=Task.from_dict(td); t.original_index=td.get('original_index',999)
    t.product_clarify_md=td['product_clarify_md']; t.demand_md=td['demand_md']
    t.review_md=td['review_md']; t._calc_req_wd(); tasks.append(t)

# Per-PM anchor
pm_tl=defaultdict(list)
for t in tasks: pm_tl[t.pm_name or '_none'].append(t)
for pm,pts in pm_tl.items():
    wtr=[pp for pp in pts if pp.tech_review is not None]
    if wtr: min(wtr,key=lambda x:x.tech_review).is_anchor=True

active=[t for t in tasks if not(t.is_anchor and t.tech_review and t.tech_review<today)]

# Fix 3 & 4 (same as simulate_all_pm.py)
for t in active:
    if t.is_anchor and t.tech_review is None and t.design_end:
        if t.review_md and t.review_md>0:
            rs=cal.next_working_day(t.design_end)
            re=cal.add_working_days(rs,math.ceil(t.review_md))
            t.tech_review_start=rs; t.tech_review_end=re
            t.tech_review=cal.next_working_day(re)
        else: t.tech_review=cal.next_working_day(t.design_end)
    if t.is_anchor and t.clarify_start is None and t.design_start:
        tc=max(1,math.ceil(t.iterations*3))
        ce=cal.prev_working_day(t.design_start); cs=ce
        for _ in range(tc-1): cs=cal.prev_working_day(cs)
        t.clarify_start=cs; t.clarify_end=ce
    if not t.is_anchor and t.tech_review is not None:
        t.tech_review = None

# Print 刘观福 tasks' state before schedule
liu = [t for t in active if t.pm_name == '刘观福']
print('刘观福任务入schedule之前:')
for t in sorted(liu, key=lambda x: (0 if x.is_anchor else 1, MODULE_PRIORITY.get(x.module,99), x.original_index)):
    print(f'  {t.module:8s} {t.name[:30]:30s} is_anchor={t.is_anchor} tech_review={t.tech_review} new_req={t.new_req_start}')

sched = schedule_all(active, cal, today, max_gap_days=5)

print()
for t in sorted(sched, key=lambda x: x.new_clarify_start or date(9999,1,1)):
    if t.pm_name == '刘观福':
        cs = t.new_clarify_start or t.clarify_start
        tr = t.tech_review
        print(f'  {t.module:8s} {t.name[:30]:30s} clarify开始={cs} tech_review={tr} new_req={t.new_req_start}')
