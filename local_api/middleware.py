"""Phase 4.3 — Middleware: Bearer Token auth + input validation (forbidden fields, SQL/Shell).

Key constraint: the access log MUST NOT include any token fragment — only
auth_result, token_present, request_id, endpoint, status_code.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message

from .config import API_TOKEN
from .validators import check_forbidden_fields, check_status, deep_check_body

# Paths that don't require authentication
PUBLIC_PATHS = frozenset({"/health"})

logger = logging.getLogger("local_api.middleware")

# Known content types we accept
ACCEPTED_CONTENT_TYPES = frozenset({"application/json", "application/json; charset=utf-8", "application/json;charset=utf-8"})


def _normalise_content_type(ct: str) -> str:
    """Strip whitespace and downcase the Content-Type for safe comparison."""
    return ct.lower().replace(" ", "")
class AuthAndValidationMiddleware(BaseHTTPMiddleware):
    """Middleware that performs, in order:

    1. Token validation → 401
    2. Content-Type check on mutating requests → 415
    3. Forbidden-field scan on request body → 422
    4. JSON parse → 400
    5. SQL / Shell pattern scan on request body → 422
    6. Normal flow → downstream handler

    All auth failures are logged WITHOUT any token fragment.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        endpoint = f"{request.method} {request.url.path}"

        # ── 0. Public path bypass ────────────────────────────────────────
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # ── 1. Token validation ─────────────────────────────────────────
        auth_header: Optional[str] = request.headers.get("Authorization", "")
        token_present = bool(auth_header.strip())
        auth_ok = False

        if token_present and auth_header.startswith("Bearer "):
            raw_token = auth_header[7:]
            if raw_token == API_TOKEN:
                auth_ok = True

        # Log the auth outcome — NO token fragment in the log
        logger.info(
            "auth result=%s token_present=%s request_id=%s endpoint=%s",
            "success" if auth_ok else "failed",
            token_present,
            request_id,
            endpoint,
        )

        if not auth_ok:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing authentication token"},
            )

        # ── 2. Content-Type check (mutating methods — only when body present) ──
        if request.method in ("POST", "PATCH", "PUT"):
            content_length = request.headers.get("Content-Length", "")
            if content_length and int(content_length) > 0:
                content_type = request.headers.get("Content-Type", "")
                if _normalise_content_type(content_type) not in ACCEPTED_CONTENT_TYPES:
                    return JSONResponse(
                        status_code=415,
                        content={"detail": "Content-Type must be application/json"},
                    )

        # ── 3. Forbidden-field scan + SQL/Shell scan ────────────────────
        if request.method in ("POST", "PATCH", "PUT"):
            body_bytes = await request.body()
            # Store body so downstream handlers can re-read it
            request._body = body_bytes

            # 3a. JSON parse
            try:
                body_text = body_bytes.decode("utf-8")
                body = json.loads(body_text) if body_text else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Request body is not valid JSON"},
                )

            # 3b. Forbidden fields
            forbidden = check_forbidden_fields(body)
            if forbidden is not None:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"Forbidden field detected: '{forbidden}'"},
                )

            # 3b2. Status validation
            status_err = check_status(body)
            if status_err is not None:
                return JSONResponse(
                    status_code=422,
                    content={"detail": status_err},
                )

            # 3c. SQL / Shell patterns
            violations = deep_check_body(body)
            if violations:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": "SQL or shell injection pattern detected",
                        "violations": [
                            {"field": v["field"], "type": v["type"]}
                            for v in violations
                        ],
                    },
                )

        # ── 4. Proceed ──────────────────────────────────────────────────
        response = await call_next(request)
        return response
