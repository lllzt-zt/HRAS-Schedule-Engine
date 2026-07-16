"""生成二期/三期独立报告，从表数据直接读取，使用引擎标准样式"""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from datetime import date
from collections import defaultdict
from schedule_engine import MODULE_PRIORITY

BASE = "BKBKbUWXtas7tSshzoccyZa3ndb"
TABLE = "tbl5jDThuT51h4II"

result = subprocess.run(
    f'lark-cli base +record-list --base-token {BASE} --table-id {TABLE} --limit 200 --format json --as user',
    capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw['data']['data']
fids = raw['data']['field_id_list']
idx = {f:i for i,f in enumerate(fids)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''
def n(rec,fid): x=v(rec,fid); return float(x) if x else 0

# Collect Phase 2/3 tasks
tasks = []
for rec in records:
    phase = s(rec, 'fldSuDoxFN')
    if phase not in ('二期','三期'): continue
    if s(rec, 'fldp8xrC9I') == '是': continue
    if v(rec, 'fldF6tKrhx'): continue
    pm_v = v(rec, 'fldsoiimKC')
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    nm = (v(rec, 'fldD5A1CvQ') or '').replace(chr(10),' ').strip()
    mod = s(rec, 'fldPJrW9qs')
    
    def fmt(s, e):
        return f'{s}~{e}' if s and e else '-'
    
    dev_s = d(rec, 'fldu4esCvi')
    dev_e = d(rec, 'fldpXsPTvE')
    test_s = d(rec, 'fldlCFwt7C')
    test_e = d(rec, 'fld98ZTVMF')
    acc_s = d(rec, 'fldgv6n2rx')
    acc_e = d(rec, 'flduVm6fRo')
    
    cs = d(rec, 'fldSRgjNL3')
    ce = d(rec, 'fldXvhBatN')
    ds = d(rec, 'fld5PTJ6XN')
    de = d(rec, 'fldaMhsTR5')
    rs = d(rec, 'fldJkPovnb')
    re_ = d(rec, 'fldRvi3Miv')
    
    dev_slots = (v(rec, 'fldCeLIjfG') or '').replace('101','A').replace('102','B')
    test_slots = v(rec, 'fldFaworUZ') or ''
    
    # Compute man-days
    dev_md = n(rec, 'fldOPeRb5q')
    test_md = n(rec, 'fldRWW5yQT')
    acc_md = n(rec, 'fldxIxJkPI')
    
    iters = v(rec, 'fld5BFCMTn') or '-'
    
    tasks.append({
        'name': nm, 'pm': pm, 'module': mod, 'phase': phase,
        'iterations': str(iters),
        'req_phase': fmt(cs or ds or rs, re_ or de or ce),
        'review': d(rec, 'fldMxPbqBt') or '-',
        'dev': fmt(dev_s, dev_e),
        'test': fmt(test_s, test_e),
        'accept': fmt(acc_s, acc_e),
        'dev_md': f'{dev_md:.0f}' if dev_md else '0',
        'test_md': f'{test_md:.1f}' if test_md else '0',
        'acc_md': f'{acc_md:.1f}' if acc_md else '0',
        'dev_slots': dev_slots or '-',
        'test_slots': test_slots or '-',
    })

# Calculate timeline
all_dates = []
for t in tasks:
    for field in ['dev','test','accept']:
        d = t[field]
        if d != '-':
            parts = d.split('~')
            for p in parts:
                p = p.strip()
                if len(p) == 10:
                    try: all_dates.append(date.fromisoformat(p))
                    except: pass
period_start = min(all_dates) if all_dates else date(2026,9,1)
period_end = max(all_dates) if all_dates else date(2027,1,31)

# Collect resource utilization data
dev_days = defaultdict(set)  # slot -> set of occupied dates
test_days = defaultdict(set)
for t in tasks:
    for phase_field, slot_field in [('dev','dev_slots'), ('test','test_slots')]:
        d_str = t[phase_field]
        slots_str = t[slot_field]
        if d_str == '-' or slots_str == '-': continue
        parts = d_str.split('~')
        if len(parts) != 2: continue
        try:
            start = date.fromisoformat(parts[0].strip())
            end = date.fromisoformat(parts[1].strip())
            import calendar
            from datetime import timedelta
            d = start
            while d <= end:
                if d.weekday() < 5:  # simple weekday check (ignoring holidays)
                    for slot in slots_str.split(','):
                        slot = slot.strip()
                        if slot:
                            dev_days[slot].add(d) if phase_field == 'dev' else test_days[slot].add(d)
                d += timedelta(days=1)
        except: pass

total_wd = sum(1 for n in range((period_end - period_start).days + 1)
               if (period_start + timedelta(days=n)).weekday() < 5)

# ===== Generate HTML =====
mod_order = ["绩效", "人事", "考勤", "薪酬", "HRONE基础建设", "流程平台"]
mod_badges = dict(zip(mod_order, ["badge-P1","badge-P2","badge-P3","badge-P4","badge-P5","badge-P6"]))

h = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HRAS 排期报告 · 二期/三期</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f6fa;padding:20px;color:#2d3436}
h1{font-size:22px;margin-bottom:4px}
.subtitle{font-size:13px;color:#636e72;margin-bottom:20px}
.summary-cards{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.card{background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:13px;color:#636e72}
.card-num{font-size:24px;font-weight:700;color:#2d3436;display:block;margin-bottom:2px}

.module-section{margin-bottom:14px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.module-header{display:flex;align-items:center;padding:12px 16px;cursor:pointer;user-select:none;transition:background .15s}
.module-header:hover{background:#f8f9ff}
.module-header .badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;margin-right:10px}
.badge-P1{background:#e17055}.badge-P2{background:#00b894}.badge-P3{background:#0984e3}
.badge-P4{background:#6c5ce7}.badge-P5{background:#e84393}.badge-P6{background:#636e72}
.module-header .count{font-size:12px;color:#636e72;margin-right:auto}
.module-header .toggle-icon{color:#b2bec3;font-size:12px;transition:transform .2s}
.module-body{overflow-x:auto;border-top:1px solid #f0f0f0}
.module-body.collapsed{display:none}

table{width:100%;border-collapse:collapse;font-size:12px;min-width:900px}
th{background:#f8f9fa;padding:8px 10px;text-align:left;font-size:11px;color:#636e72;font-weight:600;border-bottom:2px solid #dfe6e9;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f0f0f0}
tr:hover{background:#f8f9ff}
.name-td{font-weight:500;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
small{font-size:11px;color:#636e72}

.pool-section{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:20px;margin-bottom:16px}
.pool-section h2{font-size:18px;color:#2d3436;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #f0f0f0}
.pool-section h3{font-size:14px;color:#2d3436;margin:20px 0 10px 0}

.util-table{width:100%;border-collapse:collapse;font-size:13px}
.util-table th{background:#f8f9fa;padding:8px 10px;text-align:left;font-size:11px;color:#636e72;font-weight:600;border-bottom:2px solid #dfe6e9;white-space:nowrap}
.util-table td{padding:8px 10px;border-bottom:1px solid #f0f0f0;font-size:12px}
.util-table tr:hover{background:#f8f9ff}

.footer{text-align:center;padding:30px 0;font-size:12px;color:#b2bec3}
</style></head><body>
<h1>HRAS 排期报告 · 二/三期</h1>
<p class="subtitle">统计周期: ''' + str(period_start) + ''' ~ ''' + str(period_end) + ''' (共''' + str(total_wd) + '''个工作日) | 生成日期: 2026-07-16</p>

<div class="summary-cards">
<div class="card"><span class="card-num">''' + str(len(tasks)) + '''</span>有效任务</div>
<div class="card"><span class="card-num">''' + str(len(set(t['pm'] for t in tasks))) + '''</span>位PM</div>
<div class="card"><span class="card-num">''' + str(len(set(t['module'] for t in tasks))) + '''</span>个模块</div>
<div class="card">二期''' + str(sum(1 for t in tasks if t['phase']=='二期')) + ''' | 三期''' + str(sum(1 for t in tasks if t['phase']=='三期')) + '''</div>
</div>
'''

# Task tables by module
for mod in mod_order:
    mod_tasks = [t for t in tasks if t['module'] == mod]
    if not mod_tasks: continue
    mod_tasks.sort(key=lambda x: (x['pm'], x['dev'] if x['dev'] != '-' else ''))
    
    h += f'''<div class="module-section">
<div class="module-header" onclick="toggleModule(this)">
  <span class="badge {mod_badges[mod]}">{mod}</span>
  <span class="count">{len(mod_tasks)}条</span>
  <span class="toggle-icon">▾</span>
</div>
<div class="module-body">
<table>
<thead><tr>
<th>任务</th><th>分期</th><th>迭代数</th><th>技术评审</th><th>需求阶段</th>
<th>研发人天</th><th>投入研发</th><th>研发</th>
<th>测试人天</th><th>投入测试</th><th>测试</th>
<th>验收人天</th><th>验收</th><th>负责人</th>
</tr></thead><tbody>'''

    for t in mod_tasks:
        h += f'''<tr>
<td class="name-td" title="{t['name']}">{t['name']}</td>
<td style="text-align:center">{t['phase']}</td>
<td style="text-align:center">{t['iterations']}</td>
<td>{t['review']}</td>
<td><small>{t['req_phase']}</small></td>
<td>{t['dev_md']}</td>
<td>{t['dev_slots']}</td>
<td><small>{t['dev']}</small></td>
<td>{t['test_md']}</td>
<td>{t['test_slots']}</td>
<td><small>{t['test']}</small></td>
<td>{t['acc_md']}</td>
<td><small>{t['accept']}</small></td>
<td><small>{t['pm']}</small></td>
</tr>'''
    
    h += '</tbody></table></div></div>'

# Resource utilization
h += '''<div class="pool-section">
<h2>研发资源负载</h2>
<table class="util-table">
<thead><tr><th>槽位</th><th>占用(工作日)</th><th>利用率</th></tr></thead><tbody>'''

all_dev_slots = sorted(set(list(dev_days.keys()) + ['1','2','3','4','5','6','A','B']))
for slot in all_dev_slots:
    busy = len(dev_days.get(slot, set()))
    ratio = f"{busy/max(1,total_wd)*100:.0f}%" if total_wd else "0%"
    h += f'<tr><td>{slot}</td><td>{busy}</td><td>{ratio}</td></tr>'

h += '''</tbody></table></div>

<div class="pool-section">
<h2>测试资源负载</h2>
<table class="util-table">
<thead><tr><th>槽位</th><th>占用(工作日)</th><th>利用率</th></tr></thead><tbody>'''

for slot in ['1','2','3','4']:
    busy = len(test_days.get(slot, set()))
    ratio = f"{busy/max(1,total_wd)*100:.0f}%" if total_wd else "0%"
    h += f'<tr><td>{slot}</td><td>{busy}</td><td>{ratio}</td></tr>'

h += '''</tbody></table></div>

<div class="pool-section">
<h2>产品负责人验收分布</h2>
<table class="util-table">
<thead><tr><th>PM</th><th>验收任务数</th><th>验收总耗时(工作日)</th><th>验收时间段</th></tr></thead><tbody>'''

pm_accept = defaultdict(list)
for t in tasks:
    if t['accept'] != '-':
        parts = t['accept'].split('~')
        if len(parts) == 2:
            try:
                s = date.fromisoformat(parts[0].strip())
                e = date.fromisoformat(parts[1].strip())
                wd = sum(1 for n in range((e-s).days+1) if (s+timedelta(days=n)).weekday()<5)
                pm_accept[t['pm']].append((t['name'][:25], s, e, wd))
            except: pass

for pm in sorted(pm_accept.keys()):
    items = pm_accept[pm]
    total_wd = sum(i[3] for i in items)
    min_s = min(i[1] for i in items)
    max_e = max(i[2] for i in items)
    h += f'<tr><td>{pm}</td><td>{len(items)}</td><td>{total_wd}</td><td><small>{min_s} ~ {max_e}</small></td></tr>'

h += '''</tbody></table></div>

<script>
function toggleModule(el){var b=el.nextElementSibling;b.classList.toggle("collapsed");var i=el.querySelector(".toggle-icon");i.textContent=b.classList.contains("collapsed")?"▸":"▾"}
</script>
<div class="footer">Generated by HRAS Schedule Engine v3 · WorkBuddy</div>
</body></html>'''

with open('schedule_report_p23.html', 'w', encoding='utf-8') as f:
    f.write(h)

print(f"✅ 二/三期报告已生成: schedule_report_p23.html")
print(f"   {len(tasks)}个任务 | 周期: {period_start} ~ {period_end}")
print(f"   研发槽位: {len(all_dev_slots)}个 | 测试槽位: 4个")
