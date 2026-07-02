"""HTTP API test runner using async httpx."""

import time

import httpx
import structlog

from regrun.engine.variables import VariableStore
from regrun.models import AuthConfig, Test
from regrun.runners.base import RunnerResponse

logger = structlog.get_logger()


class HttpxRunner:
    """Runner for API surface tests using httpx AsyncClient."""

    def __init__(
        self,
        base_url: str,
        auth_configs: dict[str, AuthConfig],
        timeout: int = 30,
        default_auth: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_configs = auth_configs
        self._timeout = timeout
        self._default_auth = default_auth

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute an HTTP API test.

        Args:
            test: The test definition with method, path, body, auth, etc.
            variables: Current variable store for auth token resolution.

        Returns:
            RunnerResponse with HTTP status code and parsed body.
        """
        headers = self._build_headers(test, variables)
        url = self._build_url(test)

        # Per-test ``timeout`` (seconds) overrides the runner default when set.
        timeout = test.timeout if test.timeout is not None else self._timeout

        logger.debug(
            "http_request",
            method=test.method,
            url=url,
            auth=test.auth,
            has_body=test.body is not None,
        )

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method=test.method or "GET",
                    url=url,
                    headers=headers,
                    json=test.body if test.body else None,
                    params=test.query_params,
                )
            duration_ms = (time.monotonic() - start) * 1000

            body = _parse_response_body(response)

            logger.debug(
                "http_response",
                status=response.status_code,
                duration_ms=round(duration_ms, 1),
                body_type=type(body).__name__,
            )

            return RunnerResponse(
                status_code=response.status_code,
                body=body,
                duration_ms=duration_ms,
            )

        except httpx.TimeoutException as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("http_timeout", url=url, timeout=timeout, error=str(e))
            return RunnerResponse(
                error=f"Timeout after {timeout}s: {e}",
                duration_ms=duration_ms,
            )
        except httpx.HTTPError as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("http_error", url=url, error=str(e))
            return RunnerResponse(
                error=f"HTTP error: {e}",
                duration_ms=duration_ms,
            )

    def _build_headers(self, test: Test, variables: VariableStore) -> dict[str, str]:
        """Build HTTP headers from test auth config and overrides."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }

        auth_name = test.auth or self._default_auth
        if auth_name and auth_name != "none":
            auth_config = self._auth_configs.get(auth_name)
            if auth_config:
                # Resolve the token through the variable store in case it was
                # captured at runtime (e.g., "{{PROD_JWT}}")
                token = variables.render_string(auth_config.token)
                if auth_config.type == "bearer":
                    headers["Authorization"] = f"Bearer {token}"
                elif auth_config.type == "api_key":
                    headers["X-API-Key"] = token

                # Add org header if auth config specifies one and test doesn't
                # override with org_header: false
                if auth_config.org_header and test.org_header is not False:
                    org_value = variables.render_string(auth_config.org_header)
                    headers["X-Org-Slug"] = org_value
            else:
                logger.warning("auth_config_missing", auth_name=auth_name)

        return headers

    def _build_url(self, test: Test) -> str:
        """Build the full URL from base URL and test path."""
        path = test.path or ""
        if path and not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"


def _parse_response_body(response: httpx.Response) -> dict | list | str | None:
    """Parse the response body, attempting JSON first, falling back to text."""
    content_type = response.headers.get("content-type", "")

    if not response.content:
        return None

    if "json" in content_type:
        try:
            return response.json()
        except Exception:
            return response.text

    # Try JSON parsing even without content-type header
    try:
        return response.json()
    except Exception:
        return response.text
