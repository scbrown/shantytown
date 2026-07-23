"""forgejo — a Tracker over a self-hosted Forgejo/Gitea forge's issues.

The proof-of-generality adapter (internal-ref): the harness must not know what a
work item IS, only create/get/update — so a fleet whose tracker is its forge
plugs in here with zero changes anywhere else. Deliberately stdlib urllib over
the REST API: no `tea` CLI dependency, no SDK, and `gh` does not speak
Forgejo — the API is the stable surface.

Config, same source order as everything else here:
    SHANTY_FORGEJO_URL    the forge base (already in the README table; the
                          doctor uses it too). e.g. http://forge.example
    SHANTY_FORGEJO_TOKEN  an API token with issue read/write on the repo.
    repo                  "owner/name" — constructor arg; there is no sane
                          default for someone else's forge.

STATUS MAPPING, stated because it is lossy and must not be silently so:
Forgejo issues have exactly open/closed. WorkItem's richer states map:
    create            -> an open issue
    update(closed/done)   -> state=closed
    update(open)          -> state=open
    update(in_progress)   -> stays OPEN; the assignee (set in the same update)
                             is the in-progress signal. Forgejo cannot say
                             "in progress" and this adapter does not invent a
                             label scheme to fake it — the dispatcher's
                             send->verify->update flow only needs the
                             assignment recorded, and it is.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request

from .protocols import WorkItem


class ForgejoTracker:
    def __init__(self, repo: str, base_url: str | None = None,
                 token: str | None = None, timeout: int = 30):
        if not repo or "/" not in repo:
            raise ValueError("ForgejoTracker needs repo='owner/name'")
        self.repo = repo
        self.base = (base_url or os.environ.get("SHANTY_FORGEJO_URL")
                     or "http://localhost:3000").rstrip("/")
        self._token = token or os.environ.get("SHANTY_FORGEJO_TOKEN") or ""
        self.timeout = timeout

    # One tiny transport seam so tests inject a fake without patching urllib.
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.base}/api/v1/repos/{self.repo}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"token {self._token}"}
                        if self._token else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode() or "{}")

    @staticmethod
    def _to_item(d: dict) -> WorkItem:
        assignee = (d.get("assignee") or {}).get("login") if d.get("assignee") else None
        return WorkItem(id=str(d.get("number")), title=d.get("title", ""),
                        status=d.get("state", "open"), assignee=assignee)

    def get(self, item_id: str) -> WorkItem:
        try:
            return self._to_item(self._request("GET", f"/issues/{item_id}"))
        except urllib.error.HTTPError as e:
            # 404 = does not exist; anything else = could not tell. Say which.
            if e.code == 404:
                raise LookupError(f"no issue #{item_id} in {self.repo}") from e
            raise LookupError(
                f"forge answered {e.code} for issue #{item_id} — could not tell") from e

    def create(self, title: str, **fields) -> WorkItem:
        body = {"title": title,
                "body": fields.get("body") or fields.get("description") or ""}
        if fields.get("assignee"):
            body["assignees"] = [_bare(fields["assignee"])]
        return self._to_item(self._request("POST", "/issues", body))

    def update(self, item_id: str, **fields) -> None:
        body: dict = {}
        status = fields.get("status")
        if status in ("closed", "done"):
            body["state"] = "closed"
        elif status == "open":
            body["state"] = "open"
        # in_progress: no state change — the assignment IS the signal (docstring).
        if fields.get("assignee"):
            body["assignees"] = [_bare(fields["assignee"])]
        if fields.get("title"):
            body["title"] = fields["title"]
        if not body:
            return
        self._request("PATCH", f"/issues/{item_id}", body)


def _bare(assignee: str) -> str:
    """Crew-path assignees ('rig/crew/name') become the bare forge login."""
    return assignee.split("/")[-1]
