from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    STYLE = "style"
    PERFORMANCE = "performance"
    LOGIC = "logic"


class ReviewComment(BaseModel):
    severity: Severity
    category: Category
    file: str
    function: Optional[str] = None
    line_number: Optional[int] = None      # best-effort, LLM may not always know
    issue: str
    suggestion: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    existing_pattern_file: Optional[str] = None
    existing_pattern_function: Optional[str] = None
    evidence: Optional[str] = None


class PRReview(BaseModel):
    overall_summary: str
    comments: list[ReviewComment]
    verdict: str                            # derived deterministically
    verdict_reason: str
    risk_score: int = Field(ge=1, le=10)   # 1 = low risk, 10 = critical
    confidence_score: float = Field(ge=0.0, le=1.0)
    tool_calls_used: int = 0
    context_files_used: list[str] = Field(default_factory=list)