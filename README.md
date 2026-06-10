# GP_AI — Academic Advisor Service

Python FastAPI microservice that produces a personalized term plan for Helwan FCI students. Called by `GP_BackEnd` over HTTP.

## Stack

- FastAPI + Uvicorn
- Neo4j async driver (course graph, prereqs, dept membership)
- Motor (Mongo, read-only student snapshot)
- httpx → Ollama `phi` on `localhost:11434`, `num_predict: 200`, `format: json`

## Run

```bash
cp .env.example .env   # fill MONGO_URI / NEO4J_* / INTERNAL_KEY
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull phi
ollama serve &           # if not already running
uvicorn app.main:app --reload --port 9100
```

### Day-to-day (after the venv is set up)

```bash
./start.sh   # opens the Ollama desktop app + starts uvicorn on :9100 (background, survives shell exit)
./stop.sh    # stops the uvicorn process (Ollama keeps running)
```

`start.sh` is idempotent — re-running it skips anything already up and just prints the health check.

### Windows PowerShell

Use the Windows scripts instead of `start.sh` / `stop.sh`:

```powershell
.\start-windows.ps1
.\stop-windows.ps1
```

`start-windows.ps1` checks `.env`, Python, the virtual environment, Python dependencies, Ollama, the configured Ollama model, and then starts FastAPI on `PORT` from `.env` (default `9100`). If something fails, it exits with an error that points to the missing or broken part. It leaves logs in:

```text
gp_ai_windows.log
gp_ai_windows.err.log
```

If PowerShell blocks scripts, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Endpoints

| Method | Path                  | Auth                     |
|--------|-----------------------|--------------------------|
| GET    | `/healthz`            | none                     |
| POST   | `/v1/advise/student`  | `X-Internal-Key: <env>`  |
| POST   | `/v1/advise/guest`    | `X-Internal-Key: <env>`  |

Both `/advise` endpoints return:

```jsonc
{
  "plan": [{ "courseCode": "CS112", "courseName": "...", "creditHours": 3 }],
  "notes": "Focus on prereqs that unlock most CS electives.",
  "totalSuggestedCredits": 15,
  "remainingHoursToGraduate": 84,
  "currentGPA": 3.10,
  "candidatesConsidered": 12,
  "aiUsed": true
}
```

## How the plan is built

1. **Cypher** computes the candidate set (active courses, prereqs satisfied, not already passed, within `Required_level`).
2. **Ranker** sorts by preferred-department `Mandatory` → `Contains_Elective`/`Includes` → other.
3. **Phi** picks at most N codes that fit the credit cap (loaded from `data/policy.yaml`, GPA-adjusted). Output is forced to JSON.
4. **Validator** drops codes phi didn't see in the candidate set; deterministic top-up fills any shortfall. If phi fails, the rule-based picker still returns a plan with `(rule-based fallback)` appended to `notes`.

## Tests

```bash
pytest
```

Tests stub the LLM and assert: hallucinated codes are dropped, credit cap is respected, fallback fires when phi returns nothing or errors.

## Policy

`data/policy.yaml` carries credit caps, GPA load rules, and per-department total required hours. Numbers are placeholders — fill in from `دليل الطالب في اللائحة الجديدة.pdf`.
