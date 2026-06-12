from functools import lru_cache
from pathlib import Path
import yaml
from .deps import get_settings


@lru_cache
def load_policy() -> dict:
    path = Path(get_settings().POLICY_FILE)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def max_credits_for(gpa: float | None) -> int:
    policy = load_policy()
    cap = policy["defaults"]["max_credits_per_term"]
    if gpa is None:
        return cap
    for rule in policy.get("load_rules", []):
        cond = rule.get("if", {})
        then = rule.get("then", {})
        if "gpa_lt" in cond and gpa < cond["gpa_lt"]:
            cap = then.get("max_credits_per_term", cap)
        if "gpa_gte" in cond and gpa >= cond["gpa_gte"]:
            cap = then.get("max_credits_per_term", cap)
    return cap


def max_suggestions() -> int:
    return load_policy()["defaults"]["max_suggestions_per_plan"]


def graduation_project_min_hours() -> int:
    return load_policy().get("graduation_project", {}).get("min_credit_hours", 102)


def grad_project_term_max() -> int:
    return load_policy()["defaults"].get("grad_project_term_max", 21)


def total_required_hours(department: str) -> int:
    policy = load_policy()
    return policy["departments"].get(department, {}).get(
        "total_required_hours",
        policy["departments"]["General"]["total_required_hours"],
    )
