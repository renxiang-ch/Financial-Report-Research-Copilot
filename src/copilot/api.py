"""FastAPI application — exposes the agent as an HTTP endpoint."""

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from copilot.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    if not settings.api_key:
        return  # no key configured → open access
    if key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")

app = FastAPI(title="Financial Report Copilot", version="0.1.0")


@app.on_event("startup")
def _preload_embedding_model() -> None:
    import os
    if os.environ.get("SKIP_PRELOAD", "").lower() in ("1", "true", "yes"):
        return
    from copilot.retrieval.hybrid import retrieve_hybrid
    retrieve_hybrid("warmup", k=1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QuestionRequest(BaseModel):
    question: str


class AnswerResponse(BaseModel):
    answer: str
    steps: list[dict]
    citations: list[str]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AnswerResponse, dependencies=[Depends(_require_api_key)])
def ask(request: QuestionRequest):
    if not settings.openai_api_key:
        return AnswerResponse(
            answer=(
                "API key not configured. "
                "Add OPENAI_API_KEY to .env to enable the agent. "
                f"Your question was: '{request.question}'"
            ),
            steps=[{"tool": "mock", "input": {"question": request.question}}],
            citations=["No citations — running in mock mode"],
        )

    from copilot.agent.agent import ask as agent_ask
    result = agent_ask(request.question)
    return AnswerResponse(**result)
