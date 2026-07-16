"""Generate PM workload timeline report with conflict detection."""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from schedule_engine import Task, WorkingDayCalendar, MODULE_PRIORITY, schedule_all, load_feishu_config, DEFAULT_FEISHU_FIELDS
from datetime import date, timedelta
from collections import defaultdict

today = date(2026, 7, 16)
with open("holidays.json") as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get("holidays", []), hd.get("makeup_days", []))
F = load_feishu_config("feishu_config.json")

result = subprocess.run(
    'lark-cli base +record-list --base-token BKBKbUWXtas7tSshzoccyZa3ndb --table-id tblGyhmFz9YQH2Z1 --limit 200 --format json --as user',
    capture_output=True, text=True, timeout=30, shell=True
)
raw = json.loads(result.stdout)
records = raw["data"]["data"]
fid_list = raw["data"]["field_id_list"]
rid_list = raw["data"]["record_id_list"]
idx = {fid: i for i, fid in enumerate(fid_list)}

def _val(rec, fid):
    i = idx.get(fid)
    return rec[i] if i is not None and i < len(rec) else None
def _num(rec, fid, d=0):
    v = _val(rec, fid)
    if v is None: return d
    try: return float(v)
    except: return d
def _date_str(rec, fid):
    v = _val(rec, fid)
    if v and isinstance(v, str): return v[:10]
    return None
def _select(rec, fid):
    v = _val(rec, fid)
    if v and isinstance(v, list) and len(v) > 0: return v[0]
    return ""

td_list = []
for i, rec in enumerate(records):
    cv = _val(rec, F.get("reserved_canceled", "fldF6tKrhx"))
    if cv and isinstance(cv, list) and len(cv) > 0 and cv[0] == "是": continue
    if _select(rec, F.get("phase", "fldSuDoxFN")) != "一期": continue
    pm_v = _val(rec, F.get("product_owner", "fldsoiimKC"))
    pm_name = pm_v[0].get("name", "") if isinstance(pm_v, list) and pm_v else ""
    td_list.append({
        "record_id": rid_list[i] if i < len(rid_list) else "",
        "name": _val(rec, F.get("name", "fldD5A1CvQ")) or "",
        "module": _select(rec, F.get("module", "fldPJrW9qs")),
        "tech_review": _date_str(rec, F.get("tech_review", "fldMxPbqBt")),
        "clarify_start": _date_str(rec, F.get("clarify_start", "fldSRgjNL3")),
        "clarify_end": _date_str(rec, F.get("clarify_end", "fldXvhBatN")),
        "tech_review_start": _date_str(rec, F.get("tech_review_start", "fldJkPovnb")),
        "tech_review_end": _date_str(rec, F.get("tech_review_end", "fldRvi3Miv")),
        "design_start": _date_str(rec, F.get("design_start", "fld5PTJ6XN")),
        "design_end": _date_str(rec, F.get("design_end", "fldaMhsTR5")),
        "iterations": _num(rec, F.get("iterations", "fld5BFCMTn"), 1),
        "dev_count": 2, "test_count": 2,
        "dev_product_ratio": str(_val(rec, F.get("dev_product_ratio", "fldFPiQasw")) or ""),
        "dev_test_ratio": str(_val(rec, F.get("dev_test_ratio", "flduOhVcYR")) or ""),
        "product_clarify_md": _num(rec, F.get("product_clarify_md", "fld0fMXr6m"), 0),
        "demand_md": _num(rec, F.get("demand_md", "fldhE38q8v"), 0),
        "review_md": _num(rec, F.get("tech_review_md", "fldm3qBBJx"), 0),
        "pm_name": pm_name, "dev_slots": "", "test_slots": "",
        "dev_man_days": 0, "original_index": i,
    })

tasks = []
for td in td_list:
    t = Task.from_dict(td)
    t.original_index = td.get("original_index", 999)
    t.product_clarify_md = td["product_clarify_md"]
    t.demand_md = td["demand_md"]
    t.review_md = td["review_md"]
    t.dev_test_ratio = td.get("dev_test_ratio", "")
    tasks.append(t)

pm_tl = defaultdict(list)
for t in tasks: pm_tl[t.pm_name or "_none"].append(t)
for pm, pts in pm_tl.items():
    with_tr = [pp for pp in pts if pp.tech_review is not None]
    if with_tr: min(with_tr, key=lambda x: x.tech_review).is_anchor = True

active = [t for t in tasks if not (t.is_anchor and t.tech_review and t.tech_review < today)]
for t in active:
    if t.is_anchor and t.tech_review is None and t.design_end:
        if t.review_md and t.review_md > 0:
            rs = cal.next_working_day(t.design_end)
            re = cal.add_working_days(rs, math.ceil(t.review_md))
            t.tech_review = cal.next_working_day(re)
        else: t.tech_review = cal.next_working_day(t.design_end)
    if t.is_anchor and t.clarify_start is None and t.design_start:
        tc = max(1, math.ceil(t.iterations * 3))
        ce = cal.prev_working_day(t.design_start); cs = ce
        for _ in range(tc - 1): cs = cal.prev_working_day(cs)
        t.clarify_start = cs; t.clarify_end = ce
    if not t.is_anchor: t.tech_review = None

sched = schedule_all(active, cal, today, max_gap_days=5)
v4 = next((t for t in sched if "版本4" in t.name), None)
v5 = next((t for t in sched if "版本5" in t.name), None)
if v4 and v5 and v4.new_dev_end and v5.new_dev_start:
    md5 = cal.next_working_day(v4.new_dev_end)
    if v5.tech_review: md5 = max(md5, cal.next_working_day(v5.tech_review))
    if v5.new_dev_start < md5:
        v5.new_dev_start = md5; v5.new_dev_end = cal.add_working_days(md5, v5.dev_wd)

# Collect PM periods
pm_periods = defaultdict(list)
TYPE_MAP = [
    ("澄清", "new_clarify_start", "new_clarify_end", "clarify_start", "clarify_end"),
    ("需求", "new_req_start", "new_req_end", "design_start", "design_end"),
    ("评审", "new_review_start", "new_review_end", "tech_review_start", "tech_review_end"),
]

for t in sched:
    if not t.pm_name: continue
    for label, ns, ne, os, oe in TYPE_MAP:
        s = getattr(t, ns, None) or getattr(t, os, None)
        e = getattr(t, ne, None) or getattr(t, oe, None)
        if s and e and s <= e:
            span_wd = sum(1 for n in range((e - s).days + 1) if cal.is_working_day(s + timedelta(days=n)))
            if label == "澄清":
                # 引擎逻辑: total=ceil(iter×3), 前1/3非PM, 后2/3PM
                total_cal = max(1, math.ceil(t.iterations * 3))
                non_pm_wd = max(1, math.ceil(total_cal / 3))
                pm_clarify_wd = total_cal - non_pm_wd
                pm_start = cal.add_working_days(s, non_pm_wd)
                pm_clarify_end = cal.add_working_days(pm_start, pm_clarify_wd - 1)
                # 前段(非PM): 展示但不计入PM冲突
                pm_periods[t.pm_name].append({
                    "task": t.name[:35], "module": t.module, "type": "澄清(前)",
                    "sub": "非PM投入", "start": s, "end": cal.prev_working_day(pm_start) if pm_start > s else s,
                    "span_wd": non_pm_wd, "pm_wd": 0, "is_partial": True, "display_only": True
                })
                # 后段(PM实际投入): 正常计入冲突
                pm_periods[t.pm_name].append({
                    "task": t.name[:35], "module": t.module, "type": "澄清(PM)",
                    "sub": "PM投入", "start": pm_start, "end": pm_clarify_end,
                    "span_wd": pm_clarify_wd, "pm_wd": pm_clarify_wd, "is_partial": False, "display_only": False
                })
            elif label == "需求":
                pm_wd = t.demand_md or span_wd
                pm_periods[t.pm_name].append({
                    "task": t.name[:35], "module": t.module, "type": label,
                    "sub": "", "start": s, "end": e,
                    "span_wd": span_wd, "pm_wd": pm_wd, "is_partial": False, "display_only": False
                })
            elif label == "评审":
                pm_wd = t.review_md or span_wd
                pm_periods[t.pm_name].append({
                    "task": t.name[:35], "module": t.module, "type": label,
                    "sub": "", "start": s, "end": e,
                    "span_wd": span_wd, "pm_wd": pm_wd, "is_partial": False, "display_only": False
                })
    if t.new_accept_start and t.new_accept_end:
        span_wd = sum(1 for n in range((t.new_accept_end - t.new_accept_start).days + 1)
                      if cal.is_working_day(t.new_accept_start + timedelta(days=n)))
        pm_periods[t.pm_name].append({
            "task": t.name[:35], "module": t.module, "type": "验收",
            "sub": "", "start": t.new_accept_start, "end": t.new_accept_end,
            "span_wd": span_wd, "pm_wd": span_wd, "is_partial": False, "display_only": False
        })

# Detect conflicts: same PM, overlapping date ranges
# 澄清阶段PM仅为部分投入(product_clarify_md)，重叠不一定是真冲突
def has_overlap(a, b):
    return a["start"] <= b["end"] and b["start"] <= a["end"]

def is_real_conflict(a, b):
    """判断两个时间段是否构成真实冲突。
    如果两者都是部分投入(澄清)，冲突可能不成立。
    如果一方是部分投入另一方是全投入，仅当全投入时段重叠才计为冲突。"""
    if not has_overlap(a, b):
        return False, 0
    cs = max(a["start"], b["start"])
    ce = min(a["end"], b["end"])
    overlap_wd = sum(1 for n in range((ce - cs).days + 1) if cal.is_working_day(cs + timedelta(days=n)))
    if overlap_wd <= 0:
        return False, 0
    # 如果两个都是部分投入且PM总工时<1.0 → 不是真冲突
    if a.get("is_partial") and b.get("is_partial"):
        # 两者同时段总PM工作日 < 该时段日历天数 → 可能不冲突
        total_pm_wd = (a.get("pm_wd", 0) + b.get("pm_wd", 0)) * overlap_wd / max(1, (a["end"] - a["start"]).days + 1)
        if total_pm_wd < overlap_wd * 0.5:  # PM总投入 < 50%时间
            return False, overlap_wd
    return True, overlap_wd

all_conflicts = []
for pm, periods in pm_periods.items():
    pm_only = [p for p in periods if not p.get("display_only")]
    pm_only.sort(key=lambda x: x["start"])
    for i in range(len(pm_only)):
        for j in range(i + 1, len(pm_only)):
            is_real, overlap_wd = is_real_conflict(pm_only[i], pm_only[j])
            if is_real:
                conflict_start = max(periods[i]["start"], periods[j]["start"])
                conflict_end = min(periods[i]["end"], periods[j]["end"])
                all_conflicts.append({
                    "pm": pm,
                    "a": periods[i],
                    "b": periods[j],
                    "start": conflict_start,
                    "end": conflict_end,
                    "wd": overlap_wd,
                })

all_conflicts.sort(key=lambda x: (x["pm"], x["start"]))

# Build HTML
TYPE_COLORS = {
    "澄清(前)": ("#f0f0f0", "#b2bec3"), "澄清(PM)": ("#dfe6fd", "#0984e3"),
    "需求": ("#e8dffd", "#6c5ce7"),
    "评审": ("#fde0ec", "#e84393"), "验收": ("#d4f5e8", "#00b894"),
}
PM_COLORS = ["#e17055", "#e84393", "#0984e3", "#00b894", "#6c5ce7", "#d63031"]
PM_NAMES = ["刘观福", "梁景悦", "肖维", "许大庆", "谢蓉", "谭雨薇"]

total_start = date.max if all_conflicts else today
total_end = date.min if all_conflicts else today
all_periods = [p for plist in pm_periods.values() for p in plist]
for p in all_periods:
    if p["start"] < total_start: total_start = p["start"]
    if p["end"] > total_end: total_end = p["end"]
total_wd = sum(1 for n in range((total_end - total_start).days + 1)
               if cal.is_working_day(total_start + timedelta(days=n)))

STYLE = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e;padding:32px 40px}
h1{font-size:24px;font-weight:600;margin-bottom:4px;letter-spacing:-0.3px}
.subtitle{font-size:14px;color:#636e72;margin-bottom:28px}
.conflict-banner{border-radius:12px;padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px}
.conflict-banner.ok{background:#d4f5e8;border:1px solid #00b894;color:#006644}
.conflict-banner.warn{background:#fff3e0;border:1px solid #e17055;color:#993c1d}
.conflict-banner .icon{font-size:24px}
.conflict-banner .text{font-size:14px;font-weight:500}
.conflict-banner .detail{font-size:12px;color:#636e72}
.conflict-list{display:flex;flex-direction:column;gap:8px;margin:12px 0 20px 0}
.conflict-item{background:#fff5f5;border:1px solid #fcc;border-radius:8px;padding:10px 14px;display:flex;align-items:center;gap:12px;font-size:13px}
.conflict-item .pm-tag{font-weight:600;color:#e17055}
.conflict-item .date-tag{color:#636e72;font-size:12px}
.conflict-item .task-tag{color:#2d3436}
.conflict-item .wd-tag{background:#e17055;color:#fff;border-radius:8px;padding:1px 8px;font-size:11px;font-weight:600;margin-left:auto}
.pm-card{background:#fff;border-radius:16px;padding:24px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.pm-card.has-conflict{border:2px solid #e17055;box-shadow:0 2px 12px rgba(225,112,85,.15)}
.pm-header{display:flex;align-items:center;gap:12px;margin-bottom:20px}
.pm-avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;color:#fff}
.pm-title{font-size:18px;font-weight:600}
.pm-stats{font-size:13px;color:#636e72;margin-left:auto}
.stats-item{display:inline-block;margin-left:16px}
.stats-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.conflict-tag{display:inline-block;background:#e17055;color:#fff;font-size:11px;font-weight:600;padding:2px 10px;border-radius:12px;margin-left:8px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:4px}
table th{padding:10px 12px;text-align:left;font-size:11px;color:#636e72;font-weight:600;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #edf2f7}
table td{padding:10px 12px;border-bottom:1px solid #f0f2f5;vertical-align:middle}
table tr:hover td{background:#f8f9ff}
table tr.conflict-row td{background:#fff5f5}
table tr.conflict-row td:first-child{border-left:3px solid #e17055}
.type-badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;color:#fff}
.task-info{display:flex;flex-direction:column}
.task-name{font-weight:500;font-size:13px}
.task-module{font-size:11px;color:#636e72}
.date-range{font-size:12px;color:#2d3436;font-variant-numeric:tabular-nums}
.wd-num{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.footer{text-align:center;padding:24px;color:#b2bec3;font-size:12px}
@keyframes pulse{0%{opacity:1}50%{opacity:.6}100%{opacity:1}}
.pulsing{animation:pulse 1.5s ease-in-out 3}
"""

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>PM 投入时间线 · 冲突分析</title><style>{STYLE}</style></head><body>
<h1>PM 投入时间线 · 冲突分析</h1>
<p class="subtitle">周期: {total_start} ~ {total_end} (共{total_wd}个工作日) | 灰色=澄清前段(非PM) / 蓝=澄清后段(PM) / 紫=需求 / 粉=评审 / 绿=验收</p>
<div style="display:flex;gap:20px;margin-bottom:8px;flex-wrap:wrap">"""

for tname, (light, dark) in TYPE_COLORS.items():
    label_map = {"澄清(前)": "澄清(非PM)", "澄清(PM)": "澄清(PM)"}
    display = label_map.get(tname, tname)
    html += f'<span style="display:flex;align-items:center;gap:6px;font-size:13px"><span style="width:14px;height:14px;background:{dark};border-radius:4px"></span>{display}</span>'
html += "</div>"

html += """<div style="background:#edf4ff;border:1px solid #b8d4fe;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#1a3d6e;display:flex;align-items:flex-start;gap:8px">
<span style="font-size:18px;flex-shrink:0">💡</span>
<div><strong>澄清阶段PM投入规则</strong><br>
引擎规则：澄清总工期 = ceil(迭代数 × 3)，其中<strong>前1/3为非PM时间</strong>（灰），<strong>后2/3为PM时间</strong>（蓝）。<br>
报告中的<strong>澄清(前)</strong>灰色部分不计入PM冲突检测，<strong>澄清(PM)</strong>蓝色部分与其他PM阶段重叠才计为冲突。</div>
</div>"""

# Conflict summary banner
if not all_conflicts:
    html += """<div class="conflict-banner ok"><span class="icon">✓</span>
<div><div class="text">未发现时间冲突</div><div class="detail">所有PM的时间段均无重叠</div></div></div>"""
else:
    involved_pms = set(c["pm"] for c in all_conflicts)
    total_conflict_wd = sum(c["wd"] for c in all_conflicts)
    html += f"""<div class="conflict-banner warn"><span class="icon">⚠</span>
<div><div class="text">发现 {len(all_conflicts)} 处时间冲突</div>
<div class="detail">涉及 {len(involved_pms)} 位PM · 总冲突 {total_conflict_wd} 个工作日</div></div></div>
<div class="conflict-list">"""
    for c in all_conflicts:
        html += f"""<div class="conflict-item">
<span class="pm-tag">{c['pm']}</span>
<span class="task-tag">{c['a']['type']}: {c['a']['task'][:25]} ↔ {c['b']['type']}: {c['b']['task'][:25]}</span>
<span class="date-tag">{c['start']} ~ {c['end']}</span>
<span class="wd-tag">冲突{c['wd']}天</span>
</div>"""
    html += "</div>"

# Per-PM cards
for pi, pm in enumerate(PM_NAMES):
    items = sorted(pm_periods.get(pm, []), key=lambda x: x["start"])
    if not items: continue
    color = PM_COLORS[pi % len(PM_COLORS)]
    pm_conflicts = [c for c in all_conflicts if c["pm"] == pm]
    has_cf = len(pm_conflicts) > 0
    cf_str = f'<span class="conflict-tag">⚠ {len(pm_conflicts)}处冲突</span>' if has_cf else ""

    total_by_type = defaultdict(int)
    for it in items:
        total_by_type[it["type"]] += it.get("span_wd", 0)

    html += f"""<div class="pm-card {'has-conflict' if has_cf else ''}">
<div class="pm-header">
<div class="pm-avatar" style="background:{color}">{pm[0]}</div>
<span class="pm-title">{pm}{cf_str}</span>
<span class="pm-stats">"""
    for tname in ["澄清(前)", "澄清(PM)", "需求", "评审", "验收"]:
        wd = sum(it.get("span_wd", 0) for it in items if it["type"] == tname)
        pm_wd = sum(it.get("pm_wd", 0) for it in items if it["type"] == tname)
        _, dark = TYPE_COLORS.get(tname, ("#eee", "#636e72"))
        if "澄清" in tname:
            label = "澄清(非PM)" if "前" in tname else "澄清(PM)"
            html += f'<span class="stats-item"><span class="stats-dot" style="background:{dark}"></span>{label}={wd}wd</span>'
        else:
            html += f'<span class="stats-item"><span class="stats-dot" style="background:{dark}"></span>{tname}={wd}wd</span>'
    html += "</span></div>"

    if has_cf:
        html += """<div style="background:#fff5f5;border:1px solid #fcc;border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px">"""
        for c in pm_conflicts:
            html += f"""<div style="display:flex;align-items:center;gap:8px;padding:4px 0">
<span style="font-weight:600;color:#e17055;font-size:12px">{c['a']['type']}</span>
<span>{c['a']['task'][:25]}</span>
<span style="color:#636e72;font-size:12px">↔</span>
<span style="font-weight:600;color:#e17055;font-size:12px">{c['b']['type']}</span>
<span>{c['b']['task'][:25]}</span>
<span style="color:#e17055;font-weight:600;margin-left:auto">{c['start']}~{c['end']} ({c['wd']}天)</span>
</div>"""
        html += "</div>"

    html += """<table><thead><tr>
<th style="width:26%">任务项</th><th style="width:8%">类型</th><th style="width:30%">投入时间段</th><th style="width:10%;text-align:right">投入/总天数</th>
</tr></thead><tbody>"""

    # Mark conflict rows
    conflict_ranges = [(c["start"], c["end"]) for c in pm_conflicts]
    def is_conflict_row(it):
        for cs, ce in conflict_ranges:
            if has_overlap(it, {"start": cs, "end": ce}):
                return True
        return False

    for it in items:
        light, dark = TYPE_COLORS.get(it["type"], ("#f0f0f0", "#636e72"))
        span_wd = it.get("span_wd", 0)
        is_display = it.get("display_only", False)
        row_cls = " conflict-row" if (is_conflict_row(it) and not is_display) else ""
        type_label = it.get("sub", "") if it.get("sub") else it["type"]
        display_type = {"澄清(前)": "澄清(非PM)", "澄清(PM)": "澄清(PM)"}.get(it["type"], it["type"])
        html += f"""<tr class="{row_cls}">
<td><div class="task-info"><span class="task-name">{it["task"][:45]}</span><span class="task-module">{it["module"]}</span></div></td>
<td><span class="type-badge" style="background:{dark}">{display_type}</span></td>
<td class="date-range">{it["start"]} ~ {it["end"]}</td>
<td class="wd-num">{span_wd}wd</td></tr>"""

    html += "</tbody></table></div>"

html += """<div class="footer">Generated by HRAS Schedule Engine v3 · PM Timeline Analyzer</div>
</body></html>"""

with open("pm_timeline_report.html", "w", encoding="utf-8") as f:
    f.write(html)
print("PM时间线报告(含冲突分析)已生成: pm_timeline_report.html")
