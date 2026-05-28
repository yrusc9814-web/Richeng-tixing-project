"""Phase 4.3 — Input validation: forbidden fields, SQL-injection, shell-injection."""

import re
import json
from typing import Optional, Tuple


# ── Forbidden field names (privilege-escalation / dangerous fields) ─────

FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "file_path", "command", "script", "sql", "shell", "exec",
    "cmd", "path", "filename", "directory", "template", "raw_query",
})


# ── SQL-injection patterns (case-insensitive) ───────────────────────────

SQL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bSELECT\b", re.IGNORECASE),
    re.compile(r"\bINSERT\b", re.IGNORECASE),
    re.compile(r"\bUPDATE\b", re.IGNORECASE),
    re.compile(r"\bDELETE\b", re.IGNORECASE),
    re.compile(r"\bDROP\b", re.IGNORECASE),
    re.compile(r"\bALTER\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bEXEC\b", re.IGNORECASE),
    re.compile(r"\bEXECUTE\b", re.IGNORECASE),
    re.compile(r"\bUNION\b", re.IGNORECASE),
    re.compile(r"--"),
    re.compile(r"\bOR\s+1\s*=\s*1\b", re.IGNORECASE),
    re.compile(r"\bAND\s+1\s*=\s*1\b", re.IGNORECASE),
    re.compile(r"';\s*--"),
]


# ── Shell-injection patterns ────────────────────────────────────────────

SHELL_PATTERNS: list[re.Pattern] = [
    re.compile(r";"),
    re.compile(r"&&"),
    re.compile(r"\|\|"),
    re.compile(r"\|"),
    re.compile(r"`"),
    re.compile(r"\$\(\)"),
    re.compile(r"\$\{.*\}", re.IGNORECASE),
    re.compile(r"#!/"),
    re.compile(r"/bin/"),
    re.compile(r"cmd\.exe", re.IGNORECASE),
    re.compile(r"powershell", re.IGNORECASE),
    re.compile(r"subprocess", re.IGNORECASE),
    re.compile(r"os\.system", re.IGNORECASE),
    re.compile(r"\\x[0-9a-fA-F]{2}"),  # hex-encoded shell
    re.compile(r">\s*/dev/"),
    re.compile(r"2>&1"),
]


def check_forbidden_fields(body: dict) -> Optional[str]:
    """Check for any forbidden field names at the top level of the request body.

    Returns the offending field name if present, otherwise None.
    """
    if not isinstance(body, dict):
        return None
    for key in body:
        if key.lower() in FORBIDDEN_FIELDS:
            return key
    return None


def check_sql_injection(value: str) -> Optional[Tuple[str, str]]:
    """Check a string value for SQL-injection patterns.

    Returns (pattern_name, matched_snippet) if found, otherwise None.
    """
    for pattern in SQL_PATTERNS:
        match = pattern.search(value)
        if match:
            return (pattern.pattern, match.group(0))
    return None


def check_shell_injection(value: str) -> Optional[Tuple[str, str]]:
    """Check a string value for shell-injection patterns.

    Returns (pattern_name, matched_snippet) if found, otherwise None.
    """
    for pattern in SHELL_PATTERNS:
        match = pattern.search(value)
        if match:
            return (pattern.pattern, match.group(0))
    return None


# ── Status enumeration ──────────────────────────────────────────────────

VALID_STATUSES: frozenset[str] = frozenset({"pending", "completed", "cancelled"})


def check_status(body: dict) -> Optional[str]:
    """Check that status field (if present) is one of the allowed values.

    Returns an error message if invalid, otherwise None.
    """
    if isinstance(body, dict) and "status" in body:
        status = body["status"]
        status_norm = status.strip().lower() if isinstance(status, str) else status
        if status_norm not in VALID_STATUSES:
            return f"Invalid status: '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
    return None


def deep_check_body(body: dict) -> list[dict]:
    """Recursively check all string values in the request body for SQL/Shell patterns.

    Returns a list of violation dicts: {"field": ..., "type": "sql"|"shell", "pattern": ..., "snippet": ...}
    """
    violations = []

    def _walk(obj, path_prefix="root"):
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path_prefix}.{k}" if path_prefix else k
                _walk(v, new_path)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                new_path = f"{path_prefix}[{idx}]"
                _walk(item, new_path)
        elif isinstance(obj, str):
            sql_hit = check_sql_injection(obj)
            if sql_hit:
                violations.append({
                    "field": path_prefix,
                    "type": "sql",
                    "pattern": sql_hit[0],
                    "snippet": sql_hit[1],
                })
            shell_hit = check_shell_injection(obj)
            if shell_hit:
                violations.append({
                    "field": path_prefix,
                    "type": "shell",
                    "pattern": shell_hit[0],
                    "snippet": shell_hit[1],
                })

    _walk(body)
    return violations
