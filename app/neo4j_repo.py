import os
from typing import Optional

import certifi
from neo4j import AsyncGraphDatabase, AsyncDriver
from .deps import get_settings

# Aura's TLS certs are issued by SSL.com, whose root CA is often missing from
# the local Windows cert store; verify against the bundled Mozilla CA list.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

_driver: Optional[AsyncDriver] = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = AsyncGraphDatabase.driver(
            s.NEO4J_URI, auth=(s.NEO4J_USERNAME, s.NEO4J_PASSWORD)
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def _clean(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        try:
            out[k] = v.to_native() if hasattr(v, "to_native") else v
        except Exception:
            out[k] = v
    return out


async def fetch_candidates_for_student(student_id: str) -> list[dict]:
    """Active courses the student hasn't taken yet whose prereqs are all in TOOK."""
    cypher = """
    MATCH (s:Student {studentId: $studentId})
    MATCH (c:Course)
    WHERE c.isActive = true
      AND NOT (s)-[:TOOK]->(c)
      AND ALL(pre IN [(c)-[:Requires]->(p:Course) | p.Code]
              WHERE pre IN [(s)-[:TOOK]->(t:Course) | t.Code])
    OPTIONAL MATCH (d:Department)-[r:Contains_Mandatory|Contains_Elective]->(c)
    WITH c, collect(DISTINCT { dept: d.code, kind: type(r) }) AS deptLinks
    RETURN c, deptLinks
    ORDER BY c.Code
    """
    async with get_driver().session(
        database=get_settings().NEO4J_DATABASE
    ) as session:
        result = await session.run(cypher, studentId=student_id)
        records = await result.data()

    return [
        {"course": _clean(r["c"]), "deptLinks": r["deptLinks"]} for r in records
    ]


async def fetch_candidates_for_guest(
    department: str,
    academic_year: int,
    passed_codes: list[str],
) -> list[dict]:
    cypher = """
    MATCH (c:Course)
    WHERE c.isActive = true
      AND NOT c.Code IN $passedCodes
      AND ALL(pre IN [(c)-[:Requires]->(p:Course) | p.Code]
              WHERE pre IN $passedCodes)
    OPTIONAL MATCH (d:Department)-[r:Contains_Mandatory|Contains_Elective]->(c)
    WITH c, collect(DISTINCT { dept: d.code, kind: type(r) }) AS deptLinks
    RETURN c, deptLinks
    ORDER BY c.Code
    """
    async with get_driver().session(
        database=get_settings().NEO4J_DATABASE
    ) as session:
        result = await session.run(
            cypher,
            passedCodes=[c.upper().strip() for c in passed_codes],
        )
        records = await result.data()

    return [
        {"course": _clean(r["c"]), "deptLinks": r["deptLinks"]} for r in records
    ]


async def fetch_catalog() -> list[dict]:
    """Every active course with its prerequisite codes and department links.
    Used by the roadmap simulator so it can run term-by-term in memory."""
    cypher = """
    MATCH (c:Course)
    WHERE c.isActive = true
    OPTIONAL MATCH (c)-[:Requires]->(p:Course)
    WITH c, collect(p.Code) AS prereqs
    OPTIONAL MATCH (d:Department)-[r:Contains_Mandatory|Contains_Elective]->(c)
    WITH c, prereqs, collect(DISTINCT { dept: d.code, kind: type(r) }) AS deptLinks
    RETURN c, prereqs, deptLinks
    ORDER BY c.Code
    """
    async with get_driver().session(
        database=get_settings().NEO4J_DATABASE
    ) as session:
        result = await session.run(cypher)
        records = await result.data()

    return [
        {
            "course": _clean(r["c"]),
            "prereqs": [p for p in r["prereqs"] if p],
            "deptLinks": r["deptLinks"],
        }
        for r in records
    ]


async def credits_for_codes(codes: list[str]) -> int:
    """Total credit hours of the given course codes (unknown codes count 0)."""
    if not codes:
        return 0
    cypher = """
    MATCH (c:Course)
    WHERE c.Code IN $codes
    RETURN sum(c.Credits) AS total
    """
    async with get_driver().session(
        database=get_settings().NEO4J_DATABASE
    ) as session:
        result = await session.run(
            cypher, codes=[c.upper().strip() for c in codes]
        )
        rec = await result.single()
        if not rec or rec["total"] is None:
            return 0
        try:
            return int(rec["total"])
        except Exception:
            return 0


async def department_required_hours(department: str) -> int | None:
    cypher = """
    MATCH (d:Department {code: $code})
    RETURN d.Required_Hours AS hrs
    """
    async with get_driver().session(
        database=get_settings().NEO4J_DATABASE
    ) as session:
        result = await session.run(cypher, code=department)
        rec = await result.single()
        if not rec:
            return None
        val = rec["hrs"]
        if val is None:
            return None
        try:
            return int(val)
        except Exception:
            return None
