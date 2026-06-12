from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from .deps import get_settings

_client: Optional[AsyncIOMotorClient] = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(get_settings().MONGO_URI)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def fetch_all_course_histories() -> list[list[dict]]:
    """Course histories (grade/credits/passed per course) for every student,
    used as training data for the GPA forecaster."""
    db = get_client().get_default_database()
    cursor = db.users.find(
        {"role": "student", "enrolledCourses.0": {"$exists": True}},
        {"enrolledCourses": 1},
    )
    histories: list[list[dict]] = []
    async for doc in cursor:
        courses = [
            {
                "grade": float(c.get("grade") or 0),
                "creditHours": float(c.get("creditHours") or 0),
                "gradePoints": float(c.get("gradePoints") or 0),
                "isPassed": c.get("isPassed") is True,
            }
            for c in (doc.get("enrolledCourses") or [])
            if c.get("creditHours")
        ]
        if courses:
            histories.append(courses)
    return histories


async def fetch_student_courses(student_id: str) -> list[dict] | None:
    """Full graded course list for one student (the forecaster's input)."""
    db = get_client().get_default_database()
    doc = await db.users.find_one(
        {"studentId": student_id, "role": "student"},
        {"enrolledCourses": 1, "department": 1, "preferredDepartment": 1, "gpa": 1},
    )
    if not doc:
        return None
    return [
        {
            "grade": float(c.get("grade") or 0),
            "creditHours": float(c.get("creditHours") or 0),
            "gradePoints": float(c.get("gradePoints") or 0),
            "isPassed": c.get("isPassed") is True,
        }
        for c in (doc.get("enrolledCourses") or [])
        if c.get("creditHours")
    ]


async def fetch_student_snapshot(student_id: str) -> dict | None:
    """Return only the fields the advisor needs from the User document."""
    db = get_client().get_default_database()
    doc = await db.users.find_one(
        {"studentId": student_id, "role": "student"},
        {
            "studentId": 1,
            "academicYear": 1,
            "department": 1,
            "preferredDepartment": 1,
            "gpa": 1,
            "totalCreditHours": 1,
            "enrolledCourses": 1,
        },
    )
    if not doc:
        return None
    return {
        "studentId": doc["studentId"],
        "academicYear": int(doc.get("academicYear") or 1),
        "department": doc.get("department") or "General",
        "preferredDepartment": doc.get("preferredDepartment"),
        "gpa": float(doc.get("gpa") or 0),
        "totalCreditHours": int(doc.get("totalCreditHours") or 0),
        "passedCodes": [
            (c.get("courseCode") or "").upper().strip()
            for c in (doc.get("enrolledCourses") or [])
            if c.get("isPassed") is True
        ],
    }
