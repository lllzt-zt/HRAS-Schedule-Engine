# Discovery script - identifies anchors and module priorities only
import sys, json, subprocess
sys.path.insert(0, 'D:/WorkBuddyPlace/Project-scheduling')
from schedule_engine import Task, MODULE_PRIORITY, load_feishu_config, DEFAULT_FEISHU_FIELDS
from datetime import date, timedelta
from collections import defaultdict

today = date(2026, 7, 16)
F = load_feishu_config('feishu_config.json') or DEFAULT_FEISHU_FIELDS

result = subprocess.run('lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user', capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']; fid_list = raw['data']['field_id_list']
idx = {fid: i for i, fid in enumerate(fid_list)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

print('=' * 70)
print('发现阶段：读取任务数据')
print('=' * 70)

task_info = []
excluded = []
for i, rec in enumerate(records):
    cv = v(rec, F.get('reserved_canceled','fldF6tKrhx'))
    is_completed = cv and isinstance(cv,list) and len(cv)>0 and cv[0]=='是'
    if is_completed: excluded.append(f"已完成: {v(rec,F.get('name','fldD5A1CvQ'))}"); continue
    phase = s(rec, F.get('phase','fldSuDoxFN'))
    if phase != '一期': excluded.append(f"非一期({phase}): {v(rec,F.get('name','fldD5A1CvQ'))}"); continue
    
    pm_v = v(rec, F.get('product_owner','fldsoiimKC'))
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    tr_s = d(rec, F.get('tech_review','fldMxPbqBt'))
    module = s(rec, F.get('module','fldPJrW9qs'))
    name = v(rec, F.get('name','fldD5A1CvQ')) or ''
    
    # Parse tech_review, filter 1900-01-01
    tr_date = None
    if tr_s and tr_s[:4] != '1900':
        try: tr_date = date.fromisoformat(tr_s[:10])
        except: pass
    
    task_info.append({'pm': pm, 'name': name, 'module': module, 'tech_review': tr_date})

print(f'已排除: {len(excluded)}个')
for e in excluded: print(f'  {e}')
print(f'参与排期: {len(task_info)}个任务')

# Per-PM anchor detection
pm_tasks = defaultdict(list)
for t in task_info:
    pm_tasks[t['pm'] or '_none'].append(t)

print(f'\n{"="*70}')
print('首Task识别结果')
print(f'{"="*70}')
print(f'{"PM":<10} {"首Task":<40} {"技术评审":<16} {"模块":<10}')
print(f'{"-"*10} {"-"*40} {"-"*16} {"-"*10}')

for pm in sorted(pm_tasks.keys()):
    pts = pm_tasks[pm]
    with_tr = [t for t in pts if t['tech_review'] is not None]
    if with_tr:
        earliest = min(with_tr, key=lambda t: t['tech_review'])
        tr_str = str(earliest['tech_review']) if earliest['tech_review'] else '-'
        print(f'{pm:<10} {earliest["name"][:40]:<40} {tr_str:<16} {earliest["module"]:<10}')
    else:
        with_dates = [t for t in pts]  # Will add clarify_end check
        print(f'{pm:<10} {"(无首Task，全部由引擎生成)":<40} {"-":<16} {"-":<10}')

# Module priority validation
print(f'\n{"="*70}')
print('模块优先级验证')
print(f'{"="*70}')
issues = []
modules_used = set()
for t in task_info:
    if t['module']:
        modules_used.add(t['module'])
        if t['module'] not in MODULE_PRIORITY:
            issues.append(t['module'])

if issues:
    print(f'❌ 以下模块未定义优先级:')
    for m in sorted(issues):
        print(f'  - {m}')
    print('请在 MODULE_PRIORITY 中添加定义后重试')
else:
    print(f'✅ 所有模块优先级正常')
    for m in sorted(modules_used):
        print(f'  {m}: 优先级 {MODULE_PRIORITY.get(m, 99)}')

# Show subsequent task order
print(f'\n{"="*70}')
print('后续Task处理顺序（按模块优先级）')
print(f'{"="*70}')
for pm in sorted(pm_tasks.keys()):
    pts = pm_tasks[pm]
    anchor = next((t for t in pts if t['tech_review'] is not None and 
                   t['tech_review'] == min((x['tech_review'] for x in pts if x['tech_review'] is not None), default=None)), None)
    if not anchor:
        continue
    subs = [t for t in pts if t != anchor]
    subs.sort(key=lambda t: (MODULE_PRIORITY.get(t['module'], 99), pts.index(t)))
    if subs:
        chain = ' → '.join(f'{t["module"]}({MODULE_PRIORITY.get(t["module"],99)})' for t in subs)
        print(f'  {pm}: {chain}')
