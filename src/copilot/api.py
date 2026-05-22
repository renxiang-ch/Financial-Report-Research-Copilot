"""FastAPI application — exposes the agent as an HTTP endpoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from copilot.config import settings

app = FastAPI(title="Financial Report Copilot", version="0.1.0")

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


@app.post("/ask", response_model=AnswerResponse)
def ask(request: QuestionRequest):
    if not settings.anthropic_api_key:
        return AnswerResponse(
            answer=(
                "API key not configured. "
                "Add ANTHROPIC_API_KEY to .env to enable the agent. "
                f"Your question was: '{request.question}'"
            ),
            steps=[{"tool": "mock", "input": {"question": request.question}}],
            citations=["No citations — running in mock mode"],
        )

    from copilot.agent.agent import ask as agent_ask
    result = agent_ask(request.question)
    return AnswerResponse(**result)
