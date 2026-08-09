"""Request/response schemas for POST /chat and the answer-flag endpoint (Phase B4).

Mirrors the master plan's Shared Contract (Chat row) and specs/06 §3
PipelineOutput. `source_scope` beyond `own_data` is gated/mocked until B7.
"""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

SourceScope = Literal["own_data", "live_web", "both"]


class ChatRequest(BaseModel):
    query: str
    source_scope: SourceScope = "own_data"
    company_name: Optional[str] = None


class FlagRequest(BaseModel):
    query_log_id: UUID


class FlagResponse(BaseModel):
    query_log_id: UUID
    flagged: bool