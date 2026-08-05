"""Implementation module for bounded nightly organization maintenance."""

from .common import *

class JsonHttpClient:
    def __init__(self, *, token: str, user_agent: str, timeout_seconds: float = 20.0) -> None:
        if not token.strip():
            raise MaintenanceError("required API token environment variable is empty")
        if not 1.0 <= timeout_seconds <= 60.0:
            raise MaintenanceError("HTTP timeout must be between 1 and 60 seconds")
        self.token = token
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: Iterable[int] = (200,),
    ) -> tuple[int, Any, Mapping[str, str]]:
        data = None
        request_headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = Request(url, data=data, method=method, headers=request_headers)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
                status = response.status
                response_headers = dict(response.headers.items())
        except HTTPError as exc:
            raw = exc.read(8192)
            public = _clean_text(raw.decode("utf-8", errors="replace"), limit=500)
            raise MaintenanceError(f"API request returned HTTP {exc.code}: {public}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise MaintenanceError(f"API request failed: {exc}") from exc
        if len(raw) > MAX_HTTP_RESPONSE_BYTES:
            raise MaintenanceError("API response exceeded the size limit")
        if status not in set(expected):
            raise MaintenanceError(f"API request returned unexpected HTTP {status}")
        if not raw:
            return status, None, response_headers
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MaintenanceError("API response was not valid JSON") from exc
        return status, decoded, response_headers


class GitHubClient:
    def __init__(self, token: str, api_url: str = DEFAULT_GITHUB_API) -> None:
        self.base = validate_endpoint(api_url, label="GitHub API URL")
        self.http = JsonHttpClient(
            token=token,
            user_agent="ai-agent-coordinator-nightly-org-maintenance/1",
        )

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.http.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Accept": "application/vnd.github+json",
        }

    def get(self, path: str, *, expected: Iterable[int] = (200,)) -> Any:
        _, decoded, _ = self.http.request(
            "GET", f"{self.base}{path}", headers=self.auth_headers, expected=expected
        )
        return decoded

    def list_paginated(self, path: str, *, max_pages: int = 10) -> list[Any]:
        separator = "&" if "?" in path else "?"
        values: list[Any] = []
        for page in range(1, max_pages + 1):
            decoded = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(decoded, list):
                raise MaintenanceError("GitHub paginated response was not an array")
            values.extend(decoded)
            if len(decoded) < 100:
                break
        return values


class LinearClient:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = DEFAULT_LINEAR_API,
        auth_scheme: str = "api_key",
    ) -> None:
        self.api_url = validate_endpoint(api_url, label="Linear API URL")
        self.auth_scheme = auth_scheme.strip().lower()
        if self.auth_scheme not in {"api_key", "bearer"}:
            raise MaintenanceError("LINEAR_API_AUTH_SCHEME must be api_key or bearer")
        self.http = JsonHttpClient(
            token=token,
            user_agent="ai-agent-coordinator-nightly-org-maintenance/1",
        )

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        authorization = (
            self.http.token
            if self.auth_scheme == "api_key"
            else f"Bearer {self.http.token}"
        )
        _, decoded, _ = self.http.request(
            "POST",
            self.api_url,
            body={"query": query, "variables": variables},
            headers={"Authorization": authorization},
        )
        root = _require_mapping(decoded, "Linear response")
        errors = root.get("errors")
        if errors:
            message = _clean_text(errors, limit=800)
            raise MaintenanceError(f"Linear GraphQL returned errors: {message}")
        return _require_mapping(root.get("data"), "Linear response.data")


LINEAR_PROJECT_QUERY = """
query NightlyOrganizationProject($id: String!, $first: Int!) {
  project(id: $id) {
    id
    name
    issues(first: $first, orderBy: updatedAt) {
      nodes {
        id
        identifier
        title
        description
        priority
        estimate
        url
        updatedAt
        state { name type }
        labels { nodes { name } }
      }
    }
  }
}
"""


__all__ = [name for name in globals() if not name.startswith("__")]
