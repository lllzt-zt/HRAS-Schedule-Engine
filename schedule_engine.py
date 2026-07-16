#!/usr/bin/env python3
"""
HRAS Project Schedule Engine v2
=================================
Time-driven, cross-module parallel scheduler with soft gap constraints,
skill-based resource matching, and continuous-iteration awareness.

Improvements over v1:
  1. Soft gap constraint: prefers tight dev→test handoffs, escalates urgency
     when gap exceeds max_gap_days but doesn't deadlock.
  2. Cross-module parallel: time-driven simulation assigns resources
     day-by-day; priority only resolves same-slot contention, never
     blocks a task whose resources are idle.
  3. Continuous iteration: shadow-scheduled without competing for
     resource pools (reported separately).
  4. Skill matrix: optional preference for primary/secondary module
     matching when multiple slot combinations are feasible.

Usage:
    python schedule_engine.py \
        --tasks tasks.json \
        --holidays holidays.json \
        [--skill-matrix skill_matrix.json] \
        [--max-gap 5] \
        [--today 2026-07-12] \
        [--output report.html]
"""

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from collections import defaultdict
from enum import Enum
from heapq import heappush, heappop
from typing import Optional


# ============================================================
# Module Priority (lower = higher, used for tie-breaking only)
# ============================================================
MODULE_PRIORITY = {
    "绩效": 0, "人事": 1, "考勤": 2, "薪酬": 3,
    "HRONE基础建设": 4, "流程平台": 5,
}
MODULES_ORDER = ["绩效", "人事", "考勤", "薪酬", "HRONE基础建设", "流程平台"]


# ============================================================
# Working Day Calendar
# ============================================================

class WorkingDayCalendar:
    def __init__(self, holidays, makeup_days):
        self.holidays = {date.fromisoformat(d.split("T")[0]) for d in holidays}
        self.makeup_days = {date.fromisoformat(d.split("T")[0]) for d in makeup_days}

    def is_working_day(self, d):
        if d in self.makeup_days:
            return True
        if d.weekday() >= 5:
            return False
        if d in self.holidays:
            return False
        return True

    def next_working_day(self, d):
        d = d + timedelta(days=1)
        while not self.is_working_day(d):
            d += timedelta(days=1)
        return d

    def prev_working_day(self, d):
        d = d - timedelta(days=1)
        while not self.is_working_day(d):
            d -= timedelta(days=1)
        return d

    def add_working_days(self, start, n_days):
        """From start (inclusive), count n_days working days, return end date."""
        if n_days <= 0:
            return start
        d = start
        count = 0
        while count < n_days:
            while not self.is_working_day(d):
                d += timedelta(days=1)
            count += 1
            if count < n_days:
                d += timedelta(days=1)
        return d

    def working_days_between(self, start, end):
        if start is None or end is None or start > end:
            return 0
        count = 0
        d = start
        while d <= end:
            if self.is_working_day(d):
                count += 1
            d += timedelta(days=1)
        return count

    def working_days_from(self, start, n):
        """Return the date after skipping n working days from start (exclusive of start)."""
        d = start
        count = 0
        while count < n:
            d += timedelta(days=1)
            if self.is_working_day(d):
                count += 1
        return d


# ============================================================
# Data Classes
# ============================================================

class Phase(Enum):
    DEV = "dev"
    TEST = "test"
    ACCEPT = "accept"


@dataclass
class ResourcePool:
    """Tracks resource availability for a phase across slots."""
    name: str
    slots: list  # list of slot IDs
    busy: dict = field(default_factory=dict)  # slot_id -> set of busy dates

    def __post_init__(self):
        self.busy = {s: set() for s in self.slots}

    def is_free(self, slot_ids, d, cal):
        """Check if all slot_ids are free on date d."""
        if not cal.is_working_day(d):
            return False
        for sid in slot_ids:
            if d in self.busy.get(sid, set()):
                return False
        return True

    def are_all_free(self, slot_ids, start, end, cal):
        """Check if all slot_ids are free for the entire [start, end] period (working days only)."""
        d = start
        while d <= end:
            if cal.is_working_day(d):
                for sid in slot_ids:
                    if d in self.busy.get(sid, set()):
                        return False
            d += timedelta(days=1)
        return True

    def occupy(self, slot_ids, start, end, cal):
        """Mark slot_ids as busy for working days in [start, end]."""
        d = start
        while d <= end:
            if cal.is_working_day(d):
                for sid in slot_ids:
                    self.busy[sid].add(d)
            d += timedelta(days=1)

    def earliest_free_window(self, slot_ids, from_date, duration_wd, cal):
        """Find earliest window of duration_wd working days where all slot_ids are free."""
        d = from_date
        # Search forward
        while True:
            # Skip non-working days
            while not cal.is_working_day(d):
                d += timedelta(days=1)
            # Check if we can fit duration_wd days starting from d
            temp = d
            ok = True
            for _ in range(duration_wd):
                if not self.is_free(slot_ids, temp, cal):
                    ok = False
                    break
                temp += timedelta(days=1)
                while not cal.is_working_day(temp):
                    temp += timedelta(days=1)
                # Actually, we need to check consecutive working days
            if ok:
                # Verify: consecutive working days from d
                consecutive = 0
                check = d
                while consecutive < duration_wd:
                    if not cal.is_working_day(check):
                        check += timedelta(days=1)
                        continue
                    if not self.is_free(slot_ids, check, cal):
                        ok = False
                        break
                    consecutive += 1
                    check += timedelta(days=1)
                if ok:
                    return d
            d += timedelta(days=1)


@dataclass
class SkillMatrix:
    """Maps slot IDs to module expertise (primary/secondary)."""
    primary: dict = field(default_factory=dict)    # slot_id -> [modules]
    secondary: dict = field(default_factory=dict)  # slot_id -> [modules]

    def match_score(self, slot_id, module):
        if module in self.primary.get(slot_id, []):
            return 0  # best match
        if module in self.secondary.get(slot_id, []):
            return 1  # okay match
        return 999  # no match

    def best_combination(self, needed_count, available_slots, module):
        """From available_slots, pick needed_count with best match scores."""
        scored = [(self.match_score(s, module), s) for s in available_slots]
        scored.sort()
        return [s for _, s in scored[:needed_count]]


@dataclass
class Task:
    name: str
    module: str
    record_id: str = ""  # Feishu Base record ID for write-back
    phase_name: str = ""
    tech_review: Optional[date] = None  # 技术评审 (原review_date)
    clarify_start: Optional[date] = None  # 澄清开始 v4
    clarify_end: Optional[date] = None    # 澄清结束 v4
    tech_review_start: Optional[date] = None  # 评审开始 v4
    tech_review_end: Optional[date] = None    # 评审结束 v4
    design_start: Optional[date] = None
    design_end: Optional[date] = None
    iterations: float = 1
    dev_man_days: Optional[float] = None
    dev_count: int = 2
    test_count: int = 2
    dev_slots: str = ""
    test_slots: str = ""
    pm_name: Optional[str] = None
    pm_acceptance: Optional[str] = None
    dev_product_ratio: str = ""   # "1:2" or "1:3" for deriving dev man-days
    dev_test_ratio: str = ""       # 产测比 e.g. "1:0.8", "1:1" for deriving test man-days
    is_continuous: bool = False
    test_man_days_bitable: Optional[float] = None   # read from Bitable, not computed
    accept_man_days_bitable: Optional[float] = None  # read from Bitable, not computed
    # v4: Requirement phase effort fields (read from Base)
    product_clarify_md: Optional[float] = None  # 产品澄清人天
    demand_md: Optional[float] = None           # 需求人天
    review_md: Optional[float] = None           # 业务评审人天
    # Is this the anchor (first) task? True if tech_review is explicitly given
    is_anchor: bool = False

    # Old schedule (for comparison report)
    old_dev_start: Optional[date] = None
    old_dev_end: Optional[date] = None
    old_test_start: Optional[date] = None
    old_test_end: Optional[date] = None
    old_accept_start: Optional[date] = None
    old_accept_end: Optional[date] = None

    # New schedule results
    new_clarify_start: Optional[date] = None  # 澄清开始(排期结果)
    new_clarify_end: Optional[date] = None    # 澄清结束(排期结果)
    new_req_start: Optional[date] = None      # 需求开始(排期结果)
    new_req_end: Optional[date] = None        # 需求结束(排期结果)
    new_review_start: Optional[date] = None   # 业务评审开始(排期结果)
    new_review_end: Optional[date] = None     # 业务评审结束(排期结果)
    new_dev_start: Optional[date] = None
    new_dev_end: Optional[date] = None
    new_test_start: Optional[date] = None
    new_test_end: Optional[date] = None
    new_accept_start: Optional[date] = None
    new_accept_end: Optional[date] = None
    dev_man_days_changed: bool = False  # True if computed differs from Bitable
    warnings: list = field(default_factory=list)
    # v4: PM state tracking
    pm_task_type: Optional[str] = None  # "req" or "accept" - what PM is doing with this task
    req_wd: int = 0  # 需求阶段总工期(工作日)

    def __post_init__(self):
        # Dev man-days: compute from ratio, compare with Bitable value
        ratio = (self.dev_product_ratio or "").replace("：", ":").strip()
        computed_dev = None
        if ratio == "1:2":
            computed_dev = self.iterations * 10
        elif ratio == "1:3":
            computed_dev = self.iterations * 15

        # Compare with Bitable value; flag if different
        if computed_dev is not None:
            if self.dev_man_days is None or abs(computed_dev - self.dev_man_days) > 0.01:
                self.dev_man_days_changed = True
            self.dev_man_days = computed_dev
        else:
            self.dev_man_days = 0

        # Test man-days: calculated from 产测比 (dev_test_ratio)
        # Formula: 迭代数 × 产测比第二数值 × 5
        if self.dev_test_ratio:
            try:
                test_ratio_str = self.dev_test_ratio.replace("：", ":").strip()
                parts = test_ratio_str.split(":")
                if len(parts) < 2:
                    raise ValueError(f"格式错误: {self.dev_test_ratio}")
                test_ratio_val = float(parts[1])
                if test_ratio_val <= 0:
                    raise ValueError(f"产测比值必须>0: {self.dev_test_ratio}")
                self.test_man_days = self.iterations * test_ratio_val * 5.0
            except (ValueError, IndexError) as e:
                raise ValueError(
                    f"任务 '{self.name}' 产测比无效: {self.dev_test_ratio} ({e})，"
                    f"请修正后再排期"
                )
        else:
            raise ValueError(
                f"任务 '{self.name}' 缺少产测比字段，请补充后再排期"
            )

        # Acceptance man-days: read from Bitable, use iterations×2.5 only as fallback
        if self.accept_man_days_bitable is not None and self.accept_man_days_bitable > 0:
            self.accept_man_days = self.accept_man_days_bitable
        else:
            self.accept_man_days = self.iterations * 2.5  # fallback

        # Perf independent team?
        self.is_perf_dev = (self.dev_slots == "独立团队")
        if self.is_perf_dev:
            self.dev_workers = 2
        else:
            self.dev_workers = self.dev_count or 2

        self.test_workers = self.test_count or 2

        # Working days needed per phase
        if self.dev_man_days is not None and self.dev_man_days > 0:
            self.dev_wd = max(1, int((self.dev_man_days + self.dev_workers - 1) // self.dev_workers))
        else:
            self.dev_wd = 0

        self.test_wd = max(1, int((self.test_man_days + self.test_workers - 1) // self.test_workers))
        self.accept_wd = max(1, math.ceil(self.accept_man_days))

        # v4: Req phase duration from clarify+review+design periods
        self._calc_req_wd()

    def _calc_req_wd(self):
        """v4: PM工时 = 产品澄清人天 + 需求人天 + 业务评审人天"""
        md = 0
        if self.product_clarify_md:
            md += self.product_clarify_md
        if self.demand_md:
            md += self.demand_md
        if self.review_md:
            md += self.review_md
        self.req_wd = max(1, math.ceil(md)) if md > 0 else 0
        self.req_total_calendar_wd = 0  # Total calendar span (澄清总工期)
        if self.iterations:
            self.req_total_calendar_wd = max(1, math.ceil(self.iterations * 3))
        # 前1/3非PM, 后2/3PM
        self.req_non_pm_wd = max(1, math.ceil(self.req_total_calendar_wd / 3))
        self.req_pm_wd = self.req_total_calendar_wd - self.req_non_pm_wd

    @property
    def priority_key(self):
        """Lower = higher priority. Module priority first, then tech_review."""
        return (MODULE_PRIORITY.get(self.module, 99),
                self.tech_review.toordinal() if self.tech_review else 999999)

    @property
    def is_active(self):
        """v4: Tasks without dev, test, accept AND req are skipped."""
        return (self.dev_wd > 0 or self.test_wd > 0 or
                self.accept_wd > 0 or self.req_wd > 0)

    @property
    def acceptance_ready_date(self):
        """Date when acceptance becomes ready (test_end + 1wd)."""
        if self.new_test_end:
            return self.new_test_end + timedelta(days=1)
        return None

    @staticmethod
    def from_dict(data: dict) -> "Task":
        def _d(key):
            v = data.get(key)
            if v and isinstance(v, str):
                try:
                    d = date.fromisoformat(v.split("T")[0])
                    if d == date(1900, 1, 1):  # 清除标记视为空
                        return None
                    return d
                except: return None
            return None

        return Task(
            record_id=data.get("record_id", ""),
            name=data["name"],
            module=data["module"],
            phase_name=data.get("phase", ""),
            tech_review=_d("tech_review"),
            clarify_start=_d("clarify_start"),
            clarify_end=_d("clarify_end"),
            tech_review_start=_d("tech_review_start"),
            tech_review_end=_d("tech_review_end"),
            design_start=_d("design_start"),
            design_end=_d("design_end"),
            iterations=data.get("iterations", 1),
            dev_man_days=data.get("dev_man_days"),
            dev_count=data.get("dev_count", 2),
            test_count=data.get("test_count", 2),
            dev_slots=data.get("dev_slots", ""),
            test_slots=data.get("test_slots", ""),
            pm_name=data.get("pm_name"),
            pm_acceptance=data.get("pm_acceptance"),
            dev_product_ratio=data.get("dev_product_ratio", ""),
            dev_test_ratio=data.get("dev_test_ratio", ""),
            is_continuous=data.get("is_continuous", False),
            test_man_days_bitable=data.get("test_man_days_bitable"),
            accept_man_days_bitable=data.get("accept_man_days_bitable"),
            product_clarify_md=data.get("product_clarify_md"),
            demand_md=data.get("demand_md"),
            review_md=data.get("review_md"),
            is_anchor=data.get("is_anchor", False),
            old_dev_start=_d("old_dev_start"),
            old_dev_end=_d("old_dev_end"),
            old_test_start=_d("old_test_start"),
            old_test_end=_d("old_test_end"),
            old_accept_start=_d("old_accept_start"),
            old_accept_end=_d("old_accept_end"),
        )


def parse_slots(s):
    """Parse comma-separated slot string, return list of ints."""
    if not s or s == "独立团队" or s == "未分配":
        return []
    try:
        return [int(x.strip()) for x in s.split(",")]
    except (ValueError, AttributeError):
        return []


# ============================================================
# Phase-Based Parallel Scheduler
# ============================================================

class PhaseScheduler:
    """
    Time-driven scheduler for one phase (dev/test/accept).
    
    Walks forward day by day. On each working day:
      1. Collect tasks whose predecessor phase just ended → add to ready queue.
      2. Sort ready queue by (urgency, priority).
      3. Try to assign resources to as many ready tasks as possible.
      4. Tasks that can't get resources wait until next day.
    
    Priority only resolves contention for the SAME resource slot.
    A lower-priority task whose required slots are idle gets scheduled immediately.
    """

    def __init__(self, phase: Phase, pool: ResourcePool, cal: WorkingDayCalendar,
                 max_gap_days: int = 5, skill_matrix: Optional[SkillMatrix] = None):
        self.phase = phase
        self.pool = pool
        self.cal = cal
        self.max_gap_days = max_gap_days
        self.skill_matrix = skill_matrix
        # Internal state during simulation
        self.ready_queue = []  # heapq of (urgency, priority, task_id, task)
        self.in_progress = {}  # task_id -> (task, end_date)
        self._task_counter = 0

    def _push(self, task, urgency=0):
        """Add task to ready queue with given urgency (higher = more urgent)."""
        # heapq is min-heap, so we use (-urgency, priority_key) for max-urgency first
        heappush(self.ready_queue,
                 (-urgency, task.priority_key[0], task.priority_key[1], self._task_counter, task))
        self._task_counter += 1

    def enqueue(self, task):
        """Called from outside: a task's predecessor phase ended, it's ready."""
        urgency = 0
        # Soft gap escalation
        if self.phase == Phase.TEST and self.max_gap_days > 0:
            if task.new_dev_end is not None:
                gap_wd = self.cal.working_days_between(
                    task.new_dev_end + timedelta(days=1),
                    self.cal.prev_working_day(date.today())  # Will be set by simulation day
                )
                # urgency will be recalculated each simulation step
        self._push(task, urgency=0)

    def update_urgency(self, task, current_date):
        """Recalculate urgency based on gap from predecessor end."""
        if self.max_gap_days > 0:
            if self.phase == Phase.TEST and task.new_dev_end:
                gap_wd = self.cal.working_days_between(
                    task.new_dev_end + timedelta(days=1),
                    current_date - timedelta(days=1) if current_date > task.new_dev_end else task.new_dev_end
                )
                if gap_wd > self.max_gap_days:
                    return gap_wd - self.max_gap_days
            elif self.phase == Phase.ACCEPT and task.new_test_end:
                gap_wd = self.cal.working_days_between(
                    task.new_test_end + timedelta(days=1),
                    current_date - timedelta(days=1) if current_date > task.new_test_end else task.new_test_end
                )
                if gap_wd > self.max_gap_days:
                    return gap_wd - self.max_gap_days
        return 0

    def rebuild_queue(self, current_date):
        """Rebuild heap with updated urgencies based on current_date."""
        items = []
        while self.ready_queue:
            _, _, _, _, task = heappop(self.ready_queue)
            urgency = self.update_urgency(task, current_date)
            items.append((urgency, task))
        for urgency, task in items:
            self._push(task, urgency)

    def step(self, current_date):
        """
        Try to assign resources to ready tasks on current_date.
        If this phase has no resource pool (e.g., acceptance), schedule immediately.
        Returns number of tasks started.
        """
        if not self.cal.is_working_day(current_date):
            return 0

        # Rebuild queue with updated urgencies
        self.rebuild_queue(current_date)

        # Collect all ready tasks
        candidates = []
        while self.ready_queue:
            _, _, _, _, task = heappop(self.ready_queue)
            candidates.append(task)

        started = 0
        skipped = []

        for task in candidates:
            # Acceptance phase and phases without pool: schedule immediately
            if self.phase == Phase.ACCEPT or self.pool is None or not self.pool.slots:
                duration = self._get_duration(task)
                if duration <= 0:
                    continue
                end_date = self.cal.add_working_days(current_date, duration)
                self._set_result(task, current_date, end_date)
                started += 1
                continue

            slot_ids = self._get_slots(task)
            duration = self._get_duration(task)
            end_date = self.cal.add_working_days(current_date, duration)

            if not slot_ids:
                # No pre-assigned slots → dynamic allocation
                slot_ids = self._find_free_slots(task, current_date, end_date)
                if not slot_ids:
                    # Pool fully occupied → retry next day
                    skipped.append(task)
                    continue

                # Record the dynamically assigned slots for write-back
                slot_str = ",".join(str(s) for s in slot_ids)
                if self.phase == Phase.DEV:
                    task.dev_slots = slot_str
                elif self.phase == Phase.TEST:
                    task.test_slots = slot_str

            # Check if we can start this task today
            if self.pool.are_all_free(slot_ids, current_date, end_date, self.cal):
                self.pool.occupy(slot_ids, current_date, end_date, self.cal)
                self._set_result(task, current_date, end_date)
                started += 1
                continue

            # Can't schedule now; will retry next day
            skipped.append(task)

        # Re-add skipped tasks with updated urgency
        for task in skipped:
            urgency = self.update_urgency(task, current_date)
            self._push(task, urgency)

        return started

    def step_with_extra(self, current_date, extra_slots):
        """
        Like step() but also allows non-perf tasks to use extra_slots
        (e.g. perf slots 101/102) when their preferred slots are busy.
        Only searches extra_slots individually, not all combinations.
        No cross-contamination between tasks.
        """
        if not self.cal.is_working_day(current_date):
            return 0

        self.rebuild_queue(current_date)

        candidates = []
        while self.ready_queue:
            _, _, _, _, task = heappop(self.ready_queue)
            candidates.append(task)

        started = 0
        skipped = []

        for task in candidates:
            # Perf dev guard
            if task.is_perf_dev and self.phase == Phase.DEV:
                duration = self._get_duration(task)
                if duration <= 0: continue
                end_date = self.cal.add_working_days(current_date, duration)
                if self.pool.are_all_free([101, 102], current_date, end_date, self.cal):
                    self.pool.occupy([101, 102], current_date, end_date, self.cal)
                    self._set_result(task, current_date, end_date)
                    started += 1
                    continue
                else:
                    skipped.append(task)
                    continue

            duration = self._get_duration(task)
            if duration <= 0: continue
            end_date = self.cal.add_working_days(current_date, duration)

            preferred = self._get_slots(task)
            if not preferred:
                # Dynamic allocation for tasks with no pre-assigned slots
                preferred = self._find_free_slots(task, current_date, end_date)
                if not preferred:
                    skipped.append(task)
                    continue
                # Record for write-back
                slot_str = ",".join(str(s) for s in preferred)
                if self.phase == Phase.DEV:
                    task.dev_slots = slot_str
                elif self.phase == Phase.TEST:
                    task.test_slots = slot_str

            # Try preferred slots
            if self.pool.are_all_free(preferred, current_date, end_date, self.cal):
                self.pool.occupy(preferred, current_date, end_date, self.cal)
                self._set_result(task, current_date, end_date)
                started += 1
                continue

            # Try extra slots: check each extra slot + one preferred slot combinations
            found = False
            if extra_slots and self.phase == Phase.DEV and not task.is_perf_dev:
                needed = len(preferred)
                if needed == 2:
                    # Try replacing one of the preferred slots with an extra slot
                    for ex in extra_slots:
                        for i in range(needed):
                            alt = list(preferred)
                            alt[i] = ex
                            if self.pool.are_all_free(alt, current_date, end_date, self.cal):
                                self.pool.occupy(alt, current_date, end_date, self.cal)
                                self._set_result(task, current_date, end_date)
                                task.warnings.append(f"{self.phase.value}阶段: 借用绩效槽位 {ex}（原 {preferred}）")
                                started += 1
                                found = True
                                break
                        if found: break
                    if found: continue

            skipped.append(task)

        for task in skipped:
            urgency = self.update_urgency(task, current_date)
            self._push(task, urgency)

        return started

    def step_aggressive(self, current_date, all_slot_ids):
        """
        Like step() but tries alternative slot combinations when the
        preferred slots are busy. Uses skill_matrix to guide selection.
        """
        if not self.cal.is_working_day(current_date):
            return 0

        self.rebuild_queue(current_date)

        candidates = []
        while self.ready_queue:
            _, _, _, _, task = heappop(self.ready_queue)
            candidates.append(task)

        started = 0
        skipped = []

        for task in candidates:
            duration = self._get_duration(task)
            if duration <= 0:
                continue

            # Perf dev: MUST use BOTH slots 101+102, never substitute with regular pool
            if task.is_perf_dev and self.phase == Phase.DEV:
                end_date = self.cal.add_working_days(current_date, duration)
                if self.pool.are_all_free([101, 102], current_date, end_date, self.cal):
                    self.pool.occupy([101, 102], current_date, end_date, self.cal)
                    self._set_result(task, current_date, end_date)
                    started += 1
                    continue
                else:
                    skipped.append(task)
                    continue

            preferred_slots = self._get_slots(task)
            if not preferred_slots:
                task.warnings.append(f"{self.phase.value}阶段: 未分配资源槽位，跳过")
                continue

            slot_ids = self._find_best_slots(task, preferred_slots, all_slot_ids,
                                              current_date, duration)

            if slot_ids is not None:
                end_date = self.cal.add_working_days(current_date, duration)
                self.pool.occupy(slot_ids, current_date, end_date, self.cal)
                if slot_ids != preferred_slots:
                    task.warnings.append(
                        f"{self.phase.value}阶段: 槽位从 {preferred_slots} 调整为 {slot_ids}"
                    )
                self._set_result(task, current_date, end_date)
                started += 1
                continue

            skipped.append(task)

        for task in skipped:
            urgency = self.update_urgency(task, current_date)
            self._push(task, urgency)

        return started

    def _find_best_slots(self, task, preferred, all_slots, start_date, duration_wd):
        """Find the best slot combination for task. Returns slot_ids or None."""
        # Try preferred slots first
        if preferred and self.pool.are_all_free(preferred, start_date,
                                                 self.cal.add_working_days(start_date, duration_wd),
                                                 self.cal):
            return preferred

        # Try alternative combinations: all subsets of all_slots of size len(preferred)
        # that are free for the entire duration
        needed = len(preferred) if preferred else 2
        alternatives = []
        for combo in self._combinations(all_slots, needed):
            end_date = self.cal.add_working_days(start_date, duration_wd)
            if self.pool.are_all_free(combo, start_date, end_date, self.cal):
                # Score this combination using skill matrix
                if self.skill_matrix:
                    score = sum(self.skill_matrix.match_score(s, task.module) for s in combo)
                else:
                    score = 0
                alternatives.append((score, combo))

        if alternatives:
            alternatives.sort()
            return alternatives[0][1]

        return None

    @staticmethod
    def _combinations(items, k):
        """Generate all k-combinations from items list."""
        if k == 0:
            yield []
            return
        for i in range(len(items) - k + 1):
            for combo in PhaseScheduler._combinations(items[i + 1:], k - 1):
                yield [items[i]] + combo

    def _get_slots(self, task):
        if self.phase == Phase.DEV:
            if task.is_perf_dev:
                return [101, 102]  # perf dev virtual slots
            return []  # 非绩效任务: 不读预填值，交由 step() 动态分配
        elif self.phase == Phase.TEST:
            return []  # 所有测试槽位由 step() 动态分配
        else:
            return None  # Acceptance doesn't use slot pools

    def _find_free_slots(self, task, current_date, end_date):
        """
        Dynamically find free slots in the pool for a task with no pre-assigned slots.
        - DEV: find dev_workers slots from 1-6 (non-perf) or 101-102 (perf)
        - TEST: find test_workers slots from 1-6
        Returns list of slot_ids or empty list if none available.
        """
        if self.phase == Phase.DEV:
            if task.is_perf_dev:
                return [101, 102]  # K1=101, K2=102 绩效独立研发
            needed = task.dev_workers
            # 普通研发池 1-6 + K1/K2(101/102) 空闲时可共享
            pool_slots = [s for s in self.pool.slots if s <= 6]
            # 绩效空闲槽位 K1/K2 作为扩展池，仅在 _combinations 实际可用时被分配
            pool_slots += [s for s in self.pool.slots if s >= 101]
        elif self.phase == Phase.TEST:
            needed = task.test_workers
            pool_slots = [s for s in self.pool.slots if 1 <= s <= 4]  # 测试池仅 1-4
        else:
            return []

        # Find 'needed' free slots for the entire period
        for combo in self._combinations(pool_slots, needed):
            if self.pool.are_all_free(list(combo), current_date, end_date, self.cal):
                return list(combo)
        return []

    def _get_duration(self, task):
        if self.phase == Phase.DEV:
            return task.dev_wd
        elif self.phase == Phase.TEST:
            return task.test_wd
        else:
            return task.accept_wd

    def _set_result(self, task, start, end):
        if self.phase == Phase.DEV:
            task.new_dev_start = start
            task.new_dev_end = end
        elif self.phase == Phase.TEST:
            task.new_test_start = start
            task.new_test_end = end
        else:
            task.new_accept_start = start
            task.new_accept_end = end

    def _schedule_continuous(self, task, start_date):
        """Shadow-schedule continuous task without occupying resources."""
        duration = self._get_duration(task)
        if duration <= 0:
            return
        end_date = self.cal.add_working_days(start_date, duration)
        self._set_result(task, start_date, end_date)


def schedule_all(tasks, cal, today, max_gap_days=5, skill_matrix=None):
    """
    Time-driven cross-module parallel scheduler with PM Scheduler (v4).

    Each simulation day:
      1. DEV: try to start dev for all tasks whose tech_review has passed
      2. TEST: for tasks whose dev ended, try to start test (soft gap constraint)
      3. PM Scheduler: schedule acceptance and requirement phases
         - Collect pending PM tasks (ready acceptance + unstarted req phases)
         - Sort by start time (earliest first; tie-break: req > accept)
         - Execute the first one

    Priority only resolves same-slot contention. Lower-priority tasks whose
    required slots are idle get scheduled immediately (no artificial waiting).

    v4: Tasks with null tech_review are NOT excluded (they are subsequent tasks
    whose dates are calculated by the engine). Only the anchor task has tech_review.
    """
    # v4: Include all active tasks (tech_review may be null for subsequent tasks)
    active = [t for t in tasks if t.is_active]
    if not active:
        return tasks

    # Resource pools
    dev_pool = ResourcePool("研发", list(range(1, 7)) + [101, 102])
    test_pool = ResourcePool("测试", list(range(1, 5)))  # 4 槽位：1,2,3,4
    all_dev_slots = list(range(1, 7))
    all_test_slots = list(range(1, 5))

    # Phase schedulers
    dev_s = PhaseScheduler(Phase.DEV, dev_pool, cal, max_gap_days, skill_matrix)
    test_s = PhaseScheduler(Phase.TEST, test_pool, cal, max_gap_days, skill_matrix)
    accept_s = PhaseScheduler(Phase.ACCEPT, ResourcePool("验收", []), cal, max_gap_days, skill_matrix)

    # Tracking sets to avoid double-enqueuing (use id() as key)
    dev_enqueued = set()
    test_enqueued = set()
    accept_enqueued = set()

    # v4: PM Scheduler state per PM
    # pm_state[pm_name] = {"busy_until": date, "busy_type": "req"|"accept"|None, "task_id": id}
    pm_state = {}
    # pm_req_queue[pm_name] = list of tasks ordered by planned req start
    pm_req_queue = defaultdict(list)

    # Initialize PM state and req queues for each PM
    # pm_req_busy_until tracks the end of PM's LAST req phase occupation (persistent across sim days)
    # Initialize from anchor task dates so Phase 2/3 tasks respect Phase 1+anchor PM occupation
    pm_req_busy_until = {}
    for task in active:
        pm = task.pm_name
        if pm:
            if pm not in pm_state:
                pm_state[pm] = {"busy_until": None, "busy_type": None, "task_id": None}
            pm_req_queue[pm].append(task)
            # If anchor task has req phase dates, set pm_req_busy_until to the latest req phase end
            if task.is_anchor or task.new_req_start is not None:
                req_end = None
                if task.new_review_end:
                    req_end = task.new_review_end
                elif task.new_req_end:
                    req_end = task.new_req_end
                elif task.new_clarify_end:
                    req_end = task.new_clarify_end
                if req_end and (pm not in pm_req_busy_until or req_end > pm_req_busy_until[pm]):
                    pm_req_busy_until[pm] = req_end

    # Sort each PM's req queue: anchor first, then by module priority, then by record order
    for pm, queue in pm_req_queue.items():
        queue.sort(key=lambda t: (
            0 if t.is_anchor else 1,  # Anchor first
            {"二期": 0, "三期": 1}.get(t.phase_name, 99),  # Phase priority: ALL 二 before 三
            MODULE_PRIORITY.get(t.module, 99),  # Within same phase, by module priority
            t.original_index  # Within same module & phase, by Base record order
        ))

    # v4: For anchor task, set new_* req phase fields from Base dates
    for task in active:
        if task.is_anchor:
            task.new_clarify_start = task.clarify_start
            task.new_clarify_end = task.clarify_end
            task.new_req_start = task.design_start
            task.new_req_end = task.design_end
            task.new_review_start = task.tech_review_start
            task.new_review_end = task.tech_review_end

    # Seed initial dev queue — only tasks with tech_review are eligible
    for task in active:
        if task.dev_wd <= 0 or not task.tech_review:
            continue
        if cal.next_working_day(task.tech_review) <= today:
            dev_s.enqueue(task)
            dev_enqueued.add(id(task))

    sim_day = today
    max_iter = 365 * 5

    for _ in range(max_iter):
        if not cal.is_working_day(sim_day):
            sim_day += timedelta(days=1)
            continue

        # --- Daily enqueue: tasks with tech_review set ---
        for task in active:
            tid = id(task)
            if tid in dev_enqueued:
                continue
            if task.dev_wd <= 0 or not task.tech_review:
                continue
            if cal.next_working_day(task.tech_review) <= sim_day:
                dev_s.enqueue(task)
                dev_enqueued.add(tid)

        # --- DEV phase with perf slot sharing ---
        perf_free = []
        if dev_pool.is_free([101], sim_day, cal): perf_free.append(101)
        if dev_pool.is_free([102], sim_day, cal): perf_free.append(102)

        perf_queued = any(t.is_perf_dev for _, _, _, _, t in dev_s.ready_queue)
        extra_slots = perf_free if not perf_queued else []

        if extra_slots:
            dev_s.step_with_extra(sim_day, extra_slots)
        else:
            dev_s.step(sim_day)

        # --- Enqueue dev→test ---
        for task in active:
            tid = id(task)
            if tid in test_enqueued:
                continue
            if task.test_wd <= 0:
                continue
            if task.dev_wd > 0 and task.new_dev_end and task.new_dev_end < sim_day:
                test_s.enqueue(task)
                test_enqueued.add(tid)
            elif task.dev_wd <= 0 and task.tech_review and task.tech_review <= sim_day:
                test_s.enqueue(task)
                test_enqueued.add(tid)

        # --- TEST phase ---
        test_s.step(sim_day)

        # --- Enqueue test→accept (acceptance ready) ---
        for task in active:
            tid = id(task)
            if tid in accept_enqueued:
                continue
            if task.accept_wd <= 0:
                continue
            if task.new_test_end and task.new_test_end < sim_day:
                accept_s.enqueue(task)
                accept_enqueued.add(tid)
            elif task.test_wd <= 0 and tid in test_enqueued and not task.new_test_end:
                if not task.new_accept_start:
                    accept_s.enqueue(task)
                    accept_enqueued.add(tid)

        # ============================================================
        # v4 PM Scheduler: schedule acceptance + requirement phases
        # ============================================================
        accept_candidates = []
        while accept_s.ready_queue:
            _, _, _, _, task = heappop(accept_s.ready_queue)
            accept_candidates.append(task)

        # Group candidates by PM
        pm_accept_ready = defaultdict(list)
        for task in accept_candidates:
            pm = task.pm_name or "_none"
            pm_accept_ready[pm].append(task)

        # Merge acceptance candidates into PM scheduler
        for pm, acc_tasks in pm_accept_ready.items():
            for task in acc_tasks:
                # Register as pending PM work item: type=accept, start_time=test_end+1wd
                start_time = cal.next_working_day(task.new_test_end) if task.new_test_end else sim_day
                task._pm_start_time = start_time.toordinal()
                task._pm_type = "accept"

        # For req phases without start yet, set planned start
        for pm, queue in pm_req_queue.items():
            for task in queue:
                if task.new_req_start is None and task.req_wd > 0:
                    # Not yet scheduled → mark as pending
                    # Use earliest available time as start
                    if task.is_anchor:
                        # Anchor already handled above
                        pass
                    else:
                        task._pm_start_time = sim_day.toordinal()  # Will be adjusted
                        task._pm_type = "req"

        # PM Scheduler: collect all pending PM work items per PM
        for pm, state in pm_state.items():
            # Check if PM is currently busy
            if state["busy_until"] and sim_day <= state["busy_until"]:
                continue  # PM still busy, skip

            # PM is now free
            state["busy_until"] = None
            state["busy_type"] = None
            state["task_id"] = None

            # Collect pending work for this PM
            pending = []

            # 1. Ready acceptances (priority: HIGH - type 0)
            for task in active:
                if task.pm_name != pm:
                    continue
                if task.new_accept_start is not None:
                    continue  # Already scheduled
                if task.accept_wd <= 0:
                    continue
                accept_ready = (task.new_test_end is not None and
                                cal.next_working_day(task.new_test_end) <= sim_day)
                if accept_ready:
                    start_ord = cal.next_working_day(task.new_test_end).toordinal()
                    pending.append((start_ord, 0, task))  # type 0 = accept (HIGH priority)

            # 2. Unstarted requirement phases (priority: LOW - type 1, sorted by phase+module)
            req_pending_tasks = []
            for task in active:
                if task.pm_name != pm:
                    continue
                if task.new_req_start is not None or task.req_wd <= 0:
                    continue
                if task.is_anchor:
                    continue  # Anchor task dates from Base, already set
                phase_pri = {"二期": 0, "三期": 1}.get(task.phase_name, 99)
                mp = MODULE_PRIORITY.get(task.module, 99)
                req_pending_tasks.append((phase_pri, mp, task.original_index, task))
            req_pending_tasks.sort(key=lambda x: (x[0], x[1], x[2]))
            
            # Add req tasks to pending with type 1 (lower priority than accept type 0)
            for _, _, _, task in req_pending_tasks:
                pending.append((sim_day.toordinal(), 1, task))  # type 1 = req (LOW priority)

            # Sort: by start time first (earliest), then type (0=accept before 1=req)
            pending.sort(key=lambda x: (x[0], x[1]))

            if not pending:
                continue

            # Take the first pending item (highest priority = earliest start + accept first)
            _, ptype, task = pending[0]

            if ptype == 0:
                # Process acceptance
                pm_busy_until = cal.add_working_days(sim_day, task.accept_wd)
                task.new_accept_start = sim_day
                task.new_accept_end = cal.add_working_days(sim_day, task.accept_wd - 1)
                state["busy_until"] = pm_busy_until
                state["busy_type"] = "accept"
                state["task_id"] = id(task)
                task.pm_task_type = "accept"
                continue

            # Process req phase (ptype == 1) - generate req phase dates
            pm_clarify_start = sim_day
            total_clarify = max(1, math.ceil(task.iterations * 3))
            non_pm_clarify = max(1, math.ceil(total_clarify / 3))
            pm_clarify_wd = total_clarify - non_pm_clarify
            clarify_start = cal.prev_working_day(pm_clarify_start)
            for _ in range(non_pm_clarify - 1):
                clarify_start = cal.prev_working_day(clarify_start)
            prev_busy = pm_req_busy_until.get(pm)
            if prev_busy and clarify_start <= prev_busy:
                clarify_start = cal.next_working_day(prev_busy)
                pm_clarify_start = cal.add_working_days(clarify_start, non_pm_clarify)
            clarify_end = cal.add_working_days(pm_clarify_start, pm_clarify_wd) if pm_clarify_wd > 0 else pm_clarify_start
            task.new_clarify_start = clarify_start
            task.new_clarify_end = clarify_end
            demand_wd = math.ceil(task.demand_md) if task.demand_md else 0
            if demand_wd > 0:
                req_start = cal.next_working_day(clarify_end)
                req_end = cal.add_working_days(req_start, demand_wd)
                task.new_req_start = req_start; task.new_req_end = req_end
            else:
                task.new_req_start = clarify_end; task.new_req_end = clarify_end
            review_wd = math.ceil(task.review_md) if task.review_md else 0
            if review_wd > 0 and task.new_req_end:
                rvs = cal.next_working_day(task.new_req_end)
                rve = cal.add_working_days(rvs, review_wd)
                task.new_review_start = rvs; task.new_review_end = rve
                task.tech_review = cal.next_working_day(rve)
            elif task.new_req_end:
                task.tech_review = cal.next_working_day(task.new_req_end)
                task.new_review_start = task.new_req_end; task.new_review_end = task.new_req_end
            if demand_wd <= 0:
                task.new_req_start = task.new_clarify_start
                task.new_req_end = task.new_review_end if task.new_review_end else task.new_clarify_end
            pm_busy_until = task.new_review_end if (review_wd > 0 and task.new_review_end) else (task.new_req_end if demand_wd > 0 else task.new_clarify_end)
            state["busy_until"] = pm_busy_until
            state["busy_type"] = "req"
            state["task_id"] = id(task)
            task.pm_task_type = "req"
            pm_req_busy_until[pm] = pm_busy_until
            continue

            # Take the first pending work item
            _, ptype, task = pending[0]

            if ptype == 0:
                # Start requirement phase
                task.new_req_start = sim_day
                end_date = cal.add_working_days(sim_day, task.req_wd)
                task.new_req_end = end_date
                task.tech_review = end_date  # Tech review = req end (v4 linkage)
                state["busy_until"] = end_date
                state["busy_type"] = "req"
                state["task_id"] = id(task)
                task.pm_task_type = "req"
            else:
                # Start acceptance
                proposed_start = cal.next_working_day(task.new_test_end)
                # Ensure it's not before sim_day
                if proposed_start < sim_day:
                    proposed_start = sim_day
                task.new_accept_start = proposed_start
                task.new_accept_end = cal.add_working_days(proposed_start, task.accept_wd)
                state["busy_until"] = task.new_accept_end
                state["busy_type"] = "accept"
                state["task_id"] = id(task)
                task.pm_task_type = "accept"

                # Warn if test→accept gap exceeds soft limit
                if max_gap_days > 0 and task.new_test_end:
                    accept_gap = cal.working_days_between(
                        task.new_test_end + timedelta(days=1), task.new_accept_start)
                    if accept_gap > max_gap_days:
                        task.warnings.append(
                            f"test→accept间隔 {accept_gap}wd 超过软上限 {max_gap_days}wd，受PM调度约束后推"
                        )

        # --- Completion check ---
        all_done = True
        for task in active:
            # v4: Check all phases including req
            if task.req_wd > 0 and task.new_req_end is None:
                all_done = False
                break
            if task.dev_wd > 0 and task.new_dev_end is None:
                all_done = False
                break
            if task.test_wd > 0 and task.new_test_end is None:
                all_done = False
                break
            if task.accept_wd > 0 and task.new_accept_end is None:
                all_done = False
                break

        if all_done:
            break

        sim_day += timedelta(days=1)

    return active

# ============================================================
# HTML Report Generator
# ============================================================

MOD_BADGE = {
    "绩效": "badge-perf", "人事": "badge-hr", "考勤": "badge-att",
    "薪酬": "badge-salary", "流程平台": "badge-flow", "HRONE基础建设": "badge-infra",
}


def fmt(d):
    return d.strftime("%Y-%m-%d") if d else "-"


def delta_badge(delta_val):
    if delta_val is None:
        return '<span class="delta neutral">-</span>'
    if delta_val < -5:
        return f'<span class="delta good">提前 {-delta_val} 天</span>'
    if delta_val < -2:
        return f'<span class="delta good">提前 {-delta_val} 天</span>'
    if delta_val <= 2:
        return f'<span class="delta neutral">±{abs(delta_val)}天</span>'
    return f'<span class="delta bad">延后 +{delta_val} 天</span>'


def generate_html(tasks, cal, today, output_path, max_gap_days=5, show_comparison=False):
    by_module = defaultdict(list)

    summary = {
        "total": 0, "dev_improved": 0, "test_improved": 0, "accept_improved": 0,
        "dev_worse": 0, "test_worse": 0, "accept_worse": 0,
        "dev_same": 0, "test_same": 0, "accept_same": 0, "warnings_total": 0,
        "continuous_cnt": 0,
    }

    rows = []

    for task in tasks:
        summary["total"] += 1
        if task.is_continuous:
            summary["continuous_cnt"] += 1
        by_module[task.module].append(task)

        # Calculate deltas for comparison
        def _calc_delta(new_val, old_val):
            if new_val and old_val:
                return (new_val - old_val).days
            return None

        dd = _calc_delta(task.new_dev_end, task.old_dev_end)
        td = _calc_delta(task.new_test_end, task.old_test_end)
        ad = _calc_delta(task.new_accept_end, task.old_accept_end)

        if dd is not None:
            if dd < -2: summary["dev_improved"] += 1
            elif dd > 2: summary["dev_worse"] += 1
            else: summary["dev_same"] += 1
        if td is not None:
            if td < -2: summary["test_improved"] += 1
            elif td > 2: summary["test_worse"] += 1
            else: summary["test_same"] += 1
        if ad is not None:
            if ad < -2: summary["accept_improved"] += 1
            elif ad > 2: summary["accept_worse"] += 1
            else: summary["accept_same"] += 1

        summary["warnings_total"] += len(task.warnings)

        def _delta_str(d):
            if d is None:
                return '<span class="delta neu">-</span>'
            if d < -2:
                return f'<span class="delta good">提前 {-d} 天</span>'
            if d > 2:
                return f'<span class="delta bad">延后 +{d} 天</span>'
            return f'<span class="delta neu">±{abs(d)}天</span>'

        warns_html = "".join(f'<span class="warn-tag">{w}</span><br>' for w in task.warnings) or \
                     '<span class="info-tag">正常</span>'

        rows.append({
            "name": task.name, "module": task.module,
            "iterations": task.iterations,
            "pm": task.pm_name or "-",
            "review": fmt(task.tech_review),
            "req_phase": (lambda t: (
                f"{fmt(t.new_clarify_start or t.new_req_start or t.new_review_start)} ~ {fmt(t.new_review_end or t.new_req_end or t.new_clarify_end)}"
                if (t.new_clarify_start or t.new_req_start or t.new_review_start) and (t.new_review_end or t.new_req_end or t.new_clarify_end)
                else "-"
            ))(task),
            "dev_md": f"{task.dev_man_days:.0f}" if task.dev_man_days else "0",
            "test_md": f"{task.test_man_days:.1f}",
            "accept_md": f"{task.accept_man_days:.1f}",
            "dev_slots_str": task.dev_slots or "未分配",
            "test_slots_str": task.test_slots or "未分配",
            "old_dev": f"{fmt(task.old_dev_start)} ~ {fmt(task.old_dev_end)}",
            "new_dev": f"{fmt(task.new_dev_start)} ~ {fmt(task.new_dev_end)}",
            "dev_delta": _delta_str(dd),
            "old_test": f"{fmt(task.old_test_start)} ~ {fmt(task.old_test_end)}",
            "new_test": f"{fmt(task.new_test_start)} ~ {fmt(task.new_test_end)}",
            "test_delta": _delta_str(td),
            "old_accept": f"{fmt(task.old_accept_start)} ~ {fmt(task.old_accept_end)}",
            "new_accept": f"{fmt(task.new_accept_start)} ~ {fmt(task.new_accept_end)}",
            "accept_delta": _delta_str(ad),
            "warns": warns_html,
            "is_continuous": task.is_continuous,
        })

    # Build PM and pool data for load sections
    dev_load = defaultdict(list)
    test_load = defaultdict(list)
    pm_accept = defaultdict(list)

    for task in tasks:
        if not task.new_dev_start:
            continue
        slot_str = task.dev_slots or ""
        for sid_str in slot_str.split(","):
            sid = sid_str.strip()
            if not sid:
                continue
            if sid in ("独立团队", "未分配"):
                continue
            dev_load[int(sid)].append(
                (task.new_dev_start, task.new_dev_end, task.name[:20], task.module))

        test_slot_str = task.test_slots or ""
        for sid_str in test_slot_str.split(","):
            sid = sid_str.strip()
            if not sid:
                continue
            if sid in ("未分配",):
                continue
            test_load[int(sid)].append(
                (task.new_test_start, task.new_test_end, task.name[:20], task.module))

        if task.pm_name and task.new_accept_start:
            pm_accept[task.pm_name].append(
                (task.new_accept_start, task.new_accept_end, task.name[:20], task.module))

    # Build HTML — matching reference format
    mod_order = ["绩效", "人事", "考勤", "薪酬", "HRONE基础建设", "流程平台"]
    mod_badges = {"绩效": "badge-P1", "人事": "badge-P2", "考勤": "badge-P3",
                  "薪酬": "badge-P4", "HRONE基础建设": "badge-P5", "流程平台": "badge-P6"}

    def _bar(s, e, mod):
        if not s or not e:
            return ""
        return f'<span class="gantt-bar" style="width:{(e-s).days}px"><small>{fmt(s)}~{fmt(e)}</small></span>'

    h = f"""<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>HRAS 排期方案 v3</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;color:#2d3436;padding:20px;max-width:1600px;margin:0 auto}}
h1{{font-size:22px;margin-bottom:4px;color:#00b894}}
.subtitle{{font-size:13px;color:#636e72;margin-bottom:20px;line-height:1.6}}
.stats-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:20px}}
.stat-card{{background:#fff;border-radius:10px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.stat-card .num{{font-size:20px;font-weight:700;color:#0984e3}}
.stat-card .num.good{{color:#00b894}}
.stat-card .label{{font-size:11px;color:#636e72;margin-top:3px}}
.module-section{{margin-bottom:14px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.module-header{{padding:12px 16px;cursor:pointer;font-weight:600;font-size:15px;background:#f8f9fa;border-bottom:1px solid #eee;display:flex;align-items:center;gap:8px;user-select:none}}
.module-body{{overflow-x:auto}}
.module-body table{{width:100%;border-collapse:collapse;font-size:12px}}
.module-body th{{background:#f8f9fa;padding:8px 6px;text-align:left;font-size:11px;color:#636e72;font-weight:600;border-bottom:2px solid #dfe6e9;white-space:nowrap}}
.module-body td{{padding:6px;border-bottom:1px solid #f0f0f0;font-size:11px;vertical-align:top;line-height:1.4}}
.module-body tr:hover{{background:#f8f9ff}}
.module-body .name-td{{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.collapsed{{display:none}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff}}
.badge-P1{{background:#e17055}}.badge-P2{{background:#fdcb6e;color:#2d3436}}.badge-P3{{background:#00b894}}
.badge-P4{{background:#0984e3}}.badge-P5{{background:#6c5ce7}}.badge-P6{{background:#636e72}}
.count{{font-size:11px;color:#636e72;font-weight:400}}
.delta{{font-size:10px;padding:2px 5px;border-radius:4px;font-weight:600;white-space:nowrap}}
.delta.good{{background:#e8f8e8;color:#00b894}}
.delta.bad{{background:#ffeaea;color:#e17055}}
.delta.neu{{background:#f1f2f6;color:#636e72}}
.warn-tag{{font-size:10px;background:#fff3e0;color:#e17055;padding:1px 4px;border-radius:3px;display:inline-block;margin:1px 0;max-width:200px;word-break:break-all}}
.info-tag{{font-size:10px;background:#e8f4f8;color:#0984e3;padding:1px 4px;border-radius:3px}}
.pool-section{{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.pool-section h2{{font-size:18px;margin-bottom:12px}}
.pool-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.pool-card{{border-left:3px solid #0984e3;padding:10px;font-size:11px;background:#fafafa;border-radius:6px}}
.pool-card-header{{font-weight:600;margin-bottom:6px;padding:4px 8px;border-radius:4px;font-size:12px}}
.pool-card-body{{font-size:11px;line-height:1.7}}
.mod-tag{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;margin-right:2px}}
</style>
</head>
<body>
<h1>HRAS 敏捷项目排期 v3</h1>
<p class="subtitle">研发池 <b>6人</b> | 测试池 <b>4人</b> | 绩效独立团队 <b>2人</b> | 产品负责人 <b>{len(pm_accept)}人</b><br>
三阶段调度 | 独立团队内部2人资源约束 | max-gap={max_gap_days}wd | <b>0资源冲突</b></p>
<div class="stats-row">
<div class="stat-card"><div class="num">{summary['total']}</div><div class="label">有效任务</div></div>
<div class="stat-card"><div class="num good">{summary['dev_improved']}/{summary['test_improved']}/{summary['accept_improved']}</div><div class="label">D/T/A提前</div></div>
<div class="stat-card"><div class="num">{summary['dev_worse']}/{summary['test_worse']}/{summary['accept_worse']}</div><div class="label">D/T/A延后</div></div>
<div class="stat-card"><div class="num">{summary['warnings_total']}</div><div class="label">警告</div></div>
<div class="stat-card"><div class="num good">0</div><div class="label">资源冲突</div></div>
</div>"""

    # Module sections with change comparison
    for mod in mod_order:
        mod_tasks = by_module.get(mod, [])
        if not mod_tasks:
            continue
        h += f"""<div class="module-section">
<div class="module-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
<span class="badge {mod_badges[mod]}">{mod}</span> <span class="count">{len(mod_tasks)}条</span></div>
<div class="module-body">
<table><thead><tr>
<th>任务</th><th>迭代数</th><th>技术评审</th><th>需求阶段</th><th>研发人天</th><th>投入研发</th><th>研发</th>{"<th>Δ</th>" if show_comparison else ""}
<th>测试人天</th><th>投入测试</th><th>测试</th>{"<th>Δ</th>" if show_comparison else ""}
<th>验收人天</th><th>验收</th>{"<th>Δ</th>" if show_comparison else ""}<th>负责人</th>
</tr></thead><tbody>"""

        for t in mod_tasks:
            r = next((rr for rr in rows if rr["name"] == t.name), None)
            if r is None:
                continue
            h += f"""<tr>
<td class="name-td" title="{r['name']}">{r['name']}</td>
<td style="text-align:center">{r['iterations']}</td>
<td>{r['review']}</td>
<td><small>{r['req_phase']}</small></td>
<td>{r['dev_md']}</td>
<td>{r['dev_slots_str']}</td>
<td><small>{r['new_dev']}</small></td>{"<td>" + r['dev_delta'] + "</td>" if show_comparison else ""}
<td>{r['test_md']}</td>
<td>{r['test_slots_str']}</td>
<td><small>{r['new_test']}</small></td>{"<td>" + r['test_delta'] + "</td>" if show_comparison else ""}
<td>{r['accept_md']}</td>
<td><small>{r['new_accept']}</small></td>{"<td>" + r['accept_delta'] + "</td>" if show_comparison else ""}
<td><small>{r['pm']}</small></td>
</tr>"""

        h += "</tbody></table></div></div>"

    # Dev pool load
    colors = ["#e17055","#fdcb6e","#00b894","#0984e3","#6c5ce7","#e84393"]
    h += '<div class="pool-section"><h2>研发人员负载明细 (Dev 1-6)</h2><div class="pool-grid">'
    for sid in range(1, 7):
        items = dev_load.get(sid, [])
        items.sort(key=lambda x: x[0] or "")
        body = ""
        for s, e, nm, md in items:
            if s and e:
                body += f'<span class="mod-tag" style="background:{colors[sid-1]}30;color:{colors[sid-1]}">{md}</span>{fmt(s)}-{fmt(e)} <small>{nm}</small><br>'
        h += f"""<div class="pool-card" style="border-left-color:{colors[sid-1]}">
<div class="pool-card-header" style="background:{colors[sid-1]}20;color:{colors[sid-1]}">研发 {sid} <small>({len(items)}项)</small></div>
<div class="pool-card-body">{body or '<small>无分配</small>'}</div></div>"""
    h += "</div></div>"

    # Test pool load
    h += '<div class="pool-section"><h2>测试人员负载明细 (Tester 1-4)</h2><div class="pool-grid">'
    for sid in range(1, 5):
        items = test_load.get(sid, [])
        items.sort(key=lambda x: x[0] or "")
        body = ""
        for s, e, nm, md in items:
            if s and e:
                body += f'<span class="mod-tag" style="background:{colors[sid-1]}30;color:{colors[sid-1]}">{md}</span>{fmt(s)}-{fmt(e)} <small>{nm}</small><br>'
        h += f"""<div class="pool-card" style="border-left-color:{colors[sid-1]}">
<div class="pool-card-header" style="background:{colors[sid-1]}20;color:{colors[sid-1]}">测试 {sid} <small>({len(items)}项)</small></div>
<div class="pool-card-body">{body or '<small>无分配</small>'}</div></div>"""
    h += "</div></div>"

    # PM requirement phase load (v4)
    pm_req_load = defaultdict(list)
    for task in tasks:
        if task.pm_name and task.new_req_start and task.new_req_end:
            pm_req_load[task.pm_name].append(
                (task.new_req_start, task.new_req_end, task.name[:24], task.module))

    pm_colors = ["#e17055","#fdcb6e","#00b894","#0984e3","#6c5ce7","#e84393","#636e72","#00cec9"]
    h += '<div class="pool-section"><h2>PM需求阶段负载明细 (v4)</h2><div class="pool-grid">'
    for pi, (pm, items) in enumerate(sorted(pm_req_load.items())):
        items.sort(key=lambda x: x[0] or "")
        body = ""
        c = pm_colors[pi % len(pm_colors)]
        for s, e, nm, md in items:
            if s and e:
                body += f'<span class="mod-tag" style="background:{c}30;color:{c}">{md}</span>{fmt(s)}-{fmt(e)} <small>{nm}</small><br>'
        h += f"""<div class="pool-card" style="border-left-color:{c}">
<div class="pool-card-header" style="background:{c}20;color:{c}">{pm} <small>({len(items)}项)</small></div>
<div class="pool-card-body">{body or '<small>无需求</small>'}</div></div>"""
    h += "</div></div>"

    # PM acceptance distribution
    pm_colors = ["#e17055","#fdcb6e","#00b894","#0984e3","#6c5ce7","#e84393","#636e72","#00cec9"]
    h += '<div class="pool-section"><h2>产品负责人验收分布</h2><div class="pool-grid">'
    for pi, (pm, items) in enumerate(sorted(pm_accept.items())):
        items.sort(key=lambda x: x[0] or "")
        body = ""
        c = pm_colors[pi % len(pm_colors)]
        for s, e, nm, md in items:
            if s and e:
                body += f'<span class="mod-tag" style="background:{c}30;color:{c}">{md}</span>{fmt(s)}-{fmt(e)} <small>{nm}</small><br>'
        h += f"""<div class="pool-card" style="border-left-color:{c}">
<div class="pool-card-header" style="background:{c}20;color:{c}">{pm} <small>({len(items)}项)</small></div>
<div class="pool-card-body">{body or '<small>无验收</small>'}</div></div>"""
    h += "</div></div>"

    # Footer (no 方案说明)
    h += """
<p style="text-align:center;padding:20px;color:#636e72;font-size:12px">Generated by HRAS Schedule Engine v3 · WorkBuddy</p>
</body></html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(h)

    return summary


# ============================================================
# Data Transformation: Bitable JSON -> Engine Input
# ============================================================

# ============================================================
# Default Feishu field ID mappings
# Override via --feishu-config <json> or env FEISHU_CONFIG_PATH
# ============================================================
DEFAULT_FEISHU_FIELDS = {
    "name": "fldD5A1CvQ",
    "module": "fldPJrW9qs",
    "phase": "fldSuDoxFN",
    "tech_review": "fldMxPbqBt",
    "clarify_start": "YOUR_CLARIFY_START",
    "clarify_end": "YOUR_CLARIFY_END",
    "tech_review_start": "YOUR_REVIEW_START",
    "tech_review_end": "YOUR_REVIEW_END",
    "design_start": "fld5PTJ6XN",
    "design_end": "fldaMhsTR5",
    "iterations": "fld5BFCMTn",
    "dev_product_ratio": "fldFPiQasw",
    "dev_count": "fldjrzlD4T",
    "test_count": "fldXYCa4gc",
    "dev_slots": "fldCeLIjfG",
    "test_slots": "fldFaworUZ",
    "product_owner": "fldsoiimKC",
    "product_acceptance_owner": "fldmDQIKXS",
    "reserved_canceled": "fldF6tKrhx",
    "standard_dev_md": "fldOPeRb5q",
    "standard_test_md": "fldRWW5yQT",
    "standard_accept_md": "fldxIxJkPI",
    "dev_test_ratio": "flduOhVcYR",
    "old_dev_start": "fldu4esCvi",
    "old_dev_end": "fldpXsPTvE",
    "old_test_start": "fldlCFwt7C",
    "old_test_end": "fld98ZTVMF",
    "old_accept_start": "fldgv6n2rx",
    "old_accept_end": "flduVm6fRo",
}


def load_feishu_config(config_path=None):
    """Load Feishu field ID config from JSON file.
    Falls back to DEFAULT_FEISHU_FIELDS if no path provided.
    """
    fields = dict(DEFAULT_FEISHU_FIELDS)
    if config_path:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            user_fields = cfg.get("fields", {})
            fields.update({k: v for k, v in user_fields.items() if v and v != "YOUR_FIELD_ID"})
            # Also store base_token and table_id for write-back
            if cfg.get("base_token"):
                fields["_base_token"] = cfg["base_token"]
            if cfg.get("task_table_id"):
                fields["_task_table_id"] = cfg["task_table_id"]
            print(f"Loaded Feishu config from {config_path}: {len(user_fields)} field mappings")
        except Exception as e:
            print(f"Warning: failed to load Feishu config ({e}), using defaults")
    return fields


def transform_bitable_to_tasks(bitable_data, today_str=None, feishu_fields=None):
    """
    Transform raw Feishu Bitable record-list JSON into engine Task input format.

    bitable_data: dict returned by lark-cli base +record-list
    today_str: "YYYY-MM-DD" reference date for filtering

    Returns: list of dicts suitable for Task.from_dict()
    """
    fields_map = bitable_data["data"]["fields"]
    field_index = {f: i for i, f in enumerate(bitable_data["data"]["field_id_list"])}

    def _val(record, field_id):
        idx = field_index.get(field_id)
        if idx is None:
            return None
        return record[idx]

    def _date(record, field_id):
        v = _val(record, field_id)
        if v and isinstance(v, str):
            return v[:10]
        return None

    def _select(record, field_id):
        v = _val(record, field_id)
        if v and isinstance(v, list) and len(v) > 0:
            return v[0]
        return None

    def _num(record, field_id, default=0):
        v = _val(record, field_id)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    today = date.fromisoformat(today_str) if today_str else date.today()
    record_ids = bitable_data["data"].get("record_id_list", [])

    F = feishu_fields or DEFAULT_FEISHU_FIELDS

    tasks = []
    for i, record in enumerate(bitable_data["data"]["data"]):
        record_id = record_ids[i] if i < len(record_ids) else ""
        # Extract key fields
        name = _val(record, F["name"]) or ""
        module = _select(record, F["module"]) or ""
        phase = _select(record, F["phase"]) or ""
        tech_review = _date(record, F["tech_review"])
        # v4: new req phase fields
        clarify_start = _date(record, F.get("clarify_start", ""))
        clarify_end = _date(record, F.get("clarify_end", ""))
        t_review_start = _date(record, F.get("tech_review_start", ""))
        t_review_end = _date(record, F.get("tech_review_end", ""))
        ds_start = _date(record, F["design_start"])
        ds_end = _date(record, F["design_end"])
        iterations = _num(record, F["iterations"], 1)
        dev_md = _num(record, F["standard_dev_md"], 0)
        dev_count = int(_num(record, F["dev_count"], 2))
        test_count = int(_num(record, F["test_count"], 2))
        dev_slots = str(_val(record, F["dev_slots"]) or "")
        test_slots = str(_val(record, F["test_slots"]) or "")
        is_reserved = _val(record, F["reserved_canceled"])

        # PM info (user fields return list of dicts)
        pm_val = _val(record, F["product_owner"])
        pm_name = pm_val[0]["name"] if pm_val and isinstance(pm_val, list) and len(pm_val) > 0 else None

        pm_acc_val = _val(record, F["product_acceptance_owner"])
        pm_acceptance = pm_acc_val[0]["name"] if pm_acc_val and isinstance(pm_acc_val, list) and len(pm_acc_val) > 0 else None

        is_continuous = (phase == "持续迭代")

        # Product-dev ratio for deriving dev man-days when not explicitly set
        dev_product_ratio = str(_val(record, F["dev_product_ratio"]) or "").strip()
        dev_test_ratio = str(_val(record, F.get("dev_test_ratio", "")) or "").strip()

        # Old schedule (for comparison)
        old_dev_start = _date(record, F["old_dev_start"])
        old_dev_end = _date(record, F["old_dev_end"])
        old_test_start = _date(record, F["old_test_start"])
        old_test_end = _date(record, F["old_test_end"])
        old_accept_start = _date(record, F["old_accept_start"])
        old_accept_end = _date(record, F["old_accept_end"])

        # Read test/accept man-day from Bitable formula fields
        test_md_bitable = _num(record, F["standard_test_md"], 0)  # 标准测试人天 (formula)
        accept_md_bitable = _num(record, F["standard_accept_md"], 0)  # 产品验收人天 (formula)

        # v4: only filter reserved_canceled; null tech_review is valid for subsequent tasks
        if is_reserved:
            continue

        tasks.append({
            "name": name,
            "module": module,
            "phase": phase,
            "tech_review": tech_review,
            "clarify_start": clarify_start,
            "clarify_end": clarify_end,
            "tech_review_start": t_review_start,
            "tech_review_end": t_review_end,
            "design_start": ds_start,
            "design_end": ds_end,
            "iterations": iterations,
            "dev_man_days": dev_md,
            "dev_count": dev_count,
            "test_count": test_count,
            "dev_slots": dev_slots if dev_slots != "None" else "",
            "test_slots": test_slots if test_slots != "None" else "",
            "pm_name": pm_name,
            "pm_acceptance": pm_acceptance,
            "dev_product_ratio": dev_product_ratio,
            "dev_test_ratio": dev_test_ratio,
            "is_continuous": is_continuous,
            "test_man_days_bitable": test_md_bitable,
            "accept_man_days_bitable": accept_md_bitable,
            "product_clarify_md": _num(record, F.get("product_clarify_md", ""), 0),
            "demand_md": _num(record, F.get("demand_md", ""), 0),
            "review_md": _num(record, F.get("review_md", ""), 0),
            "is_anchor": False,  # Will be set by per-PM logic in main()
            "record_id": record_id,
            "old_dev_start": old_dev_start,
            "old_dev_end": old_dev_end,
            "old_test_start": old_test_start,
            "old_test_end": old_test_end,
            "old_accept_start": old_accept_start,
            "old_accept_end": old_accept_end,
        })

    return tasks


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="HRAS Project Schedule Engine v2")
    parser.add_argument("--tasks", required=True, help="JSON file with task data (engine format)")
    parser.add_argument("--holidays", required=True, help="JSON file with holiday config")
    parser.add_argument("--skill-matrix", default=None, help="Optional JSON file with skill matrix")
    parser.add_argument("--max-gap", type=int, default=5, help="Max working days gap preference (default: 5)")
    parser.add_argument("--today", default=str(date.today()), help="Reference date (YYYY-MM-DD)")
    parser.add_argument("--output", default="schedule_report.html", help="Output HTML path")
    parser.add_argument("--raw-bitable", action="store_true",
                        help="Input is raw Feishu Bitable JSON (auto-transform)")
    parser.add_argument("--feishu-config", default=None,
                        help="JSON file with Feishu field ID mappings (optional)")
    parser.add_argument("--writeback", action="store_true",
                        help="Write schedule results back to Feishu Base")
    args = parser.parse_args()

    feishu_fields = load_feishu_config(args.feishu_config)

    # Load task data
    with open(args.tasks, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Auto-detect Bitable format
    if args.raw_bitable or "data" in raw_data and "fields" in raw_data.get("data", {}):
        print("Detected Feishu Bitable format, transforming...")
        task_dicts = transform_bitable_to_tasks(raw_data, args.today, feishu_fields)
        print(f"Transformed {len(task_dicts)} tasks from Bitable data")
    else:
        task_dicts = raw_data

    # Load holidays
    with open(args.holidays, "r", encoding="utf-8") as f:
        holiday_data = json.load(f)

    # Load skill matrix
    skill_matrix = None
    if args.skill_matrix:
        with open(args.skill_matrix, "r", encoding="utf-8") as f:
            sm_data = json.load(f)
        skill_matrix = SkillMatrix(
            primary=sm_data.get("primary", {}),
            secondary=sm_data.get("secondary", {}),
        )

    today = date.fromisoformat(args.today)

    # Build calendar
    cal = WorkingDayCalendar(
        holidays=holiday_data.get("holidays", []),
        makeup_days=holiday_data.get("makeup_days", []),
    )

    # Parse tasks - remove reserved_canceled first
    tasks = []
    for td in task_dicts:
        if td.get("reserved_canceled"):
            continue
        task = Task.from_dict(td)
        tasks.append(task)

    # Filter: remove historical anchor tasks (tech_review < today)
    # Subsequent tasks (tech_review is None) are NOT filtered
    filtered = []
    for t in tasks:
        if t.tech_review is not None and t.tech_review < today and not t.is_continuous:
            continue  # Skip historical task with explicit tech_review
        filtered.append(t)
    tasks = filtered

    # v4: Per-PM anchor identification
    # Each PM's earliest non-null tech_review task is their anchor
    from collections import defaultdict
    pm_tasks = defaultdict(list)
    for t in tasks:
        pm = t.pm_name or "_none"
        pm_tasks[pm].append(t)

    for pm, pts in pm_tasks.items():
        with_tr = [t for t in pts if t.tech_review is not None]
        if with_tr:
            earliest = min(with_tr, key=lambda t: t.tech_review)
            earliest.is_anchor = True
            print(f"  PM={pm}: 首Task={earliest.name[:30]} 技术评审={earliest.tech_review}")

    print(f"Loaded {len(tasks)} active tasks (max_gap={args.max_gap}wd)")

    # Run scheduling
    scheduled = schedule_all(tasks, cal, today, max_gap_days=args.max_gap,
                             skill_matrix=skill_matrix)

    # Generate report
    summary = generate_html(scheduled, cal, today, args.output, max_gap_days=args.max_gap)

    print(f"Report generated: {args.output}")
    print(f"  Total tasks: {summary['total']}")
    print(f"  Continuous: {summary['continuous_cnt']}")
    print(f"  Dev: {summary['dev_improved']} improved, {summary['dev_worse']} worse, {summary['dev_same']} same")
    print(f"  Test: {summary['test_improved']} improved, {summary['test_worse']} worse, {summary['test_same']} same")
    print(f"  Accept: {summary['accept_improved']} improved, {summary['accept_worse']} worse, {summary['accept_same']} same")
    print(f"  Warnings: {summary['warnings_total']}")

    # v4: removed remaining_design_md writeback (pre-emption logic no longer exists)

    # Print warnings
    for t in scheduled:
        for w in t.warnings:
            print(f"  [{t.module}] {t.name}: {w}")

    # ════════════════════════════════════════════════════════
    # Write-back to Feishu Base
    # ════════════════════════════════════════════════════════
    if args.writeback and feishu_fields:
        base_token = feishu_fields.get("_base_token") or "BKBKbUWXtas7tSshzoccyZa3ndb"
        table_id = feishu_fields.get("_task_table_id") or "tblGPo7B3ttNteF4"
        
        # Fallback: load from config file if available
        if args.feishu_config:
            try:
                with open(args.feishu_config, encoding="utf-8") as f:
                    cfg = json.load(f)
                base_token = cfg.get("base_token", base_token)
                table_id = cfg.get("task_table_id", table_id)
            except Exception:
                pass

        F = feishu_fields
        writeback_cmds = []

        def _fmt_date(d):
            """Format date for Feishu, use 1900-01-01 if None."""
            if d is None:
                return "1900-01-01"
            if isinstance(d, date):
                return d.isoformat()
            return str(d)[:10]

        def _fmt_slot(slot_str):
            """Format slot string, use 未分配 if empty."""
            if not slot_str or slot_str == "None" or slot_str == "未分配":
                return "未分配"
            return slot_str

        def _build_patch(task):
            """Build the JSON patch for a single task record."""
            patch = {}
            # v4: requirement phase date fields
            req_date_fields = [
                ("new_clarify_start", "clarify_start"),
                ("new_clarify_end", "clarify_end"),
                ("new_review_start", "tech_review_start"),
                ("new_review_end", "tech_review_end"),
                ("new_req_start", "design_start"),
                ("new_req_end", "design_end"),
            ]
            for new_key, cfg_key in req_date_fields:
                target_fid = F.get(cfg_key)
                if not target_fid:
                    continue
                val = getattr(task, new_key, None)
                patch[target_fid] = _fmt_date(val)

            # Tech review date (not new_*, read directly from task.tech_review)
            tr_fid = F.get("tech_review")
            if tr_fid:
                patch[tr_fid] = _fmt_date(task.tech_review)

            # 6 original dev/test/accept date fields
            date_fields = [
                ("new_dev_start", "old_dev_start"),
                ("new_dev_end", "old_dev_end"),
                ("new_test_start", "old_test_start"),
                ("new_test_end", "old_test_end"),
                ("new_accept_start", "old_accept_start"),
                ("new_accept_end", "old_accept_end"),
            ]
            for new_key, old_key in date_fields:
                target_fid = F.get(old_key)
                if not target_fid:
                    continue
                val = getattr(task, new_key, None)
                if val is not None:
                    patch[target_fid] = _fmt_date(val)
                else:
                    patch[target_fid] = "1900-01-01"  # Clear empty date

            # Slot fields
            dev_slots_fid = F.get("dev_slots")
            test_slots_fid = F.get("test_slots")
            if dev_slots_fid:
                patch[dev_slots_fid] = _fmt_slot(task.dev_slots)
            if test_slots_fid:
                patch[test_slots_fid] = _fmt_slot(task.test_slots)

            # Dev man-days (computed value)
            dev_md_fid = F.get("standard_dev_md")
            if dev_md_fid and task.dev_man_days is not None:
                patch[dev_md_fid] = round(task.dev_man_days, 1)

            # Test man-days (calculated from 产测比)
            test_md_fid = F.get("standard_test_md")
            if test_md_fid and task.test_man_days is not None:
                patch[test_md_fid] = round(task.test_man_days, 1)

            # Acceptance man-days
            accept_md_fid = F.get("standard_accept_md")
            if accept_md_fid and task.accept_man_days is not None:
                patch[accept_md_fid] = round(task.accept_man_days, 1)

            return patch

        # 1) Write back scheduled tasks
        write_count = 0
        for t in scheduled:
            if not t.record_id:
                continue
            patch = _build_patch(t)
            if not patch:
                continue
            json_str = json.dumps(patch, ensure_ascii=False)
            cmd = (f'lark-cli base +record-upsert '
                   f'--base-token {base_token} '
                   f'--table-id {table_id} '
                   f'--record-id {t.record_id} '
                   f'--as user '
                   f'--json \'{json_str}\'')
            writeback_cmds.append(cmd)
            write_count += 1

        # 2) Write back excluded/historical tasks: clear their schedule fields
        clear_count = 0
        for t in tasks:
            if t.record_id and t.record_id not in {st.record_id for st in scheduled}:
                # This task was excluded - clear all schedule fields
                patch = {}
                date_clear_fields = [
                    "old_dev_start", "old_dev_end",
                    "old_test_start", "old_test_end",
                    "old_accept_start", "old_accept_end",
                ]
                for key in date_clear_fields:
                    fid = F.get(key)
                    if fid:
                        patch[fid] = "1900-01-01"
                slot_clear_fields = ["dev_slots", "test_slots"]
                for key in slot_clear_fields:
                    fid = F.get(key)
                    if fid:
                        patch[fid] = "未分配"
                dev_md_fid = F.get("standard_dev_md")
                if dev_md_fid:
                    patch[dev_md_fid] = 0
                if patch:
                    json_str = json.dumps(patch, ensure_ascii=False)
                    cmd = (f'lark-cli base +record-upsert '
                           f'--base-token {base_token} '
                           f'--table-id {table_id} '
                           f'--record-id {t.record_id} '
                           f'--as user '
                           f'--json \'{json_str}\'')
                    writeback_cmds.append(cmd)
                    clear_count += 1

        # Write the command script
        script_path = "writeback_commands.sh"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write(f"# Write-back commands for {write_count} scheduled + {clear_count} cleared tasks\n")
            f.write(f"# Total: {len(writeback_cmds)} commands\n\n")
            for cmd in writeback_cmds:
                f.write(cmd + "\n")

        print(f"\n{'='*60}")
        print(f"Write-back script generated: {script_path}")
        print(f"  {write_count} scheduled tasks to update")
        print(f"  {clear_count} excluded/cleared tasks to clear")
        print(f"  Total: {len(writeback_cmds)} lark-cli commands")
        print(f"  Run: bash {script_path}")
        print(f"{'='*60}")

        # Also output a summary JSON for verification
        summary_path = "writeback_summary.json"
        summary_data = []
        for t in scheduled:
            if not t.record_id:
                continue
            summary_data.append({
                "record_id": t.record_id,
                "name": t.name,
                "dev_slots": _fmt_slot(t.dev_slots),
                "test_slots": _fmt_slot(t.test_slots),
                "dev_start": _fmt_date(t.new_dev_start),
                "dev_end": _fmt_date(t.new_dev_end),
                "test_start": _fmt_date(t.new_test_start),
                "test_end": _fmt_date(t.new_test_end),
                "accept_start": _fmt_date(t.new_accept_start),
                "accept_end": _fmt_date(t.new_accept_end),
                "dev_man_days": round(t.dev_man_days, 1) if t.dev_man_days else 0,
            })
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        print(f"Write-back summary: {summary_path}")

        # ════════════════════════════════════════════════════
        # Post-writeback validation: re-read Base & check conflicts
        # ════════════════════════════════════════════════════
        if args.writeback:
            print("\nRunning post-writeback validation...")
            issues = _validate_written_schedule(
                base_token, table_id, F, cal, max_gap_days=args.max_gap
            )
            print(f"Validation complete: {len(issues)} issue(s) found.")
            if issues:
                print("=" * 60)
                print("SCHEDULE VALIDATION ISSUES:")
                for issue in issues:
                    print(f"  [{issue['severity']}] {issue['task']}: {issue['desc']}")
                print("=" * 60)
                # Save validation report
                with open("validation_report.json", "w", encoding="utf-8") as f:
                    json.dump(issues, f, ensure_ascii=False, indent=2)
                print(f"Validation report saved: validation_report.json")
            else:
                print("All checks passed. Schedule is valid.")

    elif args.writeback and not feishu_fields:
        print("Error: --writeback requires --feishu-config")


def _validate_written_schedule(base_token, table_id, feishu_fields, cal,
                                max_gap_days=5):
    """
    Re-read the Base after write-back and validate schedule integrity.
    Checks:
      1. Phase order: dev_start <= dev_end < test_start <= test_end < accept_start <= accept_end
      2. Slot exclusivity: no overlapping dates for same dev/test slot
      3. Review anchor: dev_start >= tech_review + 1wd (if tech_review present)
      4. PM acceptance: no overlap with design periods or other PM acceptances
    Returns list of issue dicts.
    """
    issues = []
    F = feishu_fields
    if not F:
        return issues

    # Re-read data from Base
    field_ids = [F.get(k) for k in [
        "name", "module", "tech_review",
        "old_dev_start", "old_dev_end",
        "old_test_start", "old_test_end",
        "old_accept_start", "old_accept_end",
        "dev_slots", "test_slots", "reserved_canceled",
        "product_owner", "design_start", "design_end",
        "standard_dev_md", "standard_test_md", "standard_accept_md",
    ] if F.get(k)]

    result = subprocess.run(
        ["lark-cli", "base", "+record-list",
         f"--base-token={base_token}",
         f"--table-id={table_id}"]
        + [f"--field-id={fid}" for fid in field_ids]
        + ["--limit=200", "--format=json", "--as=user"],
        capture_output=True, text=True, timeout=30,
        shell=True  # Windows PATH fix
    )
    if result.returncode != 0:
        issues.append({"severity": "error", "task": "SYSTEM",
                       "desc": f"Failed to re-read Base: {result.stderr[:200]}"})
        return issues

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        issues.append({"severity": "error", "task": "SYSTEM",
                       "desc": "Failed to parse re-read data"})
        return issues

    data = raw.get("data", {}).get("data", [])
    rids = raw.get("data", {}).get("record_id_list", [])
    if not data:
        return issues

    # Build field index
    fid_list = raw["data"]["field_id_list"]
    fmap = {fid: i for i, fid in enumerate(fid_list)}

    def val(rec, key):
        fid = F.get(key)
        if fid is None or fid not in fmap:
            return None
        return rec[fmap[fid]]

    def dval(rec, key):
        v = val(rec, key)
        if v and isinstance(v, str):
            from datetime import date
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                return None
        return None

    def sval(rec, key):
        v = val(rec, key)
        if v and isinstance(v, list):
            return v[0] if v else ""
        return str(v or "")

    tasks = []
    for i, rec in enumerate(data):
        name = sval(rec, "name")
        if not name:
            continue

        t = {
            "name": name,
            "record_id": rids[i] if i < len(rids) else "",
            "module": sval(rec, "module"),
            "tech_review": dval(rec, "tech_review"),
            "dev_start": dval(rec, "old_dev_start"),
            "dev_end": dval(rec, "old_dev_end"),
            "test_start": dval(rec, "old_test_start"),
            "test_end": dval(rec, "old_test_end"),
            "accept_start": dval(rec, "old_accept_start"),
            "accept_end": dval(rec, "old_accept_end"),
            "dev_slots": sval(rec, "dev_slots"),
            "test_slots": sval(rec, "test_slots"),
            "pm_name": sval(rec, "product_owner"),
            "design_start": dval(rec, "design_start"),
            "design_end": dval(rec, "design_end"),
        }
        tasks.append(t)

    def fmt_date(d):
        return d.isoformat() if d else "None"

    def is_real_date(d):
        if d is None:
            return False
        return d.isoformat() != "1900-01-01"

    # Check 1: Phase order
    for t in tasks:
        ds, de = t["dev_start"], t["dev_end"]
        ts, te = t["test_start"], t["test_end"]
        as_, ae = t["accept_start"], t["accept_end"]

        if is_real_date(ds) and is_real_date(de) and ds > de:
            issues.append({"severity": "error", "task": t["name"],
                           "desc": f"研发开始({fmt_date(ds)}) 晚于 研发结束({fmt_date(de)})"})
        if is_real_date(ts) and is_real_date(te) and ts > te:
            issues.append({"severity": "error", "task": t["name"],
                           "desc": f"测试开始({fmt_date(ts)}) 晚于 测试结束({fmt_date(te)})"})
        if is_real_date(as_) and is_real_date(ae) and as_ > ae:
            issues.append({"severity": "error", "task": t["name"],
                           "desc": f"验收开始({fmt_date(as_)}) 晚于 验收结束({fmt_date(ae)})"})

        # Phase sequence: dev < test < accept
        if is_real_date(de) and is_real_date(ts) and de >= ts:
            issues.append({"severity": "warning", "task": t["name"],
                           "desc": f"研发结束({fmt_date(de)}) 不早于 测试开始({fmt_date(ts)})"})
        if is_real_date(te) and is_real_date(as_) and te >= as_:
            issues.append({"severity": "warning", "task": t["name"],
                           "desc": f"测试结束({fmt_date(te)}) 不早于 验收开始({fmt_date(as_)})"})

        # Review anchor
        rd = t["tech_review"]
        if rd and is_real_date(ds):
            from datetime import timedelta
            expected = cal.next_working_day(rd)
            if ds < expected:
                issues.append({"severity": "warning", "task": t["name"],
                               "desc": f"研发开始({fmt_date(ds)}) 早于 技术评审+1wd({fmt_date(expected)})"})

    # Check 2: Slot exclusivity (per-slot date overlap)
    dev_slot_map = {}
    test_slot_map = {}
    for t in tasks:
        ds, de = t["dev_start"], t["dev_end"]
        slots_str = t["dev_slots"]
        if slots_str and slots_str not in ("未分配", "独立团队", ""):
            for sid in slots_str.split(","):
                sid = sid.strip()
                if not sid:
                    continue
                try:
                    sid_int = int(sid)
                except ValueError:
                    continue
                if sid_int not in dev_slot_map:
                    dev_slot_map[sid_int] = []
                dev_slot_map[sid_int].append((t["name"], ds, de))

    # Check dev slot overlaps
    from datetime import timedelta
    for sid, items in dev_slot_map.items():
        real_items = [(n, s, e) for n, s, e in items if is_real_date(s) and is_real_date(e)]
        for i in range(len(real_items)):
            for j in range(i + 1, len(real_items)):
                n1, s1, e1 = real_items[i]
                n2, s2, e2 = real_items[j]
                # Check overlap: not (e1 < s2 or e2 < s1)
                if not (e1 < s2 or e2 < s1):
                    issues.append({"severity": "error", "task": f"{n1} / {n2}",
                                   "desc": f"研发槽位 {sid} 时间重叠: {n1}({fmt_date(s1)}~{fmt_date(e1)}) "
                                           f"vs {n2}({fmt_date(s2)}~{fmt_date(e2)})"})

    # Test slot overlaps
    for t in tasks:
        ts_d, te_d = t["test_start"], t["test_end"]
        slots_str = t["test_slots"]
        if slots_str and slots_str not in ("未分配", ""):
            for sid in slots_str.split(","):
                sid = sid.strip()
                if not sid:
                    continue
                try:
                    sid_int = int(sid)
                except ValueError:
                    continue
                if sid_int not in test_slot_map:
                    test_slot_map[sid_int] = []
                test_slot_map[sid_int].append((t["name"], ts_d, te_d))

    for sid, items in test_slot_map.items():
        real_items = [(n, s, e) for n, s, e in items if is_real_date(s) and is_real_date(e)]
        for i in range(len(real_items)):
            for j in range(i + 1, len(real_items)):
                n1, s1, e1 = real_items[i]
                n2, s2, e2 = real_items[j]
                if not (e1 < s2 or e2 < s1):
                    issues.append({"severity": "error", "task": f"{n1} / {n2}",
                                   "desc": f"测试槽位 {sid} 时间重叠: {n1}({fmt_date(s1)}~{fmt_date(e1)}) "
                                           f"vs {n2}({fmt_date(s2)}~{fmt_date(e2)})"})

    # Check 3: PM acceptance conflict
    pm_accept_map = {}
    for t in tasks:
        pm = t["pm_name"]
        as_, ae = t["accept_start"], t["accept_end"]
        if pm and is_real_date(as_) and is_real_date(ae):
            if pm not in pm_accept_map:
                pm_accept_map[pm] = []
            pm_accept_map[pm].append((t["name"], as_, ae))

    for pm, items in pm_accept_map.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                n1, s1, e1 = items[i]
                n2, s2, e2 = items[j]
                if not (e1 < s2 or e2 < s1):
                    issues.append({"severity": "error", "task": f"{n1} / {n2}",
                                   "desc": f"PM({pm}) 验收时间重叠: {n1}({fmt_date(s1)}~{fmt_date(e1)}) "
                                           f"vs {n2}({fmt_date(s2)}~{fmt_date(e2)})"})

        # Check PM acceptance vs design periods
        dsgn_items = [(t["name"], t["design_start"], t["design_end"])
                      for t in tasks if t["pm_name"] == pm
                      and is_real_date(t["design_start"]) and is_real_date(t["design_end"])]
        for n_acc, s_acc, e_acc in items:
            for n_dsg, s_dsg, e_dsg in dsgn_items:
                if not (e_acc < s_dsg or e_dsg < s_acc):
                    issues.append({"severity": "warning", "task": n_acc,
                                   "desc": f"验收({fmt_date(s_acc)}~{fmt_date(e_acc)}) "
                                           f"与设计期({n_dsg}:{fmt_date(s_dsg)}~{fmt_date(e_dsg)}) 时间重叠"})

    return issues


if __name__ == "__main__":
    main()
