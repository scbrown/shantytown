"""A quipu client pointed at ANOTHER SERVICE must say so — not report an empty graph.

THE MEASURED FAILURE (this host, 2026-07-24). quipu-server's own default bind is
`127.0.0.1:3030`; so is bobbin's `--http --port` default. Two tools, one default
port, and whichever binds first wins. Here bobbin won, so the quipu client's
default landed on bobbin:

    quipu-server --db ... --bind 127.0.0.1:3032     <- the real graph
    bobbin serve --http --port 3030                  <- what :3030 actually is

The port collision is upstream and not shantytown's to fix. What IS shantytown's
to fix is what happened next: `_query` ended with

    return body.get("rows", []) if isinstance(body, dict) else []

so ANY 200 whose body is not a quipu query answer became ZERO ROWS. `all()` then
returned `[]`, `roles.check` built `Report(rows=[])`, and `Report.verdict` is OK
when there are no rows — so `st roles --check --registry quipu` gave a CLEAN
BILL OF HEALTH for a graph it had never spoken to. Reproduced before the fix:

    all() -> []          from a server that answered {"status": "ok"}

That is the precise bug quipu.py's own module docstring swears it prevents
("`all()` **raises** when quipu is unreachable. It never returns `[]` on
failure") and the one the repo keeps re-finding: a reachable wrong answer is more
dangerous than an outage, because an outage announces itself.

THE LINE THESE TESTS DRAW. Three outcomes that used to collapse into two:

    the graph is genuinely empty      -> []  (a REAL answer; must stay [])
    quipu refused this QUERY          -> QuipuQueryRejected (remedy: fix the query)
    this is not quipu at all          -> QuipuNotQuipu      (remedy: fix QUIPU_SERVER)

`QuipuNotQuipu` subclasses `QuipuUnreachable` on purpose: every existing
`except QuipuUnreachable` arm keeps its cannot-tell / exit-2 behaviour, which is
the correct verdict here, while the MESSAGE sends the operator to the config
instead of to the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from shantytown.protocols import EventsUnavailable
from shantytown.quipu import (
    QuipuNotQuipu,
    QuipuQueryRejected,
    QuipuRegistry,
    QuipuUnreachable,
)
from shantytown.quipu_events import QuipuEvents


def _resp(body: bytes):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    return lambda req, timeout=None: _R()


def _http_error(code: int, body: str = "", msg: str = "Not Found"):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(
            url="http://test.invalid/query",
            code=code,
            msg=msg,
            hdrs=None,
            fp=io.BytesIO(body.encode()),
        )

    return _raise


def _reg():
    return QuipuRegistry(server="http://test.invalid")


# --- the hole: a 200 that is not a quipu answer -----------------------------


def test_a_200_that_is_not_a_query_answer_is_NOT_an_empty_graph(monkeypatch):
    """THE REGRESSION TEST. A service that answers 200 with JSON carrying no
    "rows" (and no "error") is not a quipu that found nothing — it is not a
    quipu. Before the fix this returned [] and `all()` reported nobody exists."""
    monkeypatch.setattr("urllib.request.urlopen", _resp(b'{"status": "ok"}'))
    with pytest.raises(QuipuNotQuipu) as ei:
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")
    # The message must point at the CONFIG, since that is the remedy.
    assert "QUIPU_SERVER" in str(ei.value)
    assert "http://test.invalid" in str(ei.value)


def test_all_RAISES_rather_than_reporting_an_empty_crew(monkeypatch):
    """The docstring's load-bearing promise, at the level operators feel it:
    `all()` must not manufacture "nobody exists" out of a wrong service. This is
    the one that made `st roles --check` report OK with zero rows."""
    monkeypatch.setattr("urllib.request.urlopen", _resp(b'{"status": "ok"}'))
    with pytest.raises(QuipuUnreachable):  # QuipuNotQuipu is one of these
        _reg().all()


def test_a_200_that_is_not_even_a_dict_is_not_quipu(monkeypatch):
    """An SPA index.html or a bare JSON list. `isinstance(body, dict)` was false,
    so the old code returned [] — the same silent empty graph by a second route."""
    monkeypatch.setattr(
        "urllib.request.urlopen", _resp(b'["not", "a", "query", "answer"]')
    )
    with pytest.raises(QuipuNotQuipu):
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")


def test_a_200_of_unparseable_html_stays_UNREACHABLE(monkeypatch):
    """A service answering HTML fails at json.loads, inside the arm that already
    means could-not-look. Kept as-is: it was never silent, and an unparseable
    body genuinely tells us nothing."""
    monkeypatch.setattr(
        "urllib.request.urlopen", _resp(b"<!DOCTYPE html><title>Some UI</title>")
    )
    with pytest.raises(QuipuUnreachable):
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")


# --- the other half: a missing /query endpoint ------------------------------


def test_a_404_on_query_is_WRONG_SERVICE_not_a_bad_query(monkeypatch):
    """What the collision actually produced on this host: bobbin has no /query,
    so it answered 404. That surfaced as "quipu rejected the query (HTTP 404)",
    sending the operator to rewrite a query that was never the problem. A server
    with no /query endpoint is not a quipu with an opinion about SPARQL."""
    monkeypatch.setattr("urllib.request.urlopen", _http_error(404))
    with pytest.raises(QuipuNotQuipu) as ei:
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")
    assert "QUIPU_SERVER" in str(ei.value)


def test_a_405_on_query_is_also_WRONG_SERVICE(monkeypatch):
    """Method-not-allowed on /query means something else owns that path."""
    monkeypatch.setattr(
        "urllib.request.urlopen", _http_error(405, msg="Method Not Allowed")
    )
    with pytest.raises(QuipuNotQuipu):
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")


# --- what must NOT change --------------------------------------------------


def test_a_genuinely_empty_graph_still_returns_empty(monkeypatch):
    """The distinction only exists if the real empty answer survives it. quipu
    answers a no-match query 200 with rows: []. That is a REAL finding and must
    stay [] — collapsing it into an error would be the same conflation pointed
    the other way."""
    monkeypatch.setattr(
        "urllib.request.urlopen", _resp(b'{"count": 0, "rows": [], "variables": ["s"]}')
    )
    assert _reg()._query("SELECT ?s WHERE { ?s ?p ?o }") == []
    assert _reg().all() == []


def test_a_real_query_answer_still_works(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _resp(
            json.dumps(
                {
                    "count": 1,
                    "variables": ["s", "rt"],
                    "rows": [{"s": "http://x/ontology/ian"}],
                }
            ).encode()
        ),
    )
    assert _reg()._query("SELECT ?s WHERE { ?s ?p ?o }") == [
        {"s": "http://x/ontology/ian"}
    ]


def test_an_unsupported_FILTER_is_STILL_a_query_rejection(monkeypatch):
    """The 4xx that IS about the query must not be re-labelled wrong-service:
    quipu's engine rejects `FILTER NOT EXISTS` with 400 + its own message, and
    the remedy is to rewrite the query. Only endpoint-shaped codes mean wrong
    service."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _http_error(
            400,
            '{"error": "unsupported FILTER expression: Exists(...)"}',
            "Bad Request",
        ),
    )
    with pytest.raises(QuipuQueryRejected) as ei:
        _reg()._query("SELECT ?s WHERE { FILTER NOT EXISTS { ?s ?p ?o } }")
    assert not isinstance(ei.value, QuipuNotQuipu)
    assert "unsupported FILTER expression" in str(ei.value)


def test_a_401_is_auth_not_wrong_service(monkeypatch):
    """quipu gates writes behind a bearer. A 401/403 proves we reached something
    that CARES about quipu's auth — the remedy is a token, not a new URL."""
    monkeypatch.setattr("urllib.request.urlopen", _http_error(401, msg="Unauthorized"))
    with pytest.raises(QuipuQueryRejected) as ei:
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")
    assert not isinstance(ei.value, QuipuNotQuipu)


def test_a_500_is_a_server_fault_not_wrong_service(monkeypatch):
    """A quipu having a bad day is still a quipu."""
    monkeypatch.setattr("urllib.request.urlopen", _http_error(500, msg="Server Error"))
    with pytest.raises(QuipuQueryRejected) as ei:
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")
    assert not isinstance(ei.value, QuipuNotQuipu)


def test_connection_refused_is_STILL_plain_unreachable(monkeypatch):
    """The original load-bearing arm, byte-identical: nothing listening is
    "could not look", and its remedy (start the service) differs from both of
    the new ones."""

    def _raise(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    with pytest.raises(QuipuUnreachable) as ei:
        _reg()._query("SELECT ?s WHERE { ?s ?p ?o }")
    assert not isinstance(ei.value, QuipuNotQuipu)


def test_QuipuNotQuipu_is_caught_by_existing_unreachable_arms():
    """The compatibility contract. Every caller that already handles a graph it
    could not read (roles --check exit 2, `st project`, `st subscribe`) must keep
    working WITHOUT EDITS — "I reached the wrong service" is a cannot-tell, so
    the verdict was always right; only the message was wrong."""
    assert issubclass(QuipuNotQuipu, QuipuUnreachable)


# --- the events client has the same hole -----------------------------------


def test_events_rejects_a_wrong_service_200(monkeypatch):
    """QuipuEvents read `body.get("transactions", [])` — so a wrong-service 200
    was "no new events", and the watermark logic would report `idle` forever
    against something that has no transaction log at all. `idle` and "I am
    talking to the wrong daemon" must not be the same word."""
    monkeypatch.setattr("urllib.request.urlopen", _resp(b'{"status": "ok"}'))
    with pytest.raises(EventsUnavailable) as ei:
        QuipuEvents(server="http://test.invalid").transactions_since(0)
    assert "QUIPU_SERVER" in str(ei.value)


def test_events_rejects_a_404_transactions_endpoint(monkeypatch):
    """The measured case: bobbin 404s /transactions."""
    monkeypatch.setattr("urllib.request.urlopen", _http_error(404))
    with pytest.raises(EventsUnavailable) as ei:
        QuipuEvents(server="http://test.invalid").transactions_since(0)
    assert "QUIPU_SERVER" in str(ei.value)


def test_events_still_reads_a_real_empty_transaction_log(monkeypatch):
    """An honestly quiet quipu: a transactions body that is present and empty."""
    monkeypatch.setattr(
        "urllib.request.urlopen", _resp(b'{"count": 0, "transactions": []}')
    )
    assert QuipuEvents(server="http://test.invalid").transactions_since(0) == []


def test_events_still_reads_real_transactions(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _resp(
            json.dumps(
                {
                    "count": 1,
                    "transactions": [
                        {
                            "id": 7,
                            "actor": "someone",
                            "source": "episode:x",
                            "timestamp": "1970-01-01T00:00:00Z",
                        }
                    ],
                }
            ).encode()
        ),
    )
    evs = QuipuEvents(server="http://test.invalid").transactions_since(0)
    assert [e.id for e in evs] == [7]


# --- where the address comes from ------------------------------------------
#
# The other half of the fix. Detecting a wrong service is damage control; not
# resolving one in the first place is the cure. The address used to come from the
# ambient environment or nowhere, so the deployed value survived only as long as
# some shell kept exporting it — and the invocations least likely to inherit it
# (cron, a re-exec'd hook, a bare `st`) are exactly the ones nobody watches.

def test_explicit_argument_wins_over_everything(monkeypatch, tmp_path):
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://declared.invalid"}))
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.invalid")
    from shantytown.quipu import resolve_server
    assert resolve_server("http://explicit.invalid", tmp_path) == "http://explicit.invalid"


def test_env_json_outranks_the_ambient_environment(monkeypatch, tmp_path):
    """THE LOAD-BEARING ORDER. The deployment's written-down answer beats whatever
    a particular shell exported, so a process that inherited a STALE value (or a
    value meant for another workspace) still resolves the deployed graph."""
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://declared.invalid"}))
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.invalid")
    from shantytown.quipu import resolve_server
    assert resolve_server(None, tmp_path) == "http://declared.invalid"


def test_ambient_env_is_used_when_the_deployment_declared_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.invalid")
    from shantytown.quipu import resolve_server
    assert resolve_server(None, tmp_path) == "http://ambient.invalid"


def test_a_hook_that_inherited_NOTHING_still_finds_the_declared_address(monkeypatch, tmp_path):
    """The regression that motivated reading env.json at all: no QUIPU_SERVER in
    the environment — a cron entry, or a hook the harness re-exec'd outside the
    settings env — must still reach the deployed graph rather than the stock port
    that another service may own."""
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://declared.invalid"}))
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    from shantytown.quipu import resolve_server
    assert resolve_server(None, tmp_path) == "http://declared.invalid"


def test_root_falls_back_to_SHANTY_ROOT_for_library_callers(monkeypatch, tmp_path):
    """A client constructed with no root at all (`QuipuRegistry()`) still resolves
    the same env.json the CLI does, via $SHANTY_ROOT — otherwise the deployment's
    answer would reach only the callers that happened to thread a root through."""
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://declared.invalid"}))
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    assert QuipuRegistry().server == "http://declared.invalid"


def test_the_stock_default_is_quipus_own(monkeypatch, tmp_path):
    """Unconfigured falls back to quipu-server's OWN default bind, NOT to this
    fleet's port. A client that disagrees with its server's documented default is
    a second lie, and a public repo carries no deployment's address. The default
    is made safe by DETECTION (QuipuNotQuipu), not by guessing a better number."""
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    from shantytown.quipu import DEFAULT_SERVER, resolve_server
    assert resolve_server(None, tmp_path) == DEFAULT_SERVER
    assert DEFAULT_SERVER == "http://localhost:3030"


def test_unparseable_env_json_does_not_break_resolution(monkeypatch, tmp_path):
    """A broken config file must not take the client down — it means "the
    deployment said nothing", and the next source answers."""
    (tmp_path / "env.json").write_text("{ not json")
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.invalid")
    from shantytown.quipu import resolve_server
    assert resolve_server(None, tmp_path) == "http://ambient.invalid"


def test_both_clients_resolve_the_SAME_address(monkeypatch, tmp_path):
    """The registry and the events client must not end up pointed at different
    servers from one deployment — they share the resolver for exactly this."""
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://declared.invalid/"}))
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    assert QuipuRegistry(root=tmp_path).server == QuipuEvents(root=tmp_path).server
    # ...and both strip the trailing slash, so neither builds `//query`.
    assert QuipuRegistry(root=tmp_path).server == "http://declared.invalid"
