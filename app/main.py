from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, status

from . import advisor, llm, mongo_repo, neo4j_repo
from .auth import require_internal_key
from .schemas import (
    AdviceResponse,
    GuestAdviceRequest,
    RoadmapResponse,
    StudentAdviceRequest,
    StudentRoadmapRequest,
    SummarizeRequest,
    SummarizeResponse,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await neo4j_repo.close_driver()
    await mongo_repo.close_client()


app = FastAPI(title="GP_AI Advisor", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post(
    "/v1/advise/student",
    response_model=AdviceResponse,
    dependencies=[Depends(require_internal_key)],
)
async def advise_student(req: StudentAdviceRequest):
    try:
        return await advisor.advise_student(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post(
    "/v1/advise/guest",
    response_model=AdviceResponse,
    dependencies=[Depends(require_internal_key)],
)
async def advise_guest(req: GuestAdviceRequest):
    return await advisor.advise_guest(req)


@app.post(
    "/v1/roadmap/student",
    response_model=RoadmapResponse,
    dependencies=[Depends(require_internal_key)],
)
async def roadmap_student(req: StudentRoadmapRequest):
    try:
        return await advisor.roadmap_student(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post(
    "/v1/roadmap/guest",
    response_model=RoadmapResponse,
    dependencies=[Depends(require_internal_key)],
)
async def roadmap_guest(req: GuestAdviceRequest):
    return await advisor.roadmap_guest(req)


@app.post(
    "/v1/summarize",
    response_model=SummarizeResponse,
    dependencies=[Depends(require_internal_key)],
)
async def summarize(req: SummarizeRequest):
    summary = await llm.summarize_text(req.text)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="AI summarization failed"
        )
    return SummarizeResponse(summary=summary)
