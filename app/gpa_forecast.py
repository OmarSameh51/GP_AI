"""Graduation-GPA forecasting with a RandomForestRegressor.

Training pairs come from real student histories in Mongo: for each student we
take partial snapshots of their course sequence (features over the first k
courses, plus how much of the program is still ahead at that point) and use
their GPA over the *whole* history as the target. Because no cohort has
graduated yet, the full-history GPA is the best available proxy for the
final GPA. Real samples are augmented with grade-jittered copies so the
forest has enough data even on a small student base.
"""

import math
import time

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from . import mongo_repo, neo4j_repo, policy
from .schemas import GpaForecastResponse

SNAPSHOT_FRACTIONS = (0.4, 0.6, 0.8, 1.0)
MIN_COURSES_FOR_TRAINING = 4
MIN_SAMPLES = 60
AUGMENT_GRADE_JITTER = 4.0  # +/- points of grade noise for synthetic copies
MODEL_TTL_SECONDS = 3600

_model: RandomForestRegressor | None = None
_model_trained_at: float = 0.0
_model_samples: int = 0


def _grade_points(grade: float) -> float:
    """Mirror of GP_BackEnd/utils/gradeConverter.js."""
    if grade >= 90:
        return 4.0
    if grade >= 85:
        return 3.75
    if grade >= 80:
        return 3.4
    if grade >= 75:
        return 3.1
    if grade >= 70:
        return 2.8
    if grade >= 65:
        return 2.5
    if grade >= 60:
        return 2.25
    if grade >= 50:
        return 2.0
    return 1.0


def _gpa(courses: list[dict]) -> float:
    """Mirror of GP_BackEnd/utils/calculateGPA.js (all attempts weighted)."""
    total_points = sum(c["creditHours"] * c["gradePoints"] for c in courses)
    total_hours = sum(c["creditHours"] for c in courses)
    return round(total_points / total_hours, 2) if total_hours else 0.0


def _features(courses: list[dict], remaining_credits: float) -> list[float]:
    grades = [c["grade"] for c in courses]
    taken_credits = sum(c["creditHours"] for c in courses)
    pass_rate = sum(1 for c in courses if c["isPassed"]) / len(courses)
    mean_grade = sum(grades) / len(grades)
    std_grade = math.sqrt(sum((g - mean_grade) ** 2 for g in grades) / len(grades))
    third = max(len(grades) // 3, 1)
    trend = (sum(grades[-third:]) / third) - (sum(grades[:third]) / third)
    progress = taken_credits / (taken_credits + remaining_credits) if (taken_credits + remaining_credits) else 1.0
    return [
        _gpa(courses),
        mean_grade,
        std_grade,
        pass_rate,
        float(len(courses)),
        taken_credits,
        remaining_credits,
        progress,
        trend,
    ]


def build_training_set(
    histories: list[list[dict]], rng: np.random.Generator | None = None
) -> tuple[np.ndarray, np.ndarray]:
    rng = rng or np.random.default_rng(42)
    xs: list[list[float]] = []
    ys: list[float] = []

    for history in histories:
        if len(history) < MIN_COURSES_FOR_TRAINING:
            continue
        target = _gpa(history)
        total_credits = sum(c["creditHours"] for c in history)
        for frac in SNAPSHOT_FRACTIONS:
            k = max(int(len(history) * frac), 2)
            partial = history[:k]
            remaining = total_credits - sum(c["creditHours"] for c in partial)
            xs.append(_features(partial, remaining))
            ys.append(target)

    # Augment with grade-jittered copies until the forest has enough samples.
    base = len(xs)
    if base:
        i = 0
        while len(xs) < MIN_SAMPLES:
            source = histories[i % len(histories)]
            if len(source) >= MIN_COURSES_FOR_TRAINING:
                jittered = []
                for c in source:
                    grade = float(np.clip(c["grade"] + rng.normal(0, AUGMENT_GRADE_JITTER), 0, 100))
                    jittered.append(
                        {
                            "grade": grade,
                            "creditHours": c["creditHours"],
                            "gradePoints": _grade_points(grade),
                            "isPassed": grade >= 50,
                        }
                    )
                k = max(int(len(jittered) * rng.choice(SNAPSHOT_FRACTIONS)), 2)
                total = sum(c["creditHours"] for c in jittered)
                partial = jittered[:k]
                xs.append(_features(partial, total - sum(c["creditHours"] for c in partial)))
                ys.append(_gpa(jittered))
            i += 1
            if i > MIN_SAMPLES * 20:
                break

    return np.array(xs, dtype=float), np.array(ys, dtype=float)


def train_model(histories: list[list[dict]]) -> tuple[RandomForestRegressor | None, int]:
    x, y = build_training_set(histories)
    if len(x) < MIN_COURSES_FOR_TRAINING:
        return None, 0
    model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
    model.fit(x, y)
    return model, len(x)


async def _get_model() -> tuple[RandomForestRegressor | None, int]:
    global _model, _model_trained_at, _model_samples
    if _model is not None and time.time() - _model_trained_at < MODEL_TTL_SECONDS:
        return _model, _model_samples
    histories = await mongo_repo.fetch_all_course_histories()
    model, samples = train_model(histories)
    if model is not None:
        _model, _model_trained_at, _model_samples = model, time.time(), samples
    return model, samples


async def forecast_student_gpa(student_id: str) -> GpaForecastResponse:
    courses = await mongo_repo.fetch_student_courses(student_id)
    if courses is None:
        raise ValueError(f"Student {student_id} not found")
    if len(courses) < 2:
        raise ValueError("Not enough course history to forecast a GPA")

    snapshot = await mongo_repo.fetch_student_snapshot(student_id)
    department = (snapshot or {}).get("preferredDepartment") or (snapshot or {}).get("department") or "General"
    required = await neo4j_repo.department_required_hours(department)
    if not required:
        required = policy.total_required_hours(department)

    taken_credits = sum(c["creditHours"] for c in courses)
    remaining = max(float(required) - taken_credits, 0.0)
    current_gpa = _gpa(courses)

    model, samples = await _get_model()
    if model is None:
        # Too little data anywhere in the system: fall back to current GPA.
        return GpaForecastResponse(
            forecastGPA=current_gpa,
            currentGPA=current_gpa,
            completedCredits=int(taken_credits),
            remainingCredits=int(remaining),
            sampleSize=0,
            aiUsed=False,
        )

    predicted = float(model.predict(np.array([_features(courses, remaining)]))[0])
    predicted = float(np.clip(predicted, 0.0, 4.0))
    return GpaForecastResponse(
        forecastGPA=round(predicted, 2),
        currentGPA=current_gpa,
        completedCredits=int(taken_credits),
        remainingCredits=int(remaining),
        sampleSize=samples,
        aiUsed=True,
    )
