"""Request/response schemas for POST /chat and the answer-flag endpoint (Phase B4).

Mirrors the master plan's Shared Contract (Chat row) and specs/06 §3
PipelineOutput. `live_web`/`both` are implemented (live-web retrieval via `search_web`) and exercised by `test_live_web_scope_uses_retrieved_web_context`.
"""
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel

SourceScope = Literal["own_data", "live_web", "both"]


class ChatRequest(BaseModel):
    query: str
    source_scope: SourceScope = "own_data"
    company_name: Optional[str] = None
    # Minimum scoped identifier for thread isolation (P0#20): turns in the
    # same thread share prior context; different threads never do. Defaults
    # to "default" so existing clients keep working (single-thread).
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    # Document picker: restrict PDF evidence to these uploads. None/empty
    # means all of the user's PDFs (existing clients keep working).
    # Scoping is strict — unselected files never contribute evidence.
    file_ids: Optional[List[UUID]] = None


class FlagRequest(BaseModel):
    query_log_id: UUID


class FlagResponse(BaseModel):
    query_log_id: UUID
    flagged: bool