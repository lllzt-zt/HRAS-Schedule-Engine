"""用引擎标准样式生成二/三期报告：从表中读取已有排期数据，设置Task的new_*字段后调用generate_html"""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from schedule_engine import Task, WorkingDayCalendar, generate_html, load_feishu_config, MODULE_PRIORITY
from datetime import date, timedelta
from collections import defaultdict

BASE = "BKBKbUWXtas7tSshzoccyZa3ndb"
TABLE = "tbl5jDThuT51h4II"
today = date(2026, 7, 16)
with open('holidays.json') as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get('holidays',[]), hd.get('makeup_days',[]))

result = subprocess.run(
    f'lark-cli base +record-list --base-token {BASE} --table-id {TABLE} --limit 200 --format json --as user',
    capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']; fids = raw['data']['field_id_list']; rids = raw['data']['record_id_list']
idx = {f:i for i,f in enumerate(fids)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

def parse_date(s):
    try: return date.fromisoformat(s) if s else None
    except: return None

F = load_feishu_config('feishu_config.json')

tasks = []
for i, rec in enumerate(records):
    phase = s(rec, 'fldSuDoxFN')
    if phase not in ('二期','三期'): continue
    if s(rec, 'fldp8xrC9I') == '是': continue
    if v(rec, 'fldF6tKrhx'): continue
    pm_v = v(rec, 'fldsoiimKC')
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    nm = (v(rec, 'fldD5A1CvQ') or '').replace(chr(10),' ').strip()
    mod = s(rec, 'fldPJrW9qs')
    test_ratio = str(v(rec, 'fldWzxXVx2') or v(rec, 'flduOhVcYR') or '')
    
    # Create Task with minimal fields
    td = {
        'name': nm, 'module': mod, 'phase': phase,
        'tech_review': d(rec, 'fldMxPbqBt'),
        'iterations': float(v(rec, 'fld5BFCMTn') or 1),
        'dev_product_ratio': str(v(rec, 'fldFPiQasw') or ''),
        'dev_test_ratio': test_ratio,
        'product_clarify_md': float(v(rec, 'fld0fMXr6m') or 0),
        'demand_md': float(v(rec, 'fldhE38q8v') or 0),
        'review_md': float(v(rec, 'fldm3qBBJx') or 0),
        'pm_name': pm,
        'dev_count': int(v(rec, 'fldjrzlD4T') or 2),
        'test_count': int(v(rec, 'fldXYCa4gc') or 2),
        'dev_slots': (v(rec, 'fldCeLIjfG') or '').replace('101','A').replace('102','B'),
        'test_slots': v(rec, 'fldFaworUZ') or '',
        'dev_man_days': float(v(rec, 'fldOPeRb5q') or 0),
        'test_man_days_bitable': float(v(rec, 'fldRWW5yQT') or 0),
        'accept_man_days_bitable': float(v(rec, 'fldxIxJkPI') or 0),
        'old_dev_start': None, 'old_dev_end': None,
        'original_index': i,
    }
    
    t = Task.from_dict(td)
    t.original_index = i
    t.product_clarify_md = td['product_clarify_md']
    t.demand_md = td['demand_md']
    t.review_md = td['review_md']
    t.dev_test_ratio = td['dev_test_ratio']
    t.phase_name = phase
    
    # Now set new_* fields from table data (engine output)
    t.new_dev_start = parse_date(d(rec, 'fldu4esCvi'))
    t.new_dev_end = parse_date(d(rec, 'fldpXsPTvE'))
    t.new_test_start = parse_date(d(rec, 'fldlCFwt7C'))
    t.new_test_end = parse_date(d(rec, 'fld98ZTVMF'))
    t.new_accept_start = parse_date(d(rec, 'fldgv6n2rx'))
    t.new_accept_end = parse_date(d(rec, 'flduVm6fRo'))
    
    # Set req phase dates
    t.new_clarify_start = parse_date(d(rec, 'fldSRgjNL3'))
    t.new_clarify_end = parse_date(d(rec, 'fldXvhBatN'))
    t.new_req_start = parse_date(d(rec, 'fld5PTJ6XN'))
    t.new_req_end = parse_date(d(rec, 'fldaMhsTR5'))
    t.new_review_start = parse_date(d(rec, 'fldJkPovnb'))
    t.new_review_end = parse_date(d(rec, 'fldRvi3Miv'))
    
    t.tech_review = parse_date(d(rec, 'fldMxPbqBt'))
    
    # Dev/test man-days for report display
    if td['dev_man_days']: t.dev_man_days = td['dev_man_days']
    if td['test_man_days_bitable']: t.test_man_days = td['test_man_days_bitable']
    if td['accept_man_days_bitable']: t.accept_man_days = td['accept_man_days_bitable']
    
    # Set warnings (none for existing data)
    t.warnings = []
    
    tasks.append(t)

# Generate using engine's generate_html
generate_html(tasks, cal, today, 'schedule_report_p23_final.html', max_gap_days=5)
print(f"✅ 报告已生成: schedule_report_p23_final.html ({len(tasks)}个任务)")
