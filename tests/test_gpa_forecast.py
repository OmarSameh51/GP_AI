import os

import numpy as np

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "neo4j")

from app import gpa_forecast


def _course(grade: float, credits_: float = 3.0) -> dict:
    return {
        "grade": grade,
        "creditHours": credits_,
        "gradePoints": gpa_forecast._grade_points(grade),
        "isPassed": grade >= 50,
    }


def _history(grades: list[float]) -> list[dict]:
    return [_course(g) for g in grades]


def test_gpa_matches_backend_formula():
    courses = [_course(92), _course(76), _course(45)]
    # (3*4.0 + 3*3.1 + 3*1.0) / 9 = 2.7
    assert gpa_forecast._gpa(courses) == 2.7


def test_training_set_built_and_augmented_to_min_samples():
    histories = [
        _history([90, 85, 80, 75, 70, 88]),
        _history([60, 55, 70, 65, 72]),
    ]
    x, y = gpa_forecast.build_training_set(histories, rng=np.random.default_rng(0))
    assert len(x) >= gpa_forecast.MIN_SAMPLES
    assert len(x) == len(y)
    assert all(0.0 <= t <= 4.0 for t in y)


def test_model_predicts_higher_gpa_for_stronger_history():
    histories = [
        _history([95, 92, 90, 91, 94, 89, 93, 90]),
        _history([88, 85, 90, 82, 87, 91, 84, 86]),
        _history([70, 65, 72, 68, 75, 71, 66, 73]),
        _history([55, 60, 52, 58, 62, 57, 54, 61]),
    ]
    model, samples = gpa_forecast.train_model(histories)
    assert model is not None
    assert samples >= gpa_forecast.MIN_SAMPLES

    strong = gpa_forecast._features(_history([93, 91, 95, 90]), remaining_credits=60)
    weak = gpa_forecast._features(_history([55, 58, 52, 60]), remaining_credits=60)
    strong_pred, weak_pred = model.predict(np.array([strong, weak]))
    assert strong_pred > weak_pred
    assert 0.0 <= weak_pred <= strong_pred <= 4.0


def test_training_skips_short_histories():
    histories = [_history([90, 85])]  # below MIN_COURSES_FOR_TRAINING
    x, _ = gpa_forecast.build_training_set(histories)
    assert len(x) == 0
