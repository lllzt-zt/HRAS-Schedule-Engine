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

base = run(None)
banci = [t for t in base if '班次' in t.name and '考勤' in t.name][0]
print(f'目标任务: {banci.name}')
print(f'当前(2人): dev_wd={banci.dev_wd}')
print(f'  研发 {banci.new_dev_start}~{banci.new_dev_end}')
print(f'  测试 {banci.new_test_start}~{banci.new_test_end}')
print(f'  验收 {banci.new_accept_start}~{banci.new_accept_end}')

# 4人 (101+102一起给班次)
sched4 = run({banci.name: 4})
t4 = [t for t in sched4 if t.name == banci.name][0]
print(f'\n4人(101+102): dev_wd={t4.dev_wd}')
print(f'  研发 {t4.new_dev_start}~{t4.new_dev_end}')
print(f'  测试 {t4.new_test_start}~{t4.new_test_end}')
print(f'  验收 {t4.new_accept_start}~{t4.new_accept_end}')

# Check slot usage
print(f'\n研发槽位分配情况:')
for t in sorted(sched4, key=lambda x: x.new_dev_start or date.max):
    if t.new_dev_start:
        ds = getattr(t, 'dev_slots', '?') or '引擎分配中'
        print(f'  {t.name[:40]:40s} {t.new_dev_start}~{t.new_dev_end} 槽位:{ds}')

# Per-PM end dates
print(f'\n各PM最后完成日:')
for label, sched in [('基准(2人)', base), ('4人(101+102)', sched4)]:
    latest = max((t.new_accept_end or date.min for t in sched), default=date.min)
    print(f'{label}: 总完成日={latest}')
    for pm in ['刘观福','谢蓉','梁景悦','谭雨薇']:
        pe = max((t.new_accept_end or date.min for t in sched if t.pm_name==pm and t.new_accept_end), default=None)
        print(f'  {pm}: {pe}')
