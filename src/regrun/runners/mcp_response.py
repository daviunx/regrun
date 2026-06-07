"""MCP response normalization for fastmcp CLI output.

Handles three response patterns returned by MCP tools:
1. Flat dict: response body used as-is for JSONPath assertions.
2. Envelope: ``{"success": true, "data": {...}}`` -- unwrap ``.data``.
3. String-wrapped: ``{"result": "<json string>"}`` -- parse ``.result`` as JSON.
"""

import json
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


class McpResponse(BaseModel):
    """Normalized MCP response after pattern detection and unwrapping."""

    is_error: bool
    body: dict | str | None = None
    raw: dict


def normalize_mcp_response(raw_output: str) -> McpResponse:
    """Parse fastmcp CLI JSON output and normalize the tool response body.

    The fastmcp CLI (with ``--json``) returns a JSON object like::

        {
            "content": [{"type": "text", "text": "{\"task\": ...}"}],
            "is_error": false,
            "structured_content": ...
        }

    This function:
    1. Parses the raw stdout as JSON.
    2. Extracts ``is_error`` from the top-level response.
    3. Extracts ``content[0].text`` and parses it as JSON (the tool's return value).
    4. Detects and normalizes one of three response patterns.

    Args:
        raw_output: Raw JSON string from fastmcp CLI stdout.

    Returns:
        McpResponse with normalized body suitable for JSONPath assertions.

    Raises:
        json.JSONDecodeError: If raw_output is not valid JSON.
    """
    raw = json.loads(raw_output)
    is_error = bool(raw.get("is_error", False))

    # Extract tool output from content[0].text
    tool_body = _extract_content_text(raw)

    # Normalize based on detected pattern
    if tool_body is None:
        logger.debug("mcp_pattern", pattern="empty", is_error=is_error)
        return McpResponse(is_error=is_error, body=None, raw=raw)

    if isinstance(tool_body, str):
        # content[0].text was not valid JSON -- keep as string
        logger.debug("mcp_pattern", pattern="raw_string", is_error=is_error)
        return McpResponse(is_error=is_error, body=tool_body, raw=raw)

    if not isinstance(tool_body, dict):
        # Unexpected type (list, number, etc.) -- wrap or keep as-is
        logger.debug("mcp_pattern", pattern="non_dict", is_error=is_error)
        return McpResponse(is_error=is_error, body=tool_body, raw=raw)

    normalized = _detect_and_normalize(tool_body)

    logger.debug(
        "mcp_response_normalized",
        is_error=is_error,
        body_type=type(normalized).__name__,
    )

    return McpResponse(is_error=is_error, body=normalized, raw=raw)


def _extract_content_text(raw: dict) -> dict | str | Any | None:
    """Extract and parse ``content[0].text`` from the fastmcp CLI response.

    Prefers ``content[0].text`` over ``structured_content`` because it is
    always present and always a JSON string.

    Returns:
        Parsed JSON (usually dict) if text is valid JSON, raw text string
        otherwise, or None if content is missing/empty.
    """
    content = raw.get("content")
    if not content or not isinstance(content, list) or len(content) == 0:
        return None

    first_item = content[0]
    if not isinstance(first_item, dict):
        return None

    text = first_item.get("text")
    if text is None:
        return None

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.debug("content_text_not_json", text_preview=str(text)[:200])
        return text


def _detect_and_normalize(body: dict) -> dict:
    """Detect the response pattern and normalize to a flat dict.

    Pattern detection order:
    1. **Envelope**: has both ``success`` and ``data`` keys.
       Unwrap ``data`` as the body. If ``data`` is a dict, return it directly.
    2. **String-wrapped**: has ``result`` key whose value is a JSON string.
       Parse the inner JSON and return the parsed value.
    3. **Flat dict**: none of the above -- return as-is.

    Args:
        body: Parsed dict from ``content[0].text``.

    Returns:
        Normalized dict for JSONPath assertions.
    """
    # Pattern 2: Envelope -- {"success": true/false, "data": {...}}
    if "success" in body and "data" in body:
        data = body["data"]
        if isinstance(data, dict):
            logger.debug("mcp_pattern", pattern="envelope")
            return data
        # data is not a dict -- fall through to flat
        logger.debug(
            "mcp_pattern",
            pattern="envelope_non_dict_data",
            data_type=type(data).__name__,
        )
        return body

    # Pattern 3: String-wrapped -- {"result": "<json string>"}
    if "result" in body and isinstance(body["result"], str):
        try:
            inner = json.loads(body["result"])
            if isinstance(inner, dict):
                logger.debug("mcp_pattern", pattern="string_wrapped")
                return inner
            # Parsed but not a dict -- return as flat with parsed result
            logger.debug(
                "mcp_pattern",
                pattern="string_wrapped_non_dict",
                inner_type=type(inner).__name__,
            )
            return {**body, "result": inner}
        except (json.JSONDecodeError, TypeError):
            # result is a string but not JSON -- treat as flat dict
            logger.debug("mcp_pattern", pattern="flat_non_json_result")
            return body

    # Pattern 1: Flat dict -- use as-is
    logger.debug("mcp_pattern", pattern="flat")
    return body
