"""生成报告：一期单独sheet，二期+三期单独sheet"""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from datetime import date, timedelta
from collections import defaultdict
from schedule_engine import MODULE_PRIORITY, WorkingDayCalendar

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
def day(fid): x=d(rec,fid); return date.fromisoformat(x) if x else None

with open('holidays.json') as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get('holidays',[]), hd.get('makeup_days',[]))

tasks = []
for rec in records:
    phase = s(rec, 'fldSuDoxFN')
    if phase not in ('一期','二期','三期'): continue
    if s(rec, 'fldp8xrC9I') == '是': continue
    if v(rec, 'fldF6tKrhx'): continue
    pm_v = v(rec, 'fldsoiimKC')
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    nm = (v(rec, 'fldD5A1CvQ') or '').replace(chr(10),' ').strip()
    mod = s(rec, 'fldPJrW9qs') or '-'
    iters = v(rec, 'fld5BFCMTn') or '-'
    dev_s = d(rec, 'fldu4esCvi')
    dev_e = d(rec, 'fldpXsPTvE')
    test_s = d(rec, 'fldlCFwt7C')
    test_e = d(rec, 'fld98ZTVMF')
    acc_s = d(rec, 'fldgv6n2rx')
    acc_e = d(rec, 'flduVm6fRo')
    tr = d(rec, 'fldMxPbqBt')
    dev_md = v(rec, 'fldOPeRb5q')
    test_md = v(rec, 'fldRWW5yQT')
    accept_md = v(rec, 'fldxIxJkPI')
    dev_slots = v(rec, 'fldCeLIjfG') or ''
    test_slots = v(rec, 'fldFaworUZ') or ''
    cs = d(rec, 'fldSRgjNL3')
    ce = d(rec, 'fldXvhBatN')
    ds = d(rec, 'fld5PTJ6XN')
    de = d(rec, 'fldaMhsTR5')
    rs = d(rec, 'fldJkPovnb')
    re_ = d(rec, 'fldRvi3Miv')
    
    def fmt(s, e):
        return f'{s}~{e}' if s and e else '-'
    
    tasks.append({
        'name': nm, 'pm': pm, 'module': mod, 'phase': phase,
        'iterations': str(iters),
        'req_phase': fmt(cs or ds or rs, re_ or de or ce),
        'review': tr or '-',
        'dev': fmt(dev_s, dev_e), 'test': fmt(test_s, test_e), 'accept': fmt(acc_s, acc_e),
        'dev_md': str(dev_md or ''), 'test_md': str(test_md or ''), 'accept_md': str(accept_md or ''),
        'dev_slots': dev_slots.replace('101','A').replace('102','B') if dev_slots else '-',
        'test_slots': test_slots or '-',
    })

mod_order = ["绩效", "人事", "考勤", "薪酬", "HRONE基础建设", "流程平台"]
mod_badges = dict(zip(mod_order, ["badge-P1","badge-P2","badge-P3","badge-P4","badge-P5","badge-P6"]))

def gen_phase_html(phase_name, phase_tasks):
    if not phase_tasks:
        return f'<div class="tab-pane" id="tab-{phase_name}"><p style="padding:20px;color:#999">暂无{phase_name}任务</p></div>'
    
    h = f'<div class="tab-pane" id="tab-{phase_name}">'
    
    # Stats
    total = len(phase_tasks)
    pms = set(t['pm'] for t in phase_tasks if t['pm'])
    h += f'<div class="summary-cards"><div class="card"><span class="card-num">{total}</span>有效任务</div><div class="card"><span class="card-num">{len(pms)}</span>位PM</div></div>'
    
    # By module
    for mod in mod_order:
        mod_tasks = [t for t in phase_tasks if t['module'] == mod]
        if not mod_tasks: continue
        # Sort by PM + dev_start
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
<th>任务</th><th>迭代数</th><th>技术评审</th><th>需求阶段</th>
<th>研发人天</th><th>投入研发</th><th>研发</th>
<th>测试人天</th><th>投入测试</th><th>测试</th>
<th>验收人天</th><th>验收</th><th>负责人</th>
</tr></thead><tbody>'''
        
        for t in mod_tasks:
            h += f'''<tr>
<td class="name-td" title="{t['name']}">{t['name']}</td>
<td style="text-align:center">{t['iterations']}</td>
<td>{t['review']}</td>
<td><small>{t['req_phase']}</small></td>
<td>{t['dev_md']}</td>
<td>{t['dev_slots']}</td>
<td><small>{t['dev']}</small></td>
<td>{t['test_md']}</td>
<td>{t['test_slots']}</td>
<td><small>{t['test']}</small></td>
<td>{t['accept_md']}</td>
<td><small>{t['accept']}</small></td>
<td><small>{t['pm']}</small></td>
</tr>'''
        
        h += '</tbody></table></div></div>'
    
    h += '</div>'
    return h

# Build HTML
p1 = [t for t in tasks if t['phase'] == '一期']
p23 = [t for t in tasks if t['phase'] in ('二期','三期')]

html = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HRAS 排期报告</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f6fa;padding:20px;color:#2d3436}
h1{font-size:22px;margin-bottom:4px}
.subtitle{font-size:13px;color:#636e72;margin-bottom:20px}

/* Tabs */
.tabs{display:flex;gap:0;margin-bottom:20px;border-bottom:2px solid #dfe6e9}
.tab-btn{padding:10px 24px;font-size:14px;cursor:pointer;border:none;background:transparent;color:#636e72;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .2s}
.tab-btn:hover{color:#2d3436}
.tab-btn.active{color:#0984e3;border-bottom-color:#0984e3;font-weight:600}
.tab-pane{display:none}
.tab-pane.active{display:block}

.summary-cards{display:flex;gap:12px;margin-bottom:16px}
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

.warn-tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;background:#ffeaa7;color:#d68910;margin:1px 0}
.info-tag{color:#b2bec3;font-size:11px}

.footer{text-align:center;padding:30px 0;font-size:12px;color:#b2bec3}
</style></head><body>
<h1>HRAS 排期报告</h1>
<p class="subtitle">数据源: HRAS敏捷项目管理 · 排期表 | 生成日期: 2026-07-16</p>

<div class="tabs">
<button class="tab-btn active" onclick="switchTab('一期')">一期</button>
<button class="tab-btn" onclick="switchTab('二-三期')">二/三期</button>
</div>

''' + gen_phase_html('一期', p1) + gen_phase_html('二-三期', p23) + '''

<script>
function toggleModule(el){var b=el.nextElementSibling;b.classList.toggle("collapsed");var i=el.querySelector(".toggle-icon");i.textContent=b.classList.contains("collapsed")?"▸":"▾"}
function switchTab(name){document.querySelectorAll(".tab-btn").forEach(function(b){b.classList.remove("active")});document.querySelectorAll(".tab-pane").forEach(function(p){p.classList.remove("active")});var idx=name==="一期"?0:1;document.querySelectorAll(".tab-btn")[idx].classList.add("active");document.getElementById("tab-"+name).classList.add("active")}
document.querySelectorAll(".tab-pane")[0].classList.add("active");
</script>
<div class="footer">Generated by HRAS Schedule Engine v3 · WorkBuddy</div>
</body></html>
'''

with open("schedule_report_phases.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"✅ 报告已生成: schedule_report_phases.html (一期 {len(p1)}个 + 二/三期 {len(p23)}个)")
