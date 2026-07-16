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
        'original_index':i,
    })

def run(boost):
    tasks=[]
    for td in td_list:
        t=Task.from_dict(td); t.original_index=td.get('original_index',999)
        t.product_clarify_md=td['product_clarify_md']; t.demand_md=td['demand_md']; t.review_md=td['review_md']
        if boost and t.name in boost:
            t.dev_count=boost[t.name]; t.dev_workers=boost[t.name]
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
    v4=next((t for t in sched if t.pm_name=='梁景悦' and '版本4' in t.name),None)
    v5=next((t for t in sched if t.pm_name=='梁景悦' and '版本5' in t.name),None)
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

# Run with 班次考勤4人
sched = run({'【班次与考勤】班次、打卡、考勤计算（含全勤奖）、报表': 4})

test_tasks = [t for t in sched if t.new_test_start and t.new_test_end and t.test_wd > 0]
test_tasks.sort(key=lambda x: x.new_test_start)

print('=' * 80)
print('测试资源利用率分析')
print('=' * 80)

print(f'\n测试阶段时间线:')
for t in test_tasks:
    print(f'  {t.name[:40]:40s} {str(t.new_test_start):12s}~{str(t.new_test_end):12s} (test_wd={t.test_wd:2d}, {t.test_workers}人)')

# Concurrency
tdays = defaultdict(set)
for t in test_tasks:
    d = t.new_test_start
    while d <= t.new_test_end:
        if cal.is_working_day(d):
            tdays[d].add(t.name)
        d += timedelta(days=1)

max_c = max(len(v) for v in tdays.values()) if tdays else 0
peak_d = max(tdays, key=lambda d: len(tdays[d])) if tdays else None
print(f'\n并发统计:')
print(f'  最大并发: {max_c}个任务 ({peak_d})')
print(f'  平均并发: {sum(len(v) for v in tdays.values())/max(1,len(tdays)):.1f}个')
print(f'  测试槽位: 4个  |  每个任务用2个槽位')
print(f'  理论最大并发: 2个任务同时测试')

# Last task test (班次考勤)
banci = [t for t in test_tasks if '班次' in t.name and '考勤' in t.name][0]
print(f'\n【班次与考勤】加速测试分析:')
print(f'  当前(2人): test_wd={banci.test_wd}  test_md={banci.test_man_days}')
print(f'  3人: test_wd={max(1,math.ceil(banci.test_man_days/3))}')
print(f'  4人: test_wd={max(1,math.ceil(banci.test_man_days/4))}')

# When does 班次 test start?
print(f'  测试开始: {banci.new_test_start}')
print(f'  此时其他测试任务:')
for t in test_tasks:
    if t is not banci and t.new_test_start and t.new_test_end:
        if t.new_test_start <= banci.new_test_start <= t.new_test_end:
            print(f'    {t.name[:40]:40s} {t.new_test_start}~{t.new_test_end} (占2槽位)')
