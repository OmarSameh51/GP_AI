import os
import pytest

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "neo4j")

from app import advisor, llm, neo4j_repo


def _entry(code: str, credits_: int, name: str | None = None, level: int = 1, dept: str | None = None, kind: str = "Contains_Mandatory"):
    return {
        "course": {
            "Code": code,
            "name": name or f"Course {code}",
            "Credits": credits_,
            "Required_level": level,
            "isActive": True,
        },
        "deptLinks": [{"dept": dept, "kind": kind}] if dept else [],
    }


@pytest.mark.asyncio
async def test_select_plan_drops_hallucinated_codes(monkeypatch):
    candidates = [
        _entry("CS112", 3, dept="CS"),
        _entry("BA102", 2, dept="CS", kind="Contains_Elective"),
        _entry("MA112", 3),
    ]

    async def fake_chat(_user_prompt: str):
        # phi returns one real code + one hallucinated one
        return {"plan": ["CS112", "ZZ999"], "notes": "tiny"}

    monkeypatch.setattr(llm, "chat_json", fake_chat)

    chosen, notes = await advisor._select_plan(
        candidates=candidates, gpa=3.0, level=2, preferred_dept="CS"
    )

    codes = [e["course"]["Code"] for e in chosen]
    assert "CS112" in codes
    assert "ZZ999" not in codes
    assert notes  # non-empty


@pytest.mark.asyncio
async def test_select_plan_respects_credit_cap(monkeypatch):
    # 8 candidates of 3 credits each — cap should clamp the sum
    candidates = [_entry(f"CS{100 + i}", 3, dept="CS") for i in range(8)]

    async def fake_chat(_user_prompt: str):
        return {
            "plan": [c["course"]["Code"] for c in candidates],
            "notes": "all",
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat)

    chosen, _ = await advisor._select_plan(
        candidates=candidates, gpa=2.5, level=2, preferred_dept="CS"
    )
    total = sum(advisor._credits(e["course"]) for e in chosen)
    assert total <= 18


@pytest.mark.asyncio
async def test_select_plan_falls_back_when_llm_returns_nothing(monkeypatch):
    candidates = [_entry("CS112", 3, dept="CS"), _entry("BA102", 2, dept="CS")]

    async def fake_chat(_user_prompt: str):
        return {"plan": [], "notes": ""}

    monkeypatch.setattr(llm, "chat_json", fake_chat)

    chosen, notes = await advisor._select_plan(
        candidates=candidates, gpa=2.0, level=2, preferred_dept="CS"
    )
    assert chosen, "fallback must pick something when LLM returns []"
    assert "fallback" in notes


@pytest.mark.asyncio
async def test_select_plan_handles_llm_exception(monkeypatch):
    candidates = [_entry("CS112", 3, dept="CS")]

    async def boom(_user_prompt: str):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(llm, "chat_json", boom)

    chosen, notes = await advisor._select_plan(
        candidates=candidates, gpa=None, level=1, preferred_dept=None
    )
    assert chosen
    assert "fallback" in notes


def test_simulate_roadmap_respects_prereqs_and_semesters():
    catalog = [
        {"course": {"Code": "A", "Credits": 3, "Semester": 1}, "prereqs": [], "deptLinks": []},
        {"course": {"Code": "B", "Credits": 3, "Semester": 2}, "prereqs": ["A"], "deptLinks": []},
        {"course": {"Code": "C", "Credits": 3, "Semester": 1}, "prereqs": ["B"], "deptLinks": []},
    ]
    terms, hours = advisor.simulate_roadmap(
        catalog=catalog,
        passed_codes=[],
        start_year=1,
        start_semester=1,
        preferred_dept=None,
        gpa=None,
        required_hours=9,
        taken_hours=0,
    )
    # prereq chain forces one course per term, alternating semesters
    assert [t["entries"][0]["course"]["Code"] for t in terms] == ["A", "B", "C"]
    assert [(t["academicYear"], t["semester"]) for t in terms] == [(1, 1), (1, 2), (2, 1)]
    assert hours == 9


def test_simulate_roadmap_stops_at_required_hours():
    catalog = [
        {"course": {"Code": f"X{i}", "Credits": 3, "Semester": 1}, "prereqs": [], "deptLinks": []}
        for i in range(10)
    ]
    terms, hours = advisor.simulate_roadmap(
        catalog=catalog,
        passed_codes=[],
        start_year=1,
        start_semester=1,
        preferred_dept=None,
        gpa=None,
        required_hours=6,
        taken_hours=0,
    )
    assert len(terms) == 1
    assert hours >= 6


def test_simulate_roadmap_overflows_past_year_4():
    catalog = [
        {"course": {"Code": "A", "Credits": 3, "Semester": 1}, "prereqs": [], "deptLinks": []},
        {"course": {"Code": "B", "Credits": 3, "Semester": 2}, "prereqs": ["A"], "deptLinks": []},
        {"course": {"Code": "C", "Credits": 3, "Semester": 1}, "prereqs": ["B"], "deptLinks": []},
    ]
    terms, _ = advisor.simulate_roadmap(
        catalog=catalog,
        passed_codes=[],
        start_year=4,
        start_semester=1,
        preferred_dept=None,
        gpa=None,
        required_hours=9,
        taken_hours=0,
    )
    assert [(t["academicYear"], t["semester"]) for t in terms] == [(4, 1), (4, 2), (5, 1)]


def test_filter_by_department_keeps_core_and_own_dept_only():
    entries = [
        _entry("MA111", 3),  # common core: no dept links
        _entry("AI330", 3, dept="AI"),
        _entry("CS405", 3, dept="CS"),
        _entry("IT312", 3, dept="AI", kind="Contains_Elective"),
    ]
    kept = [e["course"]["Code"] for e in advisor._filter_by_department(entries, "AI")]
    assert kept == ["MA111", "AI330", "IT312"]
    # no department known -> nothing filtered
    assert len(advisor._filter_by_department(entries, None)) == 4


def test_roadmap_graduation_project_rules():
    filler = [
        {
            "course": {"Code": f"F{i:02d}", "Credits": 3, "Semester": 1 if i % 2 == 0 else 2, "Required_Hours": 0},
            "prereqs": [],
            "deptLinks": [],
        }
        for i in range(40)
    ]
    projects = [
        {"course": {"Code": "AI498", "Credits": 6, "Semester": 1, "Required_Hours": 102}, "prereqs": [], "deptLinks": []},
        {"course": {"Code": "CS498", "Credits": 6, "Semester": 1, "Required_Hours": "x"}, "prereqs": [], "deptLinks": []},
    ]
    terms, _ = advisor.simulate_roadmap(
        catalog=filler + projects,
        passed_codes=[],
        start_year=4,
        start_semester=1,
        preferred_dept="AI",
        gpa=None,
        required_hours=140,
        taken_hours=100,
    )
    codes = [c["course"]["Code"] for t in terms for c in t["entries"]]
    assert "CS498" not in codes  # other department's project never suggested
    assert "AI498" in codes  # own department's project appears...
    early = [c["course"]["Code"] for t in terms[:2] for c in t["entries"]]
    assert "AI498" not in early  # ...but only once 102 earned hours are reached


def test_roadmap_grad_project_lifts_cap_in_year4_sem1():
    # Year 4 sem 1, GPA cap = 18 (gpa=None). Grad project is 6 credits;
    # without the exception, only four 3-credit fillers (18) would fit
    # alongside it would actually exceed (6 + 3*4 = 18 — fits exactly).
    # So instead use 6 + filler*3 to push to 21 and verify it lands.
    filler = [
        {
            "course": {"Code": f"F{i:02d}", "Credits": 3, "Semester": 1, "Required_Hours": 0},
            "prereqs": [],
            "deptLinks": [],
        }
        for i in range(10)
    ]
    project = {
        "course": {"Code": "AI498", "Credits": 6, "Semester": 1, "Required_Hours": 0},
        "prereqs": [],
        "deptLinks": [],
    }
    terms, _ = advisor.simulate_roadmap(
        catalog=[project] + filler,
        passed_codes=[],
        start_year=4,
        start_semester=1,
        preferred_dept="AI",
        gpa=None,
        required_hours=200,  # force the planner to fill as much as the cap allows
        taken_hours=102,
    )
    first = terms[0]
    assert first["academicYear"] == 4 and first["semester"] == 1
    codes = [e["course"]["Code"] for e in first["entries"]]
    assert "AI498" in codes
    # Grad-project bonus: 6 (project) + 5*3 (fillers) = 21
    assert first["credits"] == 21


def test_roadmap_no_cap_lift_without_grad_project():
    # Same setup but no grad project in the catalog — cap stays at 18.
    filler = [
        {
            "course": {"Code": f"F{i:02d}", "Credits": 3, "Semester": 1, "Required_Hours": 0},
            "prereqs": [],
            "deptLinks": [],
        }
        for i in range(10)
    ]
    terms, _ = advisor.simulate_roadmap(
        catalog=filler,
        passed_codes=[],
        start_year=4,
        start_semester=1,
        preferred_dept="AI",
        gpa=None,
        required_hours=200,
        taken_hours=102,
    )
    assert terms[0]["credits"] == 18


def test_roadmap_no_cap_lift_in_year4_sem2():
    # Year 4 sem 2 with a grad project: the bonus must NOT apply
    # (the exception is limited to semester 1).
    filler = [
        {
            "course": {"Code": f"F{i:02d}", "Credits": 3, "Semester": 2, "Required_Hours": 0},
            "prereqs": [],
            "deptLinks": [],
        }
        for i in range(10)
    ]
    project = {
        "course": {"Code": "AI498", "Credits": 6, "Semester": 2, "Required_Hours": 0},
        "prereqs": [],
        "deptLinks": [],
    }
    terms, _ = advisor.simulate_roadmap(
        catalog=[project] + filler,
        passed_codes=[],
        start_year=4,
        start_semester=2,
        preferred_dept="AI",
        gpa=None,
        required_hours=200,
        taken_hours=102,
    )
    assert terms[0]["credits"] == 18
