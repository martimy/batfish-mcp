# -*- coding: utf-8 -*-
"""
Copyright 2026 Maen Artimy

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

"""
Turn Batfish failures into concise, structured errors.

pybatfish reports most server-side failures as a ``BatfishException`` whose
message is the whole work-item log, including Java stack traces. Returning that
verbatim is unhelpful to an LLM (and can be thousands of lines), so known
patterns are mapped to a stable ``code`` and an actionable message. The raw
exception text is available to callers that explicitly ask for ``debug``.
"""

from typing import Optional

# (code, needles, message). The first matching pattern wins.
_ERROR_PATTERNS = (
    (
        "SNAPSHOT_INCOMPATIBLE",
        (
            "local class incompatible",
            "InvalidClassException",
            "serialVersionUID",
        ),
        (
            "Snapshot was built with an incompatible Batfish version; "
            "re-upload it with the current Batfish server."
        ),
    ),
    (
        "BIDIRECTIONAL_REQUIRES_DST",
        ("PermittedByAcl is not allowed in a Transformation guard",),
        (
            "bidirectionalReachability requires a destination constraint. "
            "Set options.headers.dstIps to an IP, prefix, or node name "
            "(for example {\"dstIps\": \"server_2\"})."
        ),
    ),
    (
        "INVALID_PORT_SPEC",
        (
            "IntegerSpace",
            "deserialize from Number value",
        ),
        (
            "Port fields (srcPorts/dstPorts) must be string specs such as "
            "\"443\" or \"80,443\"; numeric JSON values are not accepted."
        ),
    ),
    (
        "INVALID_ARGUMENT",
        (
            "Expected type:",
            "Cannot construct instance",
            "no int/Int-argument constructor",
            "Failed to parse",
        ),
        (
            "Batfish rejected one of the question options. Check the input "
            "types and allowed values with batfish_get_question."
        ),
    ),
    (
        "BATFISH_UNAVAILABLE",
        (
            "Connection refused",
            "Failed to connect",
            "Max retries exceeded",
            "ConnectionError",
        ),
        (
            "Could not reach the Batfish server. Check BATFISH_SERVER and "
            "that the coordinator is running."
        ),
    ),
)

_GENERIC_MESSAGE = (
    "Batfish could not complete the question. The snapshot may be invalid or "
    "incompatible; rerun with debug=true for the full Batfish log."
)


def classify_message(text: str) -> tuple[str, str]:
    """Map an exception/work-item message to ``(code, actionable_message)``."""
    haystack = text or ""
    lowered = haystack.lower()
    for code, needles, message in _ERROR_PATTERNS:
        if any(needle.lower() in lowered for needle in needles):
            return code, message

    first_line = next(
        (line.strip() for line in haystack.splitlines() if line.strip()),
        "",
    )
    if first_line:
        return "BATFISH_ERROR", f"{_GENERIC_MESSAGE} ({first_line})"
    return "BATFISH_ERROR", _GENERIC_MESSAGE


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Map an exception to ``(code, actionable_message)``."""
    return classify_message(str(exc))


def error_payload(
    code: str,
    message: str,
    *,
    fun: Optional[str] = None,
    snapshot: Optional[str] = None,
    reference_snapshot: Optional[str] = None,
    debug: Optional[dict] = None,
) -> dict:
    """A ``batfish_run_question``-shaped structured error."""
    payload = {
        "fun": fun,
        "snapshot": snapshot,
        "reference_snapshot": reference_snapshot,
        "status": "error",
        "code": code,
        "message": message,
        "rows": [],
        "removed_columns": [],
    }
    if debug is not None:
        payload["debug"] = debug
    return payload


def exception_payload(
    exc: BaseException,
    *,
    fun: Optional[str] = None,
    snapshot: Optional[str] = None,
    reference_snapshot: Optional[str] = None,
    debug: bool = False,
) -> dict:
    """Build a structured error for ``exc``, keeping the raw log only in debug."""
    code, message = classify_exception(exc)
    details = None
    if debug:
        details = {
            "exception": type(exc).__name__,
            "log": str(exc),
        }
    return error_payload(
        code,
        message,
        fun=fun,
        snapshot=snapshot,
        reference_snapshot=reference_snapshot,
        debug=details,
    )
