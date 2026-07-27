"""The ontology namespace must come from the DEPLOYMENT, not from import time.

THE SIBLING BUG. `resolve_server` taught the quipu clients to read the address out
of `<root>/env.json` (test_quipu_wrong_service.py, "where the address comes from").
The namespace was left behind a few lines above it:

    ONTO = os.environ.get("SHANTY_ONTO_NS") or "http://shantytown.example/ontology/"

Read ONCE, at import, from the ambient environment only. So a deployment could
write `SHANTY_ONTO_NS` down in the same env.json the address now comes from and the
clients would still not see it — while the invocations least likely to have
inherited the variable are the usual ones: a cron entry, a hook the harness
re-exec'd outside the settings env, a bare `st` from a non-crew shell.

WHY THIS IS WORSE THAN THE ADDRESS BUG, not merely equal to it. A wrong ADDRESS is
now detectable: something either fails to answer or answers unlike a quipu, and
`QuipuNotQuipu` names the remedy. A wrong NAMESPACE is answered by the REAL quipu,
correctly, with `{"rows": []}` — because nothing in any real fleet's graph is typed
`<http://shantytown.example/ontology/CrewMember>`. There is no wrong-service signal
to catch, because there is no wrong service. Zero rows from the right server is the
one failure this client CANNOT distinguish from a true answer, and it lands as
"nobody exists", said with a straight face, at exit 0.

Measured on this fleet's live graph before the fix, from a shell exporting nothing:
the address resolved correctly out of env.json and `all()` still returned `[]`
against 12 real CrewMembers — `st roles --check --registry quipu` printing
"0 agents, every one reports somewhere." and exiting 0.

So the namespace must be resolved the same way and out of the same file:

    explicit arg  ->  <root>/env.json  ->  $SHANTY_ONTO_NS  ->  DEFAULT_ONTO

and it must be resolved PER CLIENT, not per interpreter — that is what the
import-time constant made impossible, and it is what these tests pin.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from shantytown.protocols import Agent
from shantytown.quipu import DEFAULT_ONTO, QuipuRegistry, resolve_onto
from shantytown.quipu_events import QuipuEvents

DECLARED = "https://declared.invalid/ontology#"
AMBIENT = "https://ambient.invalid/ontology#"


# --- where the namespace comes from ------------------------------------------

def test_explicit_argument_wins_over_everything(monkeypatch, tmp_path):
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": DECLARED}))
    monkeypatch.setenv("SHANTY_ONTO_NS", AMBIENT)
    assert resolve_onto("https://explicit.invalid/ns#", tmp_path) == \
        "https://explicit.invalid/ns#"


def test_env_json_outranks_the_ambient_environment(monkeypatch, tmp_path):
    """THE LOAD-BEARING ORDER, same as the address. The deployment's written-down
    namespace beats whatever a shell exported, so a process holding a STALE value —
    one exported before the graph was repointed — still joins the deployed facts
    instead of landing beside them."""
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": DECLARED}))
    monkeypatch.setenv("SHANTY_ONTO_NS", AMBIENT)
    assert resolve_onto(None, tmp_path) == DECLARED


def test_ambient_env_is_used_when_the_deployment_declared_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("SHANTY_ONTO_NS", AMBIENT)
    assert resolve_onto(None, tmp_path) == AMBIENT


def test_a_hook_that_inherited_NOTHING_still_finds_the_declared_namespace(
        monkeypatch, tmp_path):
    """The regression this whole file is written against: no SHANTY_ONTO_NS in the
    environment at all must still reach the deployment's graph, rather than the
    library's example namespace, which holds none of any real fleet's facts and
    answers every query with zero rows."""
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": DECLARED}))
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    assert resolve_onto(None, tmp_path) == DECLARED


def test_root_falls_back_to_SHANTY_ROOT_for_library_callers(monkeypatch, tmp_path):
    """A client built with no root (`QuipuRegistry()`) resolves the same env.json
    the CLI does, via $SHANTY_ROOT — otherwise the deployment's answer reaches only
    the callers that happened to thread a root through, which is exactly how the
    address bug survived as long as it did."""
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": DECLARED}))
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    assert QuipuRegistry().onto == DECLARED


def test_the_stock_default_is_the_libraries_example_namespace(monkeypatch, tmp_path):
    """Unconfigured falls back to a documentation namespace, NOT to any fleet's: a
    public repo carries no deployment's identity. And unlike the address, this
    default is NOT made safe by detection — which is why resolution is the whole of
    the fix here rather than half of it."""
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    assert resolve_onto(None, tmp_path) == DEFAULT_ONTO
    assert DEFAULT_ONTO == "http://shantytown.example/ontology/"


def test_unparseable_env_json_does_not_break_resolution(monkeypatch, tmp_path):
    """A broken config file means "the deployment said nothing" — the next source
    answers, and the client does not go down over it."""
    (tmp_path / "env.json").write_text("{ not json")
    monkeypatch.setenv("SHANTY_ONTO_NS", AMBIENT)
    assert resolve_onto(None, tmp_path) == AMBIENT


def test_both_clients_resolve_the_SAME_namespace(monkeypatch, tmp_path):
    """The registry and the events client must not read one deployment's graph under
    two namespaces. They share the resolver for exactly that reason — and the events
    client used to share nothing: it carried its own hardcoded copy."""
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": DECLARED}))
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    assert QuipuRegistry(root=tmp_path).onto == QuipuEvents(root=tmp_path).onto == DECLARED


def test_the_clients_do_not_read_the_namespace_at_IMPORT(monkeypatch, tmp_path):
    """Resolution happens when a client is CONSTRUCTED, so a value that becomes
    available AFTER import is still honoured — the property the old constant could
    not have, and the reason a re-exec'd hook could not be fixed by exporting
    anything.

    Note there is deliberately no `importlib.reload` here: `shantytown.quipu` owns
    the exception classes every other quipu test matches on, so reloading it swaps
    `QuipuNotQuipu` for a fresh class object and every `pytest.raises` in the suite
    that runs after stops matching. This process imported the module long before
    this line ran, so setting the variable now IS the after-import case."""
    monkeypatch.setenv("SHANTY_ONTO_NS", "https://after-import.invalid/ns#")
    assert QuipuRegistry(server="http://test.invalid",
                         root=tmp_path).onto == "https://after-import.invalid/ns#"


# --- the namespace reaches the WIRE ------------------------------------------
#
# Resolving it into an attribute proves nothing on its own: the bug was that the
# SPARQL and the turtle were BUILT from the import-time value at class-definition
# time, so a correctly-resolved namespace could still fail to appear in a single
# byte the client actually sends. These read the request bodies.

def _capture(monkeypatch, reply: dict):
    """Record every request the client sends; answer them all with `reply`."""
    sent: list[tuple[str, dict]] = []

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(reply).encode()

    def _urlopen(req, timeout=None):
        sent.append((req.full_url, json.loads(req.data.decode())))
        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return sent


def _deployment(monkeypatch, tmp_path):
    """A deployment that declared BOTH its address and its namespace in env.json and
    exported neither — the cron / re-exec'd-hook case."""
    (tmp_path / "env.json").write_text(json.dumps({
        "QUIPU_SERVER": "http://declared.invalid",
        "SHANTY_ONTO_NS": DECLARED,
    }))
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    monkeypatch.delenv("QUIPU_SERVER", raising=False)


def test_the_crew_query_carries_the_DECLARED_namespace(monkeypatch, tmp_path):
    """THE BUG, on the wire. `_ALL` was a class attribute built with an f-string at
    class-definition time, so the PREFIX was frozen at import — and a query prefixed
    with the example namespace matches nothing in a real graph and comes back
    `{"rows": []}` from a perfectly healthy quipu."""
    _deployment(monkeypatch, tmp_path)
    sent = _capture(monkeypatch, {"rows": []})

    QuipuRegistry(root=tmp_path).all()

    url, body = sent[0]
    assert url == "http://declared.invalid/query"
    assert f"PREFIX a: <{DECLARED}>" in body["query"]
    assert DEFAULT_ONTO not in body["query"]


def test_the_query_form_is_preserved_when_the_prefix_moves(monkeypatch, tmp_path):
    """quipu's engine REJECTS `FILTER NOT EXISTS` — quipu.py says so at length, and
    _query raises on an error body, so the modern form would turn every all() into
    QuipuUnreachable fleet-wide. Making the PREFIX per-instance must not have
    rewritten a single character of the body it is prepended to."""
    _deployment(monkeypatch, tmp_path)
    sent = _capture(monkeypatch, {"rows": []})

    QuipuRegistry(root=tmp_path).all()

    q = sent[0][1]["query"]
    assert "OPTIONAL { ?s a:crewStatus ?cs } FILTER(!bound(?cs))" in q
    assert "NOT EXISTS" not in q
    assert q == (f"PREFIX a: <{DECLARED}> "
                 "SELECT ?s ?rt WHERE { ?s a a:CrewMember . "
                 "OPTIONAL { ?s a:crewStatus ?cs } FILTER(!bound(?cs)) "
                 "OPTIONAL { ?s a:reports_to ?rt } }")


def test_two_registries_in_ONE_interpreter_can_hold_DIFFERENT_namespaces(
        monkeypatch, tmp_path):
    """The property an import-time constant cannot have. Not academic: `st` is a
    library as well as a CLI, so a per-interpreter namespace means the SECOND
    workspace a process touches is silently read under the FIRST one's identity."""
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    a, b = tmp_path / "a", tmp_path / "b"
    for d, ns in ((a, "https://a.invalid/ns#"), (b, "https://b.invalid/ns#")):
        d.mkdir()
        (d / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": ns}))

    sent = _capture(monkeypatch, {"rows": []})
    QuipuRegistry(server="http://test.invalid", root=a).all()
    QuipuRegistry(server="http://test.invalid", root=b).all()

    assert "https://a.invalid/ns#" in sent[0][1]["query"]
    assert "https://b.invalid/ns#" in sent[1][1]["query"]


def test_the_identity_WRITE_lands_in_the_declared_namespace(monkeypatch, tmp_path):
    """A write is worse than a read to get wrong: a read under the example namespace
    finds nothing, but a WRITE under it CREATES the fleet's crew beside its own
    facts, in a namespace nothing else queries — the fragmentation quipu.py's
    comment on this value warns about, silently, at exit 0."""
    _deployment(monkeypatch, tmp_path)
    # rows=[] -> get() raises LookupError -> no retract; conforms=True -> knot ok.
    sent = _capture(monkeypatch, {"rows": [], "conforms": True})

    QuipuRegistry(root=tmp_path).set(
        Agent(name="ian", role="worker", reports_to="hammond"))

    knots = [b for url, b in sent if url.endswith("/knot")]
    assert len(knots) == 1
    assert f"@prefix a: <{DECLARED}> ." in knots[0]["turtle"]
    assert DEFAULT_ONTO not in knots[0]["turtle"]


def test_a_retract_targets_the_declared_namespace(monkeypatch, tmp_path):
    """/retract is addressed by absolute IRI, not by prefix, so the namespace is
    built into all three fields. Retracting under the wrong one removes nothing —
    and because triple-level retraction already answers `retracted: 0` without an
    error key, it would surface as the graph refusing a re-parent rather than as a
    misconfiguration."""
    _deployment(monkeypatch, tmp_path)
    sent = _capture(monkeypatch, {"retracted": 1})

    reg = QuipuRegistry(root=tmp_path)
    reg._retract("ian", "reports_to", "hammond")

    url, body = sent[0]
    assert url == "http://declared.invalid/retract"
    assert body == {
        "entity": DECLARED + "ian",
        "predicate": DECLARED + "reports_to",
        "value": DECLARED + "hammond",
    }


def test_the_governed_workflow_query_carries_the_DECLARED_namespace(
        monkeypatch, tmp_path):
    """The events client did not merely read the namespace at import time — it never
    read it AT ALL. `_WORKFLOWS_SPARQL` was a module constant holding one
    deployment's ontology IRI, hardcoded, in a public repo: an internal hostname the
    scrub ratchet's pattern does not cover (`.local`, not `.lan`/`.svc`), and a
    namespace no other deployment could ever repoint."""
    _deployment(monkeypatch, tmp_path)
    sent = _capture(monkeypatch, {"rows": []})

    QuipuEvents(root=tmp_path).assigned_workflows()

    url, body = sent[0]
    assert url == "http://declared.invalid/query"
    assert f"PREFIX aegis: <{DECLARED}>" in body["query"]
    assert ".local/ontology/" not in body["query"]


def test_no_module_level_namespace_constant_survives():
    """The import-time constant is GONE, not merely bypassed.

    Deliberately asserting on absence: leaving `ONTO` importable would leave the bug
    importable. Anything reading it would get the value this deployment's env.json
    was written to override, and would get it silently — the failure mode being
    fixed. Absence makes that an ImportError at the call site instead."""
    import shantytown.quipu as q
    assert not hasattr(q, "ONTO"), (
        "shantytown.quipu.ONTO is back. A module-level namespace is resolved at "
        "import, before any root is known, so it cannot see <root>/env.json — use "
        "resolve_onto(onto, root), or a client's .onto attribute."
    )


@pytest.mark.parametrize("ns", [
    "https://deployment.invalid/ontology#",   # hash namespace
    "https://deployment.invalid/ontology/",   # slash namespace
])
def test_both_hash_and_slash_namespaces_are_carried_verbatim(monkeypatch, tmp_path, ns):
    """The value passes through UNTOUCHED — no separator appended, trimmed or
    normalised. Both forms are legal RDF, a real deployment uses the hash form, and
    "helpfully" repairing a namespace is itself a way to stop joining the facts you
    were pointed at."""
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_ONTO_NS": ns}))
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    assert QuipuRegistry(server="http://test.invalid", root=tmp_path).onto == ns
    sent = _capture(monkeypatch, {"rows": []})
    QuipuRegistry(server="http://test.invalid", root=tmp_path).all()
    assert f"PREFIX a: <{ns}>" in sent[0][1]["query"]
