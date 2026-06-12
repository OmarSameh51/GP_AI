import os

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "neo4j")

from app import grade_predictor


def test_prediction_within_bounds():
    res = grade_predictor.predict_grade(coursework=20, midterm=22)
    assert 0 <= res.predictedFinal <= grade_predictor.FINAL_MAX
    assert 0 <= res.predictedTotal <= 100
    assert res.predictedTotal >= res.predictedFinal


def test_stronger_internal_marks_predict_higher_final():
    strong = grade_predictor.predict_grade(coursework=24, midterm=24)
    weak = grade_predictor.predict_grade(coursework=6, midterm=5)
    assert strong.predictedFinal > weak.predictedFinal
    assert strong.predictedTotal > weak.predictedTotal
    assert strong.passLikely
    assert not weak.passLikely


def test_custom_mark_distribution_scales_final():
    # midterm /20 and coursework /30 leave a /50 final
    res = grade_predictor.predict_grade(
        coursework=27, midterm=18, coursework_max=30, midterm_max=20
    )
    assert res.finalMax == 50
    assert 0 <= res.predictedFinal <= 50
    assert res.predictedTotal <= 100

    # heavier internals (40+40) leave only a /20 final
    res_small_final = grade_predictor.predict_grade(
        coursework=36, midterm=36, coursework_max=40, midterm_max=40
    )
    assert res_small_final.finalMax == 20
    assert res_small_final.predictedFinal <= 20


def test_equivalent_percentages_predict_equivalent_outcomes():
    # 80% internals under two different distributions -> same canonical input
    a = grade_predictor.predict_grade(coursework=20, midterm=20, coursework_max=25, midterm_max=25)
    b = grade_predictor.predict_grade(coursework=24, midterm=16, coursework_max=30, midterm_max=20)
    assert abs(a.predictedFinal / 50 - b.predictedFinal / 50) < 0.01
    assert a.letter == b.letter


def test_letter_matches_backend_thresholds():
    assert grade_predictor._letter(92) == ("A+", 4.0)
    assert grade_predictor._letter(77) == ("B", 3.1)
    assert grade_predictor._letter(50) == ("D", 2.0)
    assert grade_predictor._letter(49.9) == ("F", 1.0)
