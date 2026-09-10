"""Structured AI insight/visual pipeline (specs/06).

Compatibility shim: implementation lives in app/services/llm/pipeline/ (one focused module per concern). Import from either path.
"""
from app.services.llm.pipeline import *  # noqa: F401,F403
from app.config import get_settings  # noqa: F401
from app.services.llm.groq_service import generate_response  # noqa: F401
import difflib  # noqa: F401 (kept: part of the pre-split module surface)
import json  # noqa: F401 (kept: part of the pre-split module surface)
import logging
import re  # noqa: F401 (kept: part of the pre-split module surface)
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Sequence, Tuple  # noqa: F401
from pydantic import BaseModel, Field, ValidationError, field_validator  # noqa: F401
logger = logging.getLogger(__name__)
