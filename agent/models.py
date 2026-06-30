from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class ReviewComment(BaseModel):
    severity: Severity
    file: str
    function: Optional[str] = None
    issue: str
    suggestion: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    # Fix #10 — evidence fields
    existing_pattern_file: Optional[str] = None
    existing_pattern_function: Optional[str] = None
    evidence: Optional[str] = None


class PRReview(BaseModel):
    summary: str
    comments: list[ReviewComment]
    verdict: str
    verdict_reason: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    tool_calls_used: int = 0
    context_files_used: list[str] = Field(default_factory=list)