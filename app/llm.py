import json
import httpx
from .deps import get_settings

SYSTEM_PROMPT = (
    "You are an academic advisor. The user message lists Candidates as "
    "'CODE:credits' pairs (for example CS111:3 means course CS111 worth 3 credit hours). "
    "Choose at most MaxCourses of those codes so the total credit hours stay at or below "
    "MaxCredits. Prefer codes from the student's preferred department. "
    "Output one JSON object with two keys: 'plan' (an array of the chosen course code "
    "strings, copied verbatim from the Candidates list, no extra quotes or numbers) and "
    "'notes' (one short sentence explaining the choice). Output nothing else. "
    'Example output: {"plan":["CS111","MA111"],"notes":"Foundational year-1 picks."}'
)


def build_user_prompt(
    *,
    gpa: float | None,
    level: int,
    preferred_dept: str | None,
    max_courses: int,
    max_credits: int,
    completed: list[str],
    candidates: list[tuple[str, int]],
) -> str:
    cands = ",".join(f"{c}:{h}" for c, h in candidates)
    return (
        f"GPA: {gpa if gpa is not None else 'NA'}\n"
        f"Level: {level}\n"
        f"PreferredDept: {preferred_dept or 'NA'}\n"
        f"MaxCourses: {max_courses}\n"
        f"MaxCredits: {max_credits}\n"
        f"Completed: [{','.join(completed)}]\n"
        f"Candidates: [{cands}]"
    )


async def chat_json(user_prompt: str) -> dict:
    """Call Ollama /api/chat with JSON-mode. Return parsed dict or raise."""
    s = get_settings()
    body = {
        "model": s.OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "options": {"num_predict": s.OLLAMA_NUM_PREDICT, "temperature": 0.2},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=s.OLLAMA_TIMEOUT) as client:
        r = await client.post(f"{s.OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
        payload = r.json()
    content = (payload.get("message") or {}).get("content") or "{}"
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        return {"plan": [], "notes": ""}
    return _normalize_response(raw)


SUMMARY_SYSTEM_PROMPT = (
    "You are a study assistant. Summarize the lecture text the user gives you "
    "into concise study notes, as a short list of bullet points covering only "
    "the key concepts, definitions, and takeaways from THAT TEXT. "
    "Output plain text bullet points only - no markdown headers, no preamble, "
    "no questions, no exercises, and no unrelated scenarios. "
    "Stop writing once the text has been summarized."
)


async def summarize_text(text: str) -> str:
    """Plain-text Ollama call (no JSON mode) that returns lecture study notes."""
    s = get_settings()
    body = {
        "model": s.OLLAMA_MODEL,
        "stream": False,
        "options": {"num_predict": s.OLLAMA_NUM_PREDICT_SUMMARY, "temperature": 0.3},
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    async with httpx.AsyncClient(timeout=s.OLLAMA_TIMEOUT) as client:
        r = await client.post(f"{s.OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
        payload = r.json()
    content = (payload.get("message") or {}).get("content") or ""
    return _trim_summary(content)


_BULLET_PREFIXES = ("-", "*", "•")


def _is_bullet_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(_BULLET_PREFIXES):
        return True
    head = stripped.split(".", 1)[0]
    return head.isdigit()


def _trim_summary(content: str) -> str:
    """Small models tend to keep generating unrelated quizzes/scenarios after the
    summary. Keep only the leading run of bullet-point paragraphs."""
    content = content.strip()
    paragraphs = [p for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return content

    kept: list[str] = []
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        if all(_is_bullet_line(line) for line in lines):
            kept.append(paragraph.strip())
        else:
            break

    return "\n".join(kept) if kept else content


def _normalize_response(raw: object) -> dict:
    """Phi sometimes capitalizes keys, nests the answer, or returns a list directly.
    Pull out a flat {plan: [str], notes: str} regardless."""
    if isinstance(raw, list):
        return {"plan": [str(x) for x in raw], "notes": ""}
    if not isinstance(raw, dict):
        return {"plan": [], "notes": ""}

    ci = {str(k).lower(): v for k, v in raw.items()}

    plan = ci.get("plan")
    if plan is None:
        for v in raw.values():
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                plan = v
                break

    notes = ci.get("notes") or ci.get("note") or ci.get("explanation") or ""

    if not isinstance(plan, list):
        plan = []
    flat: list[str] = []
    for item in plan:
        if isinstance(item, str):
            flat.append(item)
        elif isinstance(item, list) and item and isinstance(item[0], str):
            flat.append(item[0])
        elif isinstance(item, dict):
            code = item.get("code") or item.get("courseCode") or item.get("Code")
            if isinstance(code, str):
                flat.append(code)

    return {"plan": flat, "notes": str(notes)}
