"""Phase 2/3: per-PM batch scheduling from Phase 1 end."""
import sys, json, math, subprocess
sys.path.insert(0, "D:/WorkBuddyPlace/Project-scheduling")
from schedule_engine import Task, WorkingDayCalendar, MODULE_PRIORITY, schedule_all, load_feishu_config
from datetime import date, timedelta
from collections import defaultdict

today = date(2026, 7, 16)
with open("holidays.json") as f: hd = json.load(f)
cal = WorkingDayCalendar(hd.get("holidays", []), hd.get("makeup_days", []))
F = load_feishu_config("feishu_config.json")
BASE = "BKBKbUWXtas7tSshzoccyZa3ndb"; TABLE = "tbl5jDThuT51h4II"

result = subprocess.run(f'lark-cli base +record-list --base-token {BASE} --table-id {TABLE} --limit 200 --format json --as user',
    capture_output=True, text=True, timeout=30, shell=True)
raw = json.loads(result.stdout)
records = raw["data"]["data"]; fid_list = raw["data"]["field_id_list"]; rid_list = raw["data"]["record_id_list"]
idx = {fid: i for i, fid in enumerate(fid_list)}
def v(rec,fid): i=idx.get(fid); return rec[i] if i is not None and i<len(rec) else None
def d(rec,fid): x=v(rec,fid); return x[:10] if x and isinstance(x,str) else None
def s(rec,fid): x=v(rec,fid); return x[0] if isinstance(x,list) and x else ''

ORIGINAL_ANCHORS = {"【移动端】出勤管理":1,"【考勤】商旅平台对接（排期只是暂定）":1,
    "【奖金补贴】社保公积金、福利补贴、奖金管理":1,
    "员工多档案视图（不同国家、不同用工）":1,"工作台重塑（基于角色和事项）PC端":1,
    "工作台重塑（基于角色和事项）移动端":1}

all_tds = []
for i, rec in enumerate(records):
    nm = v(rec, "fldD5A1CvQ") or ""; phase = s(rec, "fldSuDoxFN")
    if not phase or s(rec,"fldp8xrC9I")=="是" or v(rec,"fldF6tKrhx"): continue
    pm_v = v(rec,"fldsoiimKC"); pm = pm_v[0].get("name","") if isinstance(pm_v,list) and pm_v else ""
    nm_c = nm.replace("\n"," ").strip()
    test_ratio = str(v(rec,"fldWzxXVx2") or "").strip()
    all_tds.append({"record_id":rid_list[i],"name":nm_c,"module":s(rec,"fldPJrW9qs"),"phase":phase,
        "iterations":float(v(rec,"fld5BFCMTn") or 1),
        "tech_review":d(rec,"fldMxPbqBt"),
        "clarify_start":d(rec,"fldSRgjNL3"),"clarify_end":d(rec,"fldXvhBatN"),
        "design_start":d(rec,"fld5PTJ6XN"),"design_end":d(rec,"fldaMhsTR5"),
        "review_start":d(rec,"fldJkPovnb"),"review_end":d(rec,"fldRvi3Miv"),
        "dev_product_ratio":str(v(rec,"fldFPiQasw") or ""),"dev_test_ratio":test_ratio,
        "pm_name":pm,"dev_count":int(v(rec,"fldjrzlD4T") or 2),"test_count":int(v(rec,"fldXYCa4gc") or 2),
        "product_clarify_md":float(v(rec,"fld0fMXr6m") or 0),"demand_md":float(v(rec,"fldhE38q8v") or 0),
        "review_md":float(v(rec,"fldm3qBBJx") or 0),
        "dev_man_days":0,"dev_slots":"","test_slots":"","original_index":i,
        "is_anchor":nm_c in ORIGINAL_ANCHORS,
        "ex_dev_s":d(rec,"fldu4esCvi"),"ex_dev_e":d(rec,"fldpXsPTvE")})

p1_tds = [t for t in all_tds if t["phase"]=="一期"]
p23_anchor_tds = [t for t in all_tds if t["phase"] in ("二期","三期") and t["is_anchor"]]
p23_free_tds = [t for t in all_tds if t["phase"] in ("二期","三期") and not t["is_anchor"]]

missing = [t for t in p23_free_tds if not t["dev_test_ratio"]]
if missing:
    for t in missing: print(f"⚠ 产测比为空: {t['name'][:45]}")
    raise SystemExit("请补充产测比")
print(f"一期:{len(p1_tds)} 二期锚:{len(p23_anchor_tds)} 二期新:{len(p23_free_tds)}")

def make_task(td, freeze=False):
    t = Task.from_dict(td)
    t.original_index=td["original_index"]; t.product_clarify_md=td["product_clarify_md"]
    t.demand_md=td["demand_md"]; t.review_md=td["review_md"]; t.dev_test_ratio=td["dev_test_ratio"]
    t.phase_name=td["phase"]
    if freeze and td["ex_dev_s"]:
        try: t.new_dev_start=date.fromisoformat(td["ex_dev_s"])
        except: pass
    if freeze and td["ex_dev_e"]:
        try: t.new_dev_end=date.fromisoformat(td["ex_dev_e"])
        except: pass
    if not freeze:
        # Clear old demand phase dates so engine re-generates them
        t.clarify_start = None; t.clarify_end = None
        t.design_start = None; t.design_end = None
        t.tech_review_start = None; t.tech_review_end = None
        t.tech_review = None
    return t

anchors = [make_task(td, freeze=True) for td in p23_anchor_tds]
free_tasks = [make_task(td) for td in p23_free_tds]

# Per-PM Phase 1 end
pm_p1_end = {}
for td in p1_tds:
    pm=td["pm_name"]; 
    if not pm: continue
    for rec in records:
        if (v(rec,"fldD5A1CvQ") or "").replace("\n"," ").strip()!=td["name"]: continue
        if s(rec,"fldSuDoxFN")!="一期": continue
        for fid in ["fldRvi3Miv","fldaMhsTR5","fldXvhBatN","flduVm6fRo"]:
            ds=d(rec,fid)
            if ds:
                try:
                    dt=date.fromisoformat(ds)
                    if pm not in pm_p1_end or dt>pm_p1_end[pm]: pm_p1_end[pm]=dt
                except: pass
        break

# Schedule per PM batch
all_p23 = []  # (pm, task) pairs
for pm in set(t.pm_name for t in anchors+free_tasks if t.pm_name):
    pm_tasks = [t for t in anchors+free_tasks if t.pm_name==pm]
    pm_start = pm_p1_end.get(pm) or today
    pm_sim = cal.next_working_day(pm_start)
    
    # Setup free tasks for this PM
    pm_free = [t for t in pm_tasks if t not in anchors]
    pm_tl = defaultdict(list)
    for t in pm_free: pm_tl[t.pm_name or "_none"].append(t)
    for pts in pm_tl.values():
        wtr=[pp for pp in pts if pp.tech_review is not None]
        if wtr: min(wtr,key=lambda x:x.tech_review).is_anchor=True
    act = [t for t in pm_free if not (t.is_anchor and t.tech_review and t.tech_review<today)]
    for t in act:
        if t.is_anchor and t.tech_review is None and t.design_end:
            t.tech_review = cal.add_working_days(cal.next_working_day(t.design_end), math.ceil(t.review_md)) if t.review_md else cal.next_working_day(t.design_end)
        if not t.is_anchor: pass
    
    pm_combined = [t for t in pm_tasks if t in anchors] + act
    print(f"  {pm}: {len(pm_combined)}个, 起始{pm_sim}")
    batch = schedule_all(pm_combined, cal, pm_sim, max_gap_days=5)
    all_p23.extend((pm, t) for t in batch)

# Results
print(f"\n{'='*170}")
print(f"{'任务':<35} {'期':<4} {'模块':<10} {'PM':<6} {'需求阶段':<28} {'技术评审':<12} {'研发':<22} {'验收':<22}")
print(f"{'='*170}")
for pm, t in sorted(all_p23, key=lambda x: (pm_p1_end.get(x[0],today), MODULE_PRIORITY.get(x[1].module,99), x[1].name)):
    fd=lambda d: str(d) if d else "-"
    cs=fd(t.new_clarify_start or t.clarify_start or t.new_req_start or t.design_start)
    rve=fd(t.new_review_end or t.tech_review_end or t.new_req_end or t.design_end or t.new_clarify_end or t.clarify_end)
    tr=fd(t.tech_review); dev=f"{fd(t.new_dev_start)}~{fd(t.new_dev_end)}"; acc=f"{fd(t.new_accept_start)}~{fd(t.new_accept_end)}"
    req=f"{cs}~{rve}" if cs and cs!="-" and rve and rve!="-" else "-"
    ph="②" if t.phase_name=="二期" else "③"
    nm_c=t.name.replace("\n"," ").strip()
    flag="锚" if nm_c in ORIGINAL_ANCHORS else "新"
    print(f"{t.name[:33]:35s} {ph:<4} {t.module[:8]:10s} {(t.pm_name or ''):<6} {req:<28s} {tr:<12} {dev:<22s} {acc:<22s}")

# Writeback
wb_lines=['#!/bin/bash']
for pm, t in all_p23:
    if not t.record_id: continue
    patch={}
    for attr,key,fid in [("new_clarify_start","clarify_start","fldSRgjNL3"),("new_clarify_end","clarify_end","fldXvhBatN"),
        ("new_req_start","design_start","fld5PTJ6XN"),("new_req_end","design_end","fldaMhsTR5"),
        ("new_review_start","review_start","fldJkPovnb"),("new_review_end","review_end","fldRvi3Miv")]:
        val=getattr(t,attr,None) or getattr(t,key,None)
        if val and isinstance(val,date): patch[fid]=val.isoformat()
    if t.tech_review and isinstance(t.tech_review,date): patch["fldMxPbqBt"]=t.tech_review.isoformat()
    for attr,fid in [("new_dev_start","fldu4esCvi"),("new_dev_end","fldpXsPTvE"),
        ("new_test_start","fldlCFwt7C"),("new_test_end","fld98ZTVMF"),
        ("new_accept_start","fldgv6n2rx"),("new_accept_end","flduVm6fRo")]:
        val=getattr(t,attr,None)
        if val and isinstance(val,date): patch[fid]=val.isoformat()
    if t.dev_slots: patch["fldCeLIjfG"]=t.dev_slots.replace("101","A").replace("102","B")
    if t.test_slots: patch["fldFaworUZ"]=t.test_slots
    if t.dev_man_days: patch["fldOPeRb5q"]=round(t.dev_man_days,1)
    if t.test_man_days: patch["fldRWW5yQT"]=round(t.test_man_days,1)
    if not patch: continue
    wb_lines.append(f"lark-cli base +record-upsert --base-token {BASE} --table-id {TABLE} --record-id {t.record_id} --as user --json '{json.dumps(patch, ensure_ascii=False)}'")

with open("writeback_phase23.sh","w",encoding="utf-8") as f: f.write("\n".join(wb_lines))
r=subprocess.run("bash writeback_phase23.sh",capture_output=True,text=True,timeout=120,shell=True)
good=r.stdout.count('"update"')
print(f"\n写回: {good}/{len(wb_lines)-1} ✅")
