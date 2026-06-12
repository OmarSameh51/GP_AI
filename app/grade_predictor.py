"""Per-course final-exam prediction with a RandomForestRegressor.

The student enters their coursework and midterm marks together with each
component's maximum (mark distributions differ per course, e.g. midterm /20
and coursework /30). Marks are normalized to a canonical 25/25/50 scale for
the model, and the predicted final is scaled back to whatever marks remain
out of 100. There is no per-course coursework/midterm data in Mongo yet, so
the forest is trained in-process on a synthetic relationship (final tracks
midterm slightly more than coursework, plus noise) — swap `_generate_data`
for real records once they exist.
"""

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from .schemas import GradePredictionResponse

# Canonical training scale; real inputs are normalized onto it.
COURSEWORK_MAX = 25.0
MIDTERM_MAX = 25.0
FINAL_MAX = 50.0

_model: RandomForestRegressor | None = None


def _generate_data(n: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    coursework = rng.uniform(0, COURSEWORK_MAX, n)
    midterm = rng.uniform(0, MIDTERM_MAX, n)
    final = np.clip(1.2 * midterm + 0.8 * coursework + rng.normal(0, 5, n), 0, FINAL_MAX)
    return np.column_stack([coursework, midterm]), final


def _features(coursework: float, midterm: float) -> list[float]:
    return [
        coursework,
        midterm,
        midterm - coursework,  # performance gap
        (midterm + coursework) / 2,  # average internal mark
    ]


def get_model() -> RandomForestRegressor:
    global _model
    if _model is None:
        raw, final = _generate_data()
        x = np.array([_features(c, m) for c, m in raw])
        model = RandomForestRegressor(n_estimators=100, random_state=42)
        model.fit(x, final)
        _model = model
    return _model


def _letter(total: float) -> tuple[str, float]:
    """Mirror of GP_BackEnd/utils/gradeConverter.js, with letter labels."""
    if total >= 90:
        return "A+", 4.0
    if total >= 85:
        return "A", 3.75
    if total >= 80:
        return "B+", 3.4
    if total >= 75:
        return "B", 3.1
    if total >= 70:
        return "C+", 2.8
    if total >= 65:
        return "C", 2.5
    if total >= 60:
        return "D+", 2.25
    if total >= 50:
        return "D", 2.0
    return "F", 1.0


def predict_grade(
    coursework: float,
    midterm: float,
    coursework_max: float = COURSEWORK_MAX,
    midterm_max: float = MIDTERM_MAX,
) -> GradePredictionResponse:
    final_max = 100.0 - coursework_max - midterm_max

    # Normalize onto the canonical 25/25 training scale.
    cw_scaled = coursework / coursework_max * COURSEWORK_MAX
    mt_scaled = midterm / midterm_max * MIDTERM_MAX

    model = get_model()
    predicted = float(model.predict(np.array([_features(cw_scaled, mt_scaled)]))[0])
    predicted = float(np.clip(predicted, 0.0, FINAL_MAX))

    # Scale the canonical /50 prediction to the course's actual final weight.
    predicted_final = predicted / FINAL_MAX * final_max
    total = float(np.clip(coursework + midterm + predicted_final, 0.0, 100.0))
    letter, points = _letter(total)
    return GradePredictionResponse(
        predictedFinal=round(predicted_final, 1),
        finalMax=round(final_max, 1),
        predictedTotal=round(total, 1),
        letter=letter,
        gradePoints=points,
        passLikely=total >= 50,
    )
