import sys, json, math, subprocess
sys.path.insert(0, 'D:/WorkBuddyPlace/Project-scheduling')
from schedule_engine import Task, WorkingDayCalendar, schedule_all, load_feishu_config, DEFAULT_FEISHU_FIELDS
from datetime import date

today = date(2026, 7, 16)
with open('holidays.json') as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get('holidays',[]), hd.get('makeup_days',[]))
F = load_feishu_config('feishu_config.json') or DEFAULT_FEISHU_FIELDS

result = subprocess.run('lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user', capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']; fid_list = raw['data']['field_id_list']
idx = {fid: i for i, fid in enumerate(fid_list)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def ds(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

for i, rec in enumerate(records):
    nm = v(rec, F.get('name','fldD5A1CvQ')) or ''
    if '休假' not in nm or '配置' not in nm: continue
    cv = v(rec, F.get('reserved_canceled','fldF6tKrhx'))
    if cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='是': continue
    if s(rec, F.get('phase','fldSuDoxFN')) != '一期': continue
    
    print('=== Base原始数据 ===')
    print(f'  名称: {nm}')
    pm_v = v(rec, F.get("product_owner","fldsoiimKC"))
    pm_name = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    print(f'  PM: {pm_name}')
    print(f'  迭代数: {v(rec, F.get("iterations","fld5BFCMTn"))}')
    print(f'  产研比: {v(rec, F.get("dev_product_ratio","fldFPiQasw"))}')
    print(f'  澄清: {ds(rec,F.get("clarify_start","fldSRgjNL3"))} ~ {ds(rec,F.get("clarify_end","fldXvhBatN"))}')
    print(f'  需求: {ds(rec,F.get("design_start","fld5PTJ6XN"))} ~ {ds(rec,F.get("design_end","fldaMhsTR5"))}')
    print(f'  业务评审: {ds(rec,F.get("tech_review_start","fldJkPovnb"))} ~ {ds(rec,F.get("tech_review_end","fldRvi3Miv"))}')
    print(f'  技术评审: {ds(rec,F.get("tech_review","fldMxPbqBt"))}')
    print(f'  澄清人天={v(rec,F.get("product_clarify_md","fld0fMXr6m"))} 需求人天={v(rec,F.get("demand_md","fldhE38q8v"))} 评审人天={v(rec,F.get("tech_review_md","fldm3qBBJx"))}')
    
    # Build task
    td = {
        'name': nm, 'module': s(rec,F.get('module','fldPJrW9qs')),
        'phase': '一期',
        'tech_review': ds(rec,F.get('tech_review','fldMxPbqBt')),
        'clarify_start': ds(rec,F.get('clarify_start','fldSRgjNL3')),
        'clarify_end': ds(rec,F.get('clarify_end','fldXvhBatN')),
        'tech_review_start': ds(rec,F.get('tech_review_start','fldJkPovnb')),
        'tech_review_end': ds(rec,F.get('tech_review_end','fldRvi3Miv')),
        'design_start': ds(rec,F.get('design_start','fld5PTJ6XN')),
        'design_end': ds(rec,F.get('design_end','fldaMhsTR5')),
        'iterations': float(v(rec,F.get('iterations','fld5BFCMTn')) or 1),
        'dev_product_ratio': str(v(rec,F.get('dev_product_ratio','fldFPiQasw')) or ''),
        'pm_name': pm_name,
        'product_clarify_md': float(v(rec,F.get('product_clarify_md','fld0fMXr6m')) or 0),
        'demand_md': float(v(rec,F.get('demand_md','fldhE38q8v')) or 0),
        'review_md': float(v(rec,F.get('tech_review_md','fldm3qBBJx')) or 0),
        'dev_count': 2, 'test_count': 2,
        'dev_slots': '', 'test_slots': '',
        'dev_man_days': 0, 'test_man_days_bitable': 0, 'accept_man_days_bitable': 0,
        'original_index': i,
    }
    
    t = Task.from_dict(td)
    t.original_index = i
    t.product_clarify_md = td['product_clarify_md']
    t.demand_md = td['demand_md']
    t.review_md = td['review_md']
    t._calc_req_wd()
    t.is_anchor = True
    
    # Anchor init (same as simulate script)
    if not t.tech_review and t.design_end:
        if t.review_md and t.review_md > 0:
            t.tech_review = cal.add_working_days(cal.next_working_day(t.design_end), math.ceil(t.review_md))
        else:
            t.tech_review = cal.next_working_day(t.design_end)
    
    print(f'\n=== 引擎推导(排谢蓉单个任务) ===')
    print(f'  澄清总工期=ceil({td["iterations"]}x3)={max(1,math.ceil(td["iterations"]*3))}wd')
    print(f'  PM工时=产品澄清{td["product_clarify_md"]}+需求{td["demand_md"]}+评审{td["review_md"]}={t.req_wd}wd')
    print(f'  技术评审(推导): 设计结束{td["design_end"]} + 1wd = {cal.next_working_day(t.design_end)}')
    
print(f'\n=== 作为首Task的排期结果 ===')
print(f'  需求阶段: 从Base保留澄清{td["clarify_start"]}~{td["clarify_end"]}, 需求{td["design_start"]}~{td["design_end"]}')
print(f'    澄清(Base): {td["clarify_start"]} ~ {td["clarify_end"]}')
print(f'    需求(Base): {td["design_start"]} ~ {td["design_end"]}')
print(f'    业务评审(Base): 无录入')
print(f'  技术评审(推导): {t.tech_review}')
print(f'    推导公式: 需求结束{td["design_end"]} + 1wd = {cal.next_working_day(t.design_end)}')
print(f'    由于review_md={td["review_md"]}>0, 技术评审再加{math.ceil(td["review_md"])}wd评审工时')
print(f'    实际技术评审: {t.tech_review}')
print(f'\n=== 报告显示 ===')
print(f'  需求阶段: {td["clarify_start"]} ~ {td["design_end"]}')
print(f'    (澄清开始{td["clarify_start"]} ~ 需求结束{td["design_end"]}，业务评审无数据所以只到需求结束)')
print(f'  技术评审: {t.tech_review}')

