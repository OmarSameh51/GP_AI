import re

from . import llm, neo4j_repo, mongo_repo, policy
from .schemas import (
    AdviceResponse,
    GuestAdviceRequest,
    PlanCourse,
    RoadmapResponse,
    StudentAdviceRequest,
    StudentRoadmapRequest,
    TermPlan,
)


def _credits(course: dict) -> int:
    try:
        return int(course.get("Credits") or 0)
    except Exception:
        return 0


def _belongs_to(dept_links: list[dict], dept: str | None) -> bool:
    if not dept:
        return False
    return any(link.get("dept") == dept for link in dept_links if link)


def _dept_allowed(entry: dict, dept: str | None) -> bool:
    """Common-core courses carry no department links and are open to everyone.
    Department-linked courses are only allowed when the student's own/preferred
    department lists them (Contains_Mandatory or Contains_Elective)."""
    links = [link for link in entry.get("deptLinks", []) if link and link.get("dept")]
    if not links:
        return True
    return _belongs_to(links, dept)


def _filter_by_department(entries: list[dict], dept: str | None) -> list[dict]:
    if not dept:
        return entries
    return [e for e in entries if _dept_allowed(e, dept)]


def _filter_by_level(courses: list[dict], level: int) -> list[dict]:
    out = []
    for entry in courses:
        c = entry["course"]
        required_level = c.get("Required_level")
        try:
            if required_level is None or int(required_level) <= level:
                out.append(entry)
        except Exception:
            out.append(entry)
    return out


def _filter_by_semester(courses: list[dict], semester: int | None) -> list[dict]:
    """Keep courses offered in `semester`. Courses with a non-numeric `Semester`
    (e.g. 'x' = any semester) or missing value are always kept. If `semester`
    is None, no filtering happens."""
    if semester is None:
        return courses
    out = []
    for entry in courses:
        c = entry["course"]
        raw = c.get("Semester")
        if raw is None:
            out.append(entry)
            continue
        try:
            if int(raw) == semester:
                out.append(entry)
        except (TypeError, ValueError):
            # "x" or any non-integer means the course is offered any semester
            out.append(entry)
    return out


def _order_candidates(
    candidates: list[dict], preferred_dept: str | None
) -> list[dict]:
    """Own-dept graduation project first (it's mandatory and already gated on
    eligibility), then preferred-dept mandatory, elective, everything else, by Code."""

    def key(entry: dict):
        code = entry["course"].get("Code") or ""
        if preferred_dept and _project_dept(code) == preferred_dept:
            return (-1, code)
        links = entry["deptLinks"]
        is_mandatory = any(
            link.get("dept") == preferred_dept
            and link.get("kind") == "Contains_Mandatory"
            for link in links
            if link
        )
        is_elective = any(
            link.get("dept") == preferred_dept
            and link.get("kind") == "Contains_Elective"
            for link in links
            if link
        )
        bucket = 0 if is_mandatory else (1 if is_elective else 2)
        return (bucket, code)

    return sorted(candidates, key=key)


def _term_cap(
    base_cap: int,
    year: int | None,
    semester: int | None,
    entries: list[dict],
) -> int:
    """Year 4 semester 1 with a graduation project in the term raises the
    cap to ``policy.grad_project_term_max``. Otherwise returns ``base_cap``."""
    if year != 4 or semester != 1:
        return base_cap
    has_project = any(
        _PROJECT_RE.match(e["course"].get("Code") or "") for e in entries
    )
    if not has_project:
        return base_cap
    return max(base_cap, policy.grad_project_term_max())


async def _select_plan(
    *,
    candidates: list[dict],
    gpa: float | None,
    level: int,
    preferred_dept: str | None,
    semester: int | None = None,
) -> tuple[list[dict], str]:
    base_cap = policy.max_credits_for(gpa)
    max_courses = policy.max_suggestions()

    ranked = _order_candidates(candidates, preferred_dept)
    # Cap the candidate set we hand to the LLM so the prompt stays small.
    short = ranked[: max(max_courses * 3, 12)]
    code_to_entry = {e["course"]["Code"]: e for e in short}
    cand_tuples = [(e["course"]["Code"], _credits(e["course"])) for e in short]
    completed_hint: list[str] = []  # not needed for guest; advisor.py callers pass [] for guest

    # Tell the LLM the cap it could reach if the grad-project bonus applies.
    prompt_cap = _term_cap(base_cap, level, semester, short)

    user_prompt = llm.build_user_prompt(
        gpa=gpa,
        level=level,
        preferred_dept=preferred_dept,
        max_courses=max_courses,
        max_credits=prompt_cap,
        completed=completed_hint,
        candidates=cand_tuples,
    )

    try:
        out = await llm.chat_json(user_prompt)
        ai_used = True
    except Exception:
        out = {"plan": [], "notes": ""}
        ai_used = False

    raw_codes = [str(c).upper().strip() for c in (out.get("plan") or [])]
    notes = str(out.get("notes") or "").strip()

    # Re-validate: keep only codes that were in the candidate set
    chosen: list[dict] = []
    used = 0
    seen: set[str] = set()
    for code in raw_codes:
        if code in seen or code not in code_to_entry:
            continue
        entry = code_to_entry[code]
        hrs = _credits(entry["course"])
        cap_after = _term_cap(base_cap, level, semester, chosen + [entry])
        if used + hrs > cap_after or len(chosen) >= max_courses:
            continue
        chosen.append(entry)
        used += hrs
        seen.add(code)

    # Deterministic top-up if LLM under-delivered (or failed)
    if not chosen:
        ai_used = False
    if len(chosen) < max_courses:
        for entry in ranked:
            code = entry["course"]["Code"]
            if code in seen:
                continue
            hrs = _credits(entry["course"])
            cap_after = _term_cap(base_cap, level, semester, chosen + [entry])
            if used + hrs > cap_after:
                continue
            chosen.append(entry)
            used += hrs
            seen.add(code)
            if len(chosen) >= max_courses:
                break

    if not notes:
        notes = (
            f"Selected {len(chosen)} course(s) totalling {used} credits "
            f"toward {preferred_dept or 'graduation'}."
        )
    return chosen, notes if ai_used else notes + " (rule-based fallback)"


def _to_plan_courses(entries: list[dict]) -> list[PlanCourse]:
    return [
        PlanCourse(
            courseCode=e["course"]["Code"],
            courseName=e["course"].get("name") or e["course"]["Code"],
            creditHours=_credits(e["course"]),
        )
        for e in entries
    ]


async def _remaining_hours(department: str, total_taken: int) -> int:
    hrs = await neo4j_repo.department_required_hours(department)
    if hrs is None:
        hrs = policy.total_required_hours(department)
    return max(hrs - total_taken, 0)


# Graduation project codes: AI497/AI498, CS497/CS498, IS497/IS498, IT497/IT498
_PROJECT_RE = re.compile(r"^(AI|CS|IS|IT)49[78]$")


def _project_dept(code: str | None) -> str | None:
    m = _PROJECT_RE.match(code or "")
    return m.group(1) if m else None


def _hours_gate(course: dict) -> int | None:
    """Earned-hours threshold from the course node (Required_Hours).
    None when missing or non-numeric (e.g. 'x')."""
    raw = course.get("Required_Hours")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _enrollable(course: dict, earned_hours: int, own_dept: str | None) -> bool:
    """Hours-based gating. Graduation projects additionally belong to the
    student's own department and unlock at the policy threshold (covers
    nodes with corrupt Required_Hours)."""
    code = course.get("Code") or ""
    proj_dept = _project_dept(code)
    if proj_dept:
        return proj_dept == own_dept and earned_hours >= policy.graduation_project_min_hours()
    gate = _hours_gate(course)
    return gate is None or earned_hours >= gate


def _filter_enrollable(
    entries: list[dict], earned_hours: int, own_dept: str | None
) -> list[dict]:
    return [
        e for e in entries if _enrollable(e["course"], earned_hours, own_dept)
    ]


def _offered_in(course: dict, semester: int) -> bool:
    raw = course.get("Semester")
    if raw is None:
        return True
    try:
        return int(raw) == semester
    except (TypeError, ValueError):
        # non-numeric (e.g. "x") = offered every semester
        return True


def _level_ok(course: dict, level: int) -> bool:
    required_level = course.get("Required_level")
    try:
        return required_level is None or int(required_level) <= level
    except Exception:
        return True


def _project_prereq_closure(catalog: list[dict], own_dept: str | None) -> set[str]:
    """Transitive prerequisites of the student's own graduation project.
    These are boosted in the roadmap so the path to the project always
    completes before the 102-hour gate opens."""
    if not own_dept:
        return set()
    by_code = {e["course"].get("Code"): e for e in catalog}
    closure: set[str] = set()
    stack = [
        e["course"]["Code"]
        for e in catalog
        if _project_dept(e["course"].get("Code")) == own_dept
    ]
    while stack:
        code = stack.pop()
        for p in by_code.get(code, {}).get("prereqs", []):
            if p not in closure:
                closure.add(p)
                stack.append(p)
    return closure


def simulate_roadmap(
    *,
    catalog: list[dict],
    passed_codes: list[str],
    start_year: int,
    start_semester: int,
    preferred_dept: str | None,
    gpa: float | None,
    required_hours: int,
    taken_hours: int,
    max_terms: int = 12,
) -> tuple[list[dict], int]:
    """Greedy term-by-term projection until required_hours is reached.
    Returns (terms, total_hours_after_plan). Each term is
    {academicYear, semester, entries, credits}. Deterministic by design —
    prereqs unlock progressively as earlier terms complete."""
    taken = {c.upper().strip() for c in passed_codes}
    hours = taken_hours
    base_cap = policy.max_credits_for(gpa)
    max_courses = policy.max_suggestions()
    project_chain = _project_prereq_closure(catalog, preferred_dept)

    terms: list[dict] = []
    year, semester = start_year, start_semester

    for _ in range(max_terms):
        if hours >= required_hours:
            break
        eligible = [
            e
            for e in catalog
            if e["course"].get("Code") not in taken
            and all(p in taken for p in e["prereqs"])
            and _offered_in(e["course"], semester)
            and _level_ok(e["course"], year)
            and _dept_allowed(e, preferred_dept)
            and _enrollable(e["course"], hours, preferred_dept)
        ]
        ranked = _order_candidates(eligible, preferred_dept)
        if project_chain:
            # stable: project-chain prereqs float up within their buckets
            ranked = sorted(
                ranked,
                key=lambda e: 0
                if (
                    e["course"].get("Code") in project_chain
                    or _project_dept(e["course"].get("Code")) == preferred_dept
                )
                else 1,
            )

        chosen: list[dict] = []
        used = 0
        for e in ranked:
            if len(chosen) >= max_courses:
                break
            hrs = _credits(e["course"])
            cap_after = _term_cap(base_cap, year, semester, chosen + [e])
            if used + hrs > cap_after:
                continue
            chosen.append(e)
            used += hrs

        if not chosen:
            break

        terms.append(
            {
                "academicYear": year,
                "semester": semester,
                "entries": chosen,
                "credits": used,
            }
        )
        for e in chosen:
            taken.add(e["course"]["Code"])
        hours += used

        if semester == 1:
            semester = 2
        else:
            semester = 1
            year += 1

    return terms, hours


def _terms_to_plans(terms: list[dict]) -> list[TermPlan]:
    return [
        TermPlan(
            academicYear=t["academicYear"],
            semester=t["semester"],
            courses=_to_plan_courses(t["entries"]),
            credits=t["credits"],
            overflow=t["academicYear"] > 4,
        )
        for t in terms
    ]


def _roadmap_notes(terms: list[TermPlan], remaining_after: int) -> str:
    planned = sum(t.credits for t in terms)
    if not terms:
        return "No eligible courses found to build a roadmap."
    msg = f"Projected {len(terms)} semester(s) covering {planned} credit hours."
    overflow = sum(1 for t in terms if t.overflow)
    if overflow:
        msg += (
            f" {overflow} semester(s) extend beyond the standard 4-year program."
        )
    if remaining_after > 0:
        msg += (
            f" {remaining_after} hour(s) remain unscheduled — some courses may "
            "be missing prerequisites or not offered in the catalog."
        )
    return msg


async def roadmap_student(req: StudentRoadmapRequest) -> RoadmapResponse:
    snap = await mongo_repo.fetch_student_snapshot(req.studentId)
    if not snap:
        raise ValueError(f"student not found: {req.studentId}")

    catalog = await neo4j_repo.fetch_catalog()
    department = snap.get("department") or "General"
    preferred = snap.get("preferredDepartment") or department
    taken_hours = snap["totalCreditHours"] or 0

    required = await neo4j_repo.department_required_hours(department)
    if required is None:
        required = policy.total_required_hours(department)

    terms_raw, hours_after = simulate_roadmap(
        catalog=catalog,
        passed_codes=snap["passedCodes"],
        start_year=snap["academicYear"],
        start_semester=req.semester or 1,
        preferred_dept=preferred,
        gpa=snap["gpa"],
        required_hours=required,
        taken_hours=taken_hours,
    )

    terms = _terms_to_plans(terms_raw)
    remaining_before = max(required - taken_hours, 0)
    remaining_after = max(required - hours_after, 0)

    return RoadmapResponse(
        terms=terms,
        totalPlannedCredits=sum(t.credits for t in terms),
        remainingHoursToGraduate=remaining_before,
        remainingAfterPlan=remaining_after,
        currentGPA=snap["gpa"],
        notes=_roadmap_notes(terms, remaining_after),
    )


async def roadmap_guest(req: GuestAdviceRequest) -> RoadmapResponse:
    passed_codes = [pc.courseCode for pc in req.passedCourses if pc.isPassed]
    catalog = await neo4j_repo.fetch_catalog()
    preferred = req.preferredDepartment or req.department
    taken_hours = await neo4j_repo.credits_for_codes(passed_codes)

    required = await neo4j_repo.department_required_hours(req.department)
    if required is None:
        required = policy.total_required_hours(req.department)

    terms_raw, hours_after = simulate_roadmap(
        catalog=catalog,
        passed_codes=passed_codes,
        start_year=req.academicYear,
        start_semester=req.semester or 1,
        preferred_dept=preferred,
        gpa=None,
        required_hours=required,
        taken_hours=taken_hours,
    )

    terms = _terms_to_plans(terms_raw)
    remaining_before = max(required - taken_hours, 0)
    remaining_after = max(required - hours_after, 0)

    return RoadmapResponse(
        terms=terms,
        totalPlannedCredits=sum(t.credits for t in terms),
        remainingHoursToGraduate=remaining_before,
        remainingAfterPlan=remaining_after,
        currentGPA=None,
        notes=_roadmap_notes(terms, remaining_after),
    )


async def advise_student(req: StudentAdviceRequest) -> AdviceResponse:
    snap = await mongo_repo.fetch_student_snapshot(req.studentId)
    if not snap:
        raise ValueError(f"student not found: {req.studentId}")

    preferred = snap.get("preferredDepartment") or snap.get("department")

    candidates = await neo4j_repo.fetch_candidates_for_student(req.studentId)
    candidates = _filter_by_level(candidates, snap["academicYear"])
    candidates = _filter_by_semester(candidates, req.semester)
    candidates = _filter_by_department(candidates, preferred)
    candidates = _filter_enrollable(
        candidates, snap["totalCreditHours"] or 0, preferred
    )
    chosen, notes = await _select_plan(
        candidates=candidates,
        gpa=snap["gpa"],
        level=snap["academicYear"],
        preferred_dept=preferred,
        semester=req.semester,
    )

    plan = _to_plan_courses(chosen)
    total = sum(p.creditHours for p in plan)
    remaining = await _remaining_hours(
        snap.get("department") or "General", snap["totalCreditHours"]
    )

    return AdviceResponse(
        plan=plan,
        notes=notes,
        totalSuggestedCredits=total,
        remainingHoursToGraduate=remaining,
        currentGPA=snap["gpa"],
        candidatesConsidered=len(candidates),
        aiUsed=True if notes and "fallback" not in notes else False,
    )


async def advise_guest(req: GuestAdviceRequest) -> AdviceResponse:
    passed_codes = [pc.courseCode for pc in req.passedCourses if pc.isPassed]
    preferred = req.preferredDepartment or req.department
    total_taken_hrs = await neo4j_repo.credits_for_codes(passed_codes)

    candidates = await neo4j_repo.fetch_candidates_for_guest(
        req.department, req.academicYear, passed_codes
    )
    candidates = _filter_by_level(candidates, req.academicYear)
    candidates = _filter_by_semester(candidates, req.semester)
    candidates = _filter_by_department(candidates, preferred)
    candidates = _filter_enrollable(candidates, total_taken_hrs, preferred)

    chosen, notes = await _select_plan(
        candidates=candidates,
        gpa=None,
        level=req.academicYear,
        preferred_dept=preferred,
        semester=req.semester,
    )

    plan = _to_plan_courses(chosen)
    remaining = await _remaining_hours(req.department, total_taken_hrs)

    return AdviceResponse(
        plan=plan,
        notes=notes,
        totalSuggestedCredits=sum(p.creditHours for p in plan),
        remainingHoursToGraduate=remaining,
        currentGPA=None,
        candidatesConsidered=len(candidates),
        aiUsed="fallback" not in notes,
    )
