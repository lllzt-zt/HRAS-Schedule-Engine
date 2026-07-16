"""排期结果冲突检查：排期写回后执行，确保无PM时间重叠和模块顺序错误。"""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from schedule_engine import MODULE_PRIORITY
from datetime import date, timedelta
from collections import defaultdict

BASE = "BKBKbUWXtas7tSshzoccyZa3ndb"
TABLE = "tbl5jDThuT51h4II"

def main():
    passes = 0
    fails = 0
    
    # 读取数据
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

    # 收集所有非已完成的一/二/三期任务
    tasks_data = []
    for rec in records:
        phase = s(rec, 'fldSuDoxFN')
        if phase not in ('一期','二期','三期'): continue
        if s(rec, 'fldp8xrC9I') == '是': continue
        if v(rec, 'fldF6tKrhx'): continue
        
        pm_v = v(rec, 'fldsoiimKC')
        pm = pm_v[0].get('name','') if isinstance(pm_v,list) and pm_v else ''
        nm = (v(rec, 'fldD5A1CvQ') or '').replace(chr(10),' ').strip()
        mod = s(rec, 'fldPJrW9qs')
        
        def ds(fid):
            x = d(rec, fid)
            return date.fromisoformat(x) if x else None
        
        tasks_data.append({
            'name': nm, 'pm': pm, 'module': mod, 'phase': phase,
            'clarify_start': ds('fldSRgjNL3'), 'clarify_end': ds('fldXvhBatN'),
            'design_start': ds('fld5PTJ6XN'), 'design_end': ds('fldaMhsTR5'),
            'review_start': ds('fldJkPovnb'), 'review_end': ds('fldRvi3Miv'),
            'tech_review': ds('fldMxPbqBt'),
            'dev_start': ds('fldu4esCvi'), 'dev_end': ds('fldpXsPTvE'),
            'test_start': ds('fldlCFwt7C'), 'test_end': ds('fld98ZTVMF'),
            'accept_start': ds('fldgv6n2rx'), 'accept_end': ds('flduVm6fRo'),
        })
    
    print("=" * 80)
    print(f"排期结果冲突检查 — {len(tasks_data)}个任务")
    print("=" * 80)
    
    # ===== 检查1：PM时间重叠 =====
    print(f"\n▶ 检查1: PM时间重叠（分期展示）")
    total_overlaps = {"一期": 0, "二期": 0, "三期": 0}
    pm_groups = defaultdict(list)
    for td in tasks_data:
        pm_groups[td['pm']].append(td)
    
    for pm, tasks in sorted(pm_groups.items()):
        for phase in ("一期", "二期", "三期"):
            pts = [t for t in tasks if t['phase'] == phase]
            if not pts:
                continue
            req_periods = []
            for t in pts:
                req_start = t['clarify_start'] or t['design_start'] or t['review_start']
                req_end = t['review_end'] or t['design_end'] or t['clarify_end']
                if req_start and req_end:
                    req_periods.append((t['name'][:30], req_start, req_end))
            accept_periods = []
            for t in pts:
                if t['accept_start'] and t['accept_end']:
                    accept_periods.append((t['name'][:30], t['accept_start'], t['accept_end']))
            
            overlaps = 0
            for i in range(len(req_periods)):
                for j in range(i+1, len(req_periods)):
                    if req_periods[i][1] <= req_periods[j][2] and req_periods[j][1] <= req_periods[i][2]:
                        print(f"  {'🔹' if phase=='一期' else '❌'} {pm} [{phase}]: 需求 \"{req_periods[i][0]}\" ↔ \"{req_periods[j][0]}\"")
                        overlaps += 1
            for i in range(len(accept_periods)):
                for j in range(i+1, len(accept_periods)):
                    if accept_periods[i][1] <= accept_periods[j][2] and accept_periods[j][1] <= accept_periods[i][2]:
                        print(f"  {'🔹' if phase=='一期' else '❌'} {pm} [{phase}]: 验收 \"{accept_periods[i][0]}\" ↔ \"{accept_periods[j][0]}\"")
                        overlaps += 1
            for r in req_periods:
                for a in accept_periods:
                    if r[1] <= a[2] and a[1] <= r[2] and r[0] != a[0]:
                        print(f"  {'🔹' if phase=='一期' else '❌'} {pm} [{phase}]: 需求vs验收 \"{r[0]}\" ↔ \"{a[0]}\"")
                        overlaps += 1
            
            if overlaps == 0:
                print(f"  ✅ {pm} [{phase}]: 无时间重叠")
            total_overlaps[phase] += overlaps
    
    p2p3_overlaps = total_overlaps["二期"] + total_overlaps["三期"]
    total = sum(total_overlaps.values())
    if total > 0:
        print(f"  📌 一期({total_overlaps['一期']}处,🔹仅供参考,已固定) + 二/三期({p2p3_overlaps}处)")
        fails += p2p3_overlaps

    # ===== 检查2：模块优先级顺序 =====
    print(f"\n▶ 检查2: 模块优先级顺序（同PM同分期）")
    order_issues = 0
    for pm, tasks in sorted(pm_groups.items()):
        phase_groups = defaultdict(list)
        for t in tasks:
            phase_groups[t['phase']].append(t)
        
        for phase in ("一期", "二期", "三期"):
            pts = phase_groups.get(phase, [])
            if not pts:
                continue
            pts_sorted = [p for p in pts if p['dev_start']]
            pts_sorted.sort(key=lambda x: x['dev_start'])
            
            prev_pri = -1
            prev_name = ""
            for p in pts_sorted:
                pri = MODULE_PRIORITY.get(p['module'], 99)
                if pri < prev_pri:
                    icon = "🔹" if phase == "一期" else "❌"
                    print(f"  {icon} {pm} [{phase}]: \"{prev_name}\"({prev_pri}) → \"{p['name'][:20]}\"({pri}) 优先级倒置")
                    if phase != "一期":
                        order_issues += 1
                prev_pri = pri
                prev_name = p['name'][:20]
        
        if order_issues == 0:
            pass
    
    if order_issues == 0:
        print(f"  ✅ 所有PM二/三期模块顺序正确")
        passes += 1
    else:
        print(f"  ⚠ 二/三期共发现 {order_issues} 处顺序问题")
        fails += order_issues

    # ===== 检查3：阶段顺序合理性 =====
    print(f"\n▶ 检查3: 阶段顺序合理性")
    stage_issues = 0
    for t in tasks_data:
        issues = []
        # 澄清start ≤ 澄清end
        if t['clarify_start'] and t['clarify_end'] and t['clarify_start'] > t['clarify_end']:
            issues.append("澄清起>止")
        # 需求start ≤ 需求end
        if t['design_start'] and t['design_end'] and t['design_start'] > t['design_end']:
            issues.append("需求起>止")
        # 研发start ≤ 研发end
        if t['dev_start'] and t['dev_end'] and t['dev_start'] > t['dev_end']:
            issues.append("研发起>止")
        # 测试 ≤ 验收
        if t['test_start'] and t['test_end'] and t['test_start'] > t['test_end']:
            issues.append("测试起>止")
        if t['accept_start'] and t['accept_end'] and t['accept_start'] > t['accept_end']:
            issues.append("验收起>止")
        # 研发 < 测试 < 验收
        if t['dev_end'] and t['test_start'] and t['dev_end'] >= t['test_start']:
            issues.append("研发结束≥测试开始")
        if t['test_end'] and t['accept_start'] and t['test_end'] >= t['accept_start']:
            issues.append("测试结束≥验收开始")
        
        if issues:
            print(f"  ❌ {t['name'][:35]:35s}: {', '.join(issues)}")
            stage_issues += 1
    
    if stage_issues == 0:
        print(f"  ✅ 所有任务阶段顺序正确")
        passes += 1
    else:
        fails += stage_issues

    # ===== 汇总 =====
    print(f"\n" + "=" * 80)
    if fails == 0:
        print(f"✅ 全部通过！{passes}/{passes} 项检查通过，二/三期无冲突")
    else:
        print(f"⚠ 发现 {fails} 个问题（二/三期），请修复后重新排期")
    print("=" * 80)
    return fails

if __name__ == "__main__":
    sys.exit(main())
