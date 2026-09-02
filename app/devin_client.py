import json
from dataclasses import dataclass
from typing import Any

import httpx


class DevinAPIError(Exception):
    """Raised when the Devin API returns a non-success response."""


@dataclass
class CreatedSession:
    session_id: str
    url: str | None


def build_remediation_prompt(
    issue_number: int, title: str, body: str, repo_full_name: str
) -> str:
    return f"""Remediate GitHub issue #{issue_number} in repository {repo_full_name}.

Issue title: {title}
Issue body:
{body}

Clone and inspect the repository, investigate the issue, and implement an appropriate fix
with tests where appropriate. Run the relevant lint checks and tests. Open a pull request
against the repository's default branch referencing "Fixes #{issue_number}", and report
the pull request URL when complete."""


class DevinClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 30, session_tag: str = ""):
        self.session_tag = session_tag
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def create_remediation_session(
        self, issue_number: int, title: str, body: str, repo_full_name: str
    ) -> CreatedSession:
        response = await self._client.post(
            "/sessions",
            json={
                "prompt": build_remediation_prompt(issue_number, title, body, repo_full_name),
                "title": f"Remediate {repo_full_name}#{issue_number}: {title}",
                "tags": [self.session_tag, f"issue-{issue_number}"],
                "idempotent": True,
            },
        )
        data = await self._response_data(response)
        return CreatedSession(session_id=data["session_id"], url=data.get("url"))

    async def list_sessions(self, tag: str) -> list[dict[str, Any]]:
        response = await self._client.get("/sessions", params={"tags": tag, "limit": 100})
        data = await self._response_data(response)
        if isinstance(data, list):
            return data
        return data.get("sessions", [])

    async def _response_data(self, response: httpx.Response) -> Any:
        if response.is_error:
            snippet = response.text[:500]
            raise DevinAPIError(f"Devin API returned {response.status_code}: {snippet}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise DevinAPIError("Devin API returned invalid JSON") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
