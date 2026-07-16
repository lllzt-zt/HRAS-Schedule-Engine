"""Compare new table vs old schedule with better name matching."""
import subprocess, json, re
from difflib import SequenceMatcher

# ======== 1. Read new table ========
result = subprocess.run(
    'lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tbl5jDThuT51h4II --limit 200 --format json --as user',
    capture_output=True, text=True, timeout=30, shell=True
)
raw = json.loads(result.stdout)
new_recs = raw['data']['data']
fids = raw['data']['field_id_list']
idx = {f:i for i,f in enumerate(fids)}

def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

new_tasks = {}
for rec in new_recs:
    nm = v(rec,'fldD5A1CvQ') or ''
    if s(rec,'fldSuDoxFN') != '一期': continue
    done = s(rec,'fldp8xrC9I')
    if done == '是': continue
    if v(rec,'fldF6tKrhx'): continue
    nm_c = nm.replace('\n','').strip()
    pm_v = v(rec,'fldsoiimKC')
    pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
    new_tasks[nm_c] = {
        'name': nm_c, 'module': s(rec,'fldPJrW9qs'), 'pm': pm,
        'dev_s': d(rec,'fldu4esCvi') or '-', 'dev_e': d(rec,'fldpXsPTvE') or '-',
        'test_s': d(rec,'fldlCFwt7C') or '-', 'test_e': d(rec,'fld98ZTVMF') or '-',
        'accept_s': d(rec,'fldgv6n2rx') or '-', 'accept_e': d(rec,'flduVm6fRo') or '-',
    }

# ======== 2. Read old schedule from report ========
with open('schedule_report.html', 'r', encoding='utf-8') as f:
    html = f.read()
rows = re.findall(r'<tr>.*?</tr>', html, re.DOTALL)
old_tasks = {}
for row in rows:
    if 'name-td' not in row or 'module-header' in row: continue
    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
    if len(cells) < 11: continue
    name = re.sub(r'<[^>]+>', '', cells[0]).strip()
    name = re.sub(r'\s+v[\d.]+$', '', name)
    def pd(i):
        s = re.sub(r'<[^>]+>', '', cells[i]).strip()
        p = s.split('~')
        if len(p) == 2 and len(p[0].strip()) == 10 and len(p[1].strip()) == 10:
            return p[0].strip(), p[1].strip()
        return None, None
    ds, de = pd(5)
    ts, te = pd(8)
    aas, aae = pd(10)
    if ds: old_tasks[name] = {'name': name,
        'dev_s': ds, 'dev_e': de, 'test_s': ts, 'test_e': te,
        'accept_s': aas, 'accept_e': aae}

# ======== 3. Smart matching ========
def extract_core(name):
    """Extract core identifiers - module key + version + main keywords."""
    n = name.lower()
    # Remove punctuation
    n = re.sub(r'[【】\[\]|（）()、：:，。/\s]', ' ', n)
    # Key phrases that identify tasks
    cores = []
    for phrase in ['版本4','版本5','版本1','版本2','版本3','ai agent','绩效活动','绩效报表',
                    '绩效配置','绩效方案','流程引擎','ai发起公出','ai发起调班','调班','补签','加班',
                    '休假','出差','发薪','定薪','调薪','薪酬申诉','薪酬项目','班次','考勤',
                    '入职档案','入职信息','任职记录','晋升','见习','转正','多人审批','加签',
                    '设计器','合规','首页规划','ai适配']:
        if phrase in n:
            cores.append(phrase)
    return frozenset(cores)

old_by_core = {}
for k, v in old_tasks.items():
    c = extract_core(k)
    if c:
        for core in c:
            old_by_core[core] = v  # Last match wins for simplicity

def find_match(new_name):
    n = new_name.lower()
    # Direct substring match first
    for on, ot in old_tasks.items():
        # Check if they share substantial substrings
        on_lower = on.lower()
        # Remove punctuation for comparison
        on_clean = re.sub(r'[【】\[\]|（）()、：:，。/\s]', '', on_lower)
        nn_clean = re.sub(r'[【】\[\]|（）()、：:，。/\s]', '', n)
        ratio = SequenceMatcher(None, on_clean, nn_clean).ratio()
        if ratio > 0.4:
            return ot, ratio
    # Fallback to core matching
    cores = extract_core(new_name)
    for core in cores:
        if core in old_by_core:
            return old_by_core[core], 0.5
    return None, 0

# ======== 4. Compare ========
print(f"{'新表任务名':<40} {'研发(新→旧)':<30} {'测试(新→旧)':<30} {'验收(新→旧)':<30}")
print('=' * 130)
diff_count = 0
match_count = 0

for nm in sorted(new_tasks.keys()):
    nt = new_tasks[nm]
    match, score = find_match(nm)
    
    nd = f"{nt['dev_s']}~{nt['dev_e']}"
    nts = f"{nt['test_s']}~{nt['test_e']}"
    na = f"{nt['accept_s']}~{nt['accept_e']}"
    
    if match:
        match_count += 1
        od = f"{match['dev_s']}~{match['dev_e']}"
        ots = f"{match['test_s']}~{match['test_e']}"
        oa = f"{match['accept_s']}~{match['accept_e']}"
        
        d_diff = '←' if nd != od else '='
        t_diff = '←' if nts != ots else '='
        a_diff = '←' if na != oa else '='
        
        if d_diff != '=' or t_diff != '=' or a_diff != '=':
            diff_count += 1
            print(f"{nm[:38]:40s} {nd:<14s}{d_diff}{od:<15s} {nts:<14s}{t_diff}{ots:<15s} {na:<14s}{a_diff}{oa:<15s}")
        else:
            print(f"{nm[:38]:40s} ✅ {nd:<14s} {nts:<14s} {na:<14s}")
    else:
        print(f"{nm[:38]:40s} ❓ 未匹配旧任务 - {nd}")

print(f"\n匹配: {match_count}, 有差异: {diff_count}")
