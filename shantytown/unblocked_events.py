"""Consume chaski's `workitem-unblocked` events (aegis-sfpfwf, epic aegis-c0awwp).

chaski's reaction `aegis:reaction-workitem-unblocked` records a
`aegis:ReactionFiring` in quipu when a WorkItem's LAST blocker resolves. Until
this module existed nothing read those firings: the two tend scanners
(BlockedMisstatusAlerter, DeferralAlerter) re-derived the same condition on
their own and only told the administrator.

This sweep reads each firing ONCE and re-checks the bead against LIVE br before
it acts. The event says "the graph believes the blockers resolved"; br is where
the status lives, so br decides:

  corrected   status is blocked, every `blocks` dependency is closed NOW, and no
              non-bead blocker label (external/human/access/parked) is on it.
              The status is set to open and the firing is cited in a comment,
              so the bead reaches `br ready` and the haul.
  held        a deferral, or a non-bead blocker label, still holds it. Nothing
              is changed (a deferral is lifted by its owner, never by a timer);
              the administrator is told the bead-blockers resolved.
  mismatch    br shows an open `blocks` dependency the event said was resolved
              (a re-block race, or a projection lag). Nothing is changed; it is
              reported, because an emitter that is wrong must be visible.
  noop        already open/in progress/closed with no block signal.

FAIL OPEN, like every tend sweep: an unreadable quipu or br is logged and the
firing is left unprocessed for the next pass. A correction is ledgered only
after the br write returned success.

The scanners stay as the BACKSTOP. A blocked bead they find that has no firing
here is the signature of an emitter that stopped, which is the one failure an
event consumer cannot see by itself (`seen_foci`).
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

REACTION = "http://aegis.gastown.local/ontology/reaction-workitem-unblocked"
NON_BEAD_LABELS = frozenset(
    {"blocked:human", "blocked:access", "blocked:external", "parked:by-design"})
LEDGER_CAP = 2000   # firing IRIs remembered; far more than a week of events


def firings_query(since: str) -> str:
    """Firings of the unblocked reaction at or after `since` (ISO-8601 Z).

    `>=`, not `>`: two firings can share a second. The ledger dedupes, so the
    boundary firing is re-read, never lost."""
    flt = (f'FILTER(STR(?t) >= "{since}")' if since else "")
    return ("PREFIX a: <http://aegis.gastown.local/ontology/> "
            f"SELECT ?f ?focus ?t WHERE {{ ?f a a:ReactionFiring ; "
            f"a:firedBy <{REACTION}> ; a:focus ?focus ; a:startedAt ?t . {flt} }} "
            "ORDER BY ?t ?f LIMIT 500")


def fetch_firings(root, since: str, timeout: float = 10.0) -> list[dict]:
    """[{firing, bead, at}] from quipu. Raises on any failure (fail open upstream)."""
    from .quipu import request_headers, resolve_server
    server = resolve_server(None, root).rstrip("/")
    req = urllib.request.Request(
        server + "/query",
        data=json.dumps({"query": firings_query(since), "verbose": True}).encode(),
        headers={**request_headers(), "X-Quipu-Client": "st-tend"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    if body.get("truncated"):
        raise RuntimeError("firings query truncated; refusing a partial page")
    out = []
    for row in body.get("rows") or []:
        value = lambda k: (row.get(k) or {}).get("value") if isinstance(row.get(k), dict) else row.get(k)
        firing, focus, at = value("f"), value("focus"), value("t")
        if firing and focus and at:
            out.append({"firing": str(firing), "bead": str(focus).rsplit("/", 1)[-1].split(":")[-1],
                        "at": str(at)})
    return out


def _labels(detail: dict) -> set[str]:
    return {str(v.get("name", "") if isinstance(v, dict) else v)
            for v in (detail.get("labels") or [])}


def classify(detail: dict) -> tuple[str, str]:
    """(verdict, reason) for one bead as br shows it NOW."""
    status = (detail.get("status") or "").lower()
    deps = [d for d in (detail.get("dependencies") or [])
            if d.get("dependency_type") == "blocks"]
    still_open = [d.get("id", "?") for d in deps if (d.get("status") or "") != "closed"]
    if still_open:
        return "mismatch", f"br still shows open blocker(s) {', '.join(still_open)}"
    held = sorted(_labels(detail) & NON_BEAD_LABELS)
    if status == "deferred" or detail.get("defer_until") or held:
        why = []
        if status == "deferred" or detail.get("defer_until"):
            why.append(f"deferred until {detail.get('defer_until') or '?'}")
        why.extend(held)
        return "held", "; ".join(why)
    if status == "blocked":
        return "corrected", "every blocks dependency is closed"
    return "noop", status or "unknown"


class UnblockedEventConsumer:
    def __init__(self, root, reg, panes, *, push=None, firings=None, show=None,
                 reopen=None, comment=None, now=None, log=None):
        self._root = root
        self.path = Path(root) / "notify" / "unblocked-events.json"
        self._reg, self._panes = reg, panes
        self._push = push
        self._firings = firings
        self._show = show
        self._reopen = reopen
        self._comment = comment
        self._now = now
        self._log = log or (lambda msg: None)

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        state.setdefault("since", "")
        state.setdefault("done", [])
        state.setdefault("foci", {})
        return state

    def _save(self, state: dict) -> None:
        from .files import write_json_atomic
        state["done"] = state["done"][-LEDGER_CAP:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, state)

    def _tracker(self):
        from .feed_check import _br_tracker
        tracker = _br_tracker(self._root, self._reg)
        if tracker is None:
            raise RuntimeError("no br tracker configured")
        return tracker

    def seen_foci(self) -> dict:
        """bead id -> newest firing time processed. The scanners' backstop key."""
        return dict(self._load()["foci"])

    def sweep(self) -> list[tuple[str, str]]:
        """Process new firings; return [(bead, verdict)] for the ones acted on."""
        from .notify import push_to_admin
        push = self._push or push_to_admin
        state = self._load()
        try:
            rows = (self._firings(state["since"]) if self._firings
                    else fetch_firings(self._root, state["since"]))
        except Exception as e:
            self._log(f"unblocked-events: could not read firings ({e!r})")
            return []
        if self._show is None or self._reopen is None or self._comment is None:
            try:
                tracker = self._tracker()
            except Exception as e:
                self._log(f"unblocked-events: no tracker ({e!r})")
                return []
            from .br import append_comment, show
        show_fn = self._show or (lambda b: show(tracker, b))
        reopen_fn = self._reopen or (lambda b: tracker.update(b, status="open"))
        comment_fn = self._comment or (lambda b, body: append_comment(tracker, b, body))

        done = set(state["done"])
        acted = []
        for row in rows:
            firing, bead, at = row["firing"], row["bead"], row["at"]
            if firing in done:
                continue
            try:
                detail = show_fn(bead)
            except Exception as e:
                self._log(f"unblocked-events: could not show {bead} ({e!r}); retry next pass")
                break          # keep order: do not advance past an unprocessed firing
            verdict, reason = classify(detail)
            title = (detail.get("title") or "")[:80]
            if verdict == "corrected":
                try:
                    reopen_fn(bead)
                except Exception as e:
                    self._log(f"unblocked-events: reopen {bead} failed ({e!r}); retry next pass")
                    break
                try:
                    comment_fn(bead, f"st tend: status blocked -> open from chaski event "
                                     f"{firing} ({at}); br confirms {reason}. (aegis-sfpfwf)")
                except Exception as e:
                    self._log(f"unblocked-events: comment on {bead} failed ({e!r})")
                push(self._reg, self._panes,
                     f"✓ UNBLOCKED {bead}: status blocked -> open (every blocker closed; "
                     f"chaski event {at}). It is in br ready now. {title}")
            elif verdict in ("held", "mismatch"):
                msg = (f"⚠ {bead}: its bead-blockers all resolved (chaski event {at}) but "
                       f"{reason} still holds it. Lift it, or record why it stays. {title}"
                       if verdict == "held" else
                       f"⚠ UNBLOCKED-EVENT MISMATCH {bead}: chaski fired at {at} but "
                       f"{reason}. Nothing changed. A re-block race or a projection lag; "
                       f"check the emitter.")
                if not push(self._reg, self._panes, msg):
                    # Same rule as the scanners: only a delivered report is ledgered.
                    self._log(f"unblocked-events: {bead} push did not land; retry next pass")
                    break
            done.add(firing)
            state["done"].append(firing)
            state["foci"][bead] = max(at, state["foci"].get(bead, ""))
            state["since"] = max(at, state["since"])
            if verdict != "noop":
                acted.append((bead, verdict))
        self._save(state)
        return acted
