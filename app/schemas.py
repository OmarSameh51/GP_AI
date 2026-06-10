from typing import Literal, Optional
from pydantic import BaseModel, Field

Department = Literal["AI", "CS", "IT", "IS", "General"]


class PassedCourse(BaseModel):
    courseCode: str
    grade: Optional[float] = None
    isPassed: bool = True


class StudentAdviceRequest(BaseModel):
    studentId: str
    semester: Optional[int] = Field(default=None, ge=1, le=2)


class GuestAdviceRequest(BaseModel):
    department: Department
    academicYear: int = Field(ge=1, le=4)
    preferredDepartment: Optional[Department] = None
    semester: Optional[int] = Field(default=None, ge=1, le=2)
    passedCourses: list[PassedCourse] = []


class PlanCourse(BaseModel):
    courseCode: str
    courseName: str
    creditHours: int


class AdviceResponse(BaseModel):
    plan: list[PlanCourse]
    notes: str
    totalSuggestedCredits: int
    remainingHoursToGraduate: int
    currentGPA: Optional[float] = None
    candidatesConsidered: int
    aiUsed: bool


class StudentRoadmapRequest(BaseModel):
    studentId: str
    semester: Optional[int] = Field(default=None, ge=1, le=2)


class TermPlan(BaseModel):
    academicYear: int
    semester: int
    courses: list[PlanCourse]
    credits: int
    overflow: bool = False  # term falls beyond the standard 4-year program


class RoadmapResponse(BaseModel):
    terms: list[TermPlan]
    totalPlannedCredits: int
    remainingHoursToGraduate: int
    remainingAfterPlan: int
    currentGPA: Optional[float] = None
    notes: str


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1)


class SummarizeResponse(BaseModel):
    summary: str
