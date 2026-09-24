"""Cross-host lookup without shadow local cards, and planned graph creation."""
import json
import subprocess
from dataclasses import replace

import pytest

from shantytown import cli, tier
from shantytown.answer import Answer
from shantytown.protocols import Agent
from shantytown.quipu import QuipuUnreachable


class Graph:
    def __init__(self):
        self.agents = {
            "local_admin": Agent("local_admin", "administrator", host="desktop"),
            "remote_admin": Agent("remote_admin", "administrator", host="laptop"),
        }
        self.writes = []

    def get(self, name):
        if name not in self.agents:
            raise LookupError(name)
        return self.agents[name]

    def all(self):
        return Answer.complete_read(list(self.agents.values()), how="test graph")

    def set(self, agent):
        self.writes.append(agent)
        self.agents[agent.name] = agent


class Panes:
    def __init__(self):
        self.sent = []

    def session_name(self, pane):
        return "session-local" if pane == "%7" else None

    def exists(self, pane):
        return True  # Includes a colliding off-host pane name: never send to it.

    def send(self, pane, text):
        self.sent.append((pane, text))


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    (tmp_path / "crew").mkdir()
    (tmp_path / "crew/local_admin.json").write_text(json.dumps({
        "role": "administrator", "pane": "session-local", "host": "desktop",
    }))
    (tmp_path / "shantytown.toml").write_text(
        '[host]\nname = "desktop"\n'
        '[host.peers.laptop]\nssh = "operator@laptop.example"\nroot = "/opt/crew"\n'
        '[env]\nQUIPU_SERVER = "http://graph.example"\n'
        'SHANTY_ONTO_NS = "https://fleet.example/ontology/"\n')
    graph, panes = Graph(), Panes()
    monkeypatch.setattr(cli, "QuipuRegistry", lambda **kwargs: graph)
    monkeypatch.setattr(cli, "_panes", lambda args: panes)
    monkeypatch.setenv("SHANTY_AGENT", "local_admin")
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setattr(cli, "_emit_role_settings", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "_report_who_the_rewrite_did_not_reach", lambda *args: None)
    return tmp_path, graph, panes


@pytest.mark.parametrize("registry", ["files", "quipu"])
def test_remote_recipient_without_local_card_and_verified_local_sender(fleet, monkeypatch, capsys, registry):
    root, _, panes = fleet
    calls = []
    real_run = subprocess.run

    def relay(argv, **kwargs):
        if argv[0] != "ssh":
            return real_run(argv, **kwargs)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "remote accepted\n", "")

    monkeypatch.setattr(subprocess, "run", relay)
    assert cli.main(["--root", str(root), "--registry", registry,
                     "inbox", "remote_admin", "hello"]) == cli.OK
    assert len(calls) == 1 and calls[0][0] == "ssh"
    assert "SHANTY_AGENT=local_admin" in calls[0][-1]
    assert "inbox remote_admin hello" in calls[0][-1]
    assert "relayed to host laptop" in capsys.readouterr().out
    assert not (root / "crew/remote_admin.json").exists()
    assert panes.sent == []


def test_graph_recipient_does_not_bypass_sender_identity_disagreement(fleet, monkeypatch, capsys):
    root, _, panes = fleet
    monkeypatch.setenv("SHANTY_AGENT", "remote_admin")
    assert cli.main(["--root", str(root), "--registry", "quipu",
                     "inbox", "remote_admin", "hello"]) == cli.REFUSED
    assert "identity disagreement" in capsys.readouterr().err
    assert panes.sent == []


def test_durable_remote_delivery_never_uses_a_colliding_local_pane(fleet, capsys):
    from shantytown.inbox import FilesInbox
    root, graph, panes = fleet
    graph.agents["remote_admin"] = replace(graph.get("remote_admin"), pane="session-local")
    assert cli.main(["--root", str(root), "--backend", "files", "inbox",
                     "-d", "remote_admin", "survive"]) == cli.OK
    assert len(FilesInbox(root / "inbox").unread("remote_admin")) == 1
    assert panes.sent == []
    assert "delivered to inbox" in capsys.readouterr().out


def test_failed_graph_lookup_is_unknown_not_absence(fleet, monkeypatch, capsys):
    root, graph, _ = fleet
    from shantytown import fleet as peer_census
    monkeypatch.setattr(peer_census, "collect", lambda peers: [
        {"host": "laptop", "agents": [], "error": "test peer unreachable"},
    ])

    def unavailable(name):
        raise QuipuUnreachable("test offline")

    monkeypatch.setattr(graph, "get", unavailable)
    assert cli.main(["--root", str(root), "inbox", "remote_admin", "hello"]) == cli.CANNOT_TELL
    assert "nothing was sent" in capsys.readouterr().err


@pytest.mark.parametrize("registry", ["files", "quipu"])
@pytest.mark.parametrize("cached", [False, True])
def test_graph_outage_uses_files_for_offhost_recipient(
        fleet, monkeypatch, capsys, registry, cached):
    root, graph, panes = fleet
    if cached:
        (root / "crew/remote_admin.json").write_text(json.dumps({
            "role": "administrator", "host": "laptop", "pane": "session-remote",
        }))

    def unavailable(name):
        raise QuipuUnreachable("test timeout")

    monkeypatch.setattr(graph, "get", unavailable)
    calls = []
    real_run = subprocess.run

    def ssh(argv, **kwargs):
        if argv[0] != "ssh":
            return real_run(argv, **kwargs)
        calls.append(argv[-1])
        assert "--registry files" in argv[-1]
        if "crew --json --local" in argv[-1]:
            body = dict(version=1, scope="local", complete=True, host="laptop", agents=[dict(
                name="remote_admin", role="administrator", state="up", work="idle",
                posture="normal", harness="codex", settings="current", tree="current",
                pane="session-remote", live=True,
            )])
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        assert "SHANTY_AGENT=local_admin" in argv[-1]
        assert "inbox remote_admin hello" in argv[-1]
        return subprocess.CompletedProcess(argv, 0, "remote accepted\n", "")

    monkeypatch.setattr(subprocess, "run", ssh)
    assert cli.main(["--root", str(root), "--registry", registry,
                     "inbox", "remote_admin", "hello"]) == cli.OK
    assert len(calls) == (1 if cached else 2)
    captured = capsys.readouterr()
    if registry == "quipu" or not cached:
        assert "files registry fallback" in captured.err
    assert "relayed to host laptop" in captured.out
    assert panes.sent == []
    assert (root / "crew/remote_admin.json").exists() == cached


def test_graph_outage_files_fallback_keeps_sender_disagreement_refusal(
        fleet, monkeypatch, capsys):
    root, graph, panes = fleet
    monkeypatch.setenv("SHANTY_AGENT", "remote_admin")

    def unexpected(*args, **kwargs):
        pytest.fail("sender disagreement must refuse before recipient lookup")

    monkeypatch.setattr(graph, "get", unexpected)
    assert cli.main(["--root", str(root), "--registry", "quipu",
                     "inbox", "remote_admin", "hello"]) == cli.REFUSED
    assert "identity disagreement" in capsys.readouterr().err
    assert not panes.sent


@pytest.mark.parametrize("owners", [0, 2])
def test_graph_outage_never_guesses_between_peer_owners(fleet, monkeypatch, capsys, owners):
    from shantytown import fleet as peer_census
    root, graph, panes = fleet

    def unavailable(name):
        raise QuipuUnreachable("test timeout")

    monkeypatch.setattr(graph, "get", unavailable)
    monkeypatch.setattr(peer_census, "collect", lambda peers: [
        {"host": f"peer{n}", "error": None, "agents": [
            {"name": "remote_admin", "role": "administrator", "host": f"peer{n}"},
        ]} for n in range(owners)
    ])
    assert cli.main(["--root", str(root), "inbox", "remote_admin", "hello"]) == cli.CANNOT_TELL
    assert "nothing was sent" in capsys.readouterr().err
    assert not panes.sent


@pytest.mark.parametrize("error", ["rejected", "wrong-service"])
def test_graph_protocol_errors_do_not_fall_back(fleet, monkeypatch, capsys, error):
    from shantytown.quipu import QuipuNotQuipu, QuipuQueryRejected
    from shantytown import fleet as peer_census
    root, graph, panes = fleet

    def failed(name):
        if error == "wrong-service":
            raise QuipuNotQuipu("test wrong server")
        raise QuipuQueryRejected("test refused query")

    def unexpected(*args):
        pytest.fail("protocol errors must not be treated as a transport outage")

    monkeypatch.setattr(graph, "get", failed)
    monkeypatch.setattr(peer_census, "collect", unexpected)
    assert cli.main(["--root", str(root), "inbox", "remote_admin", "hello"]) == cli.CANNOT_TELL
    assert "nothing was sent" in capsys.readouterr().err
    assert not panes.sent


def create_args(root, *extra):
    return ["--root", str(root), "--registry", "quipu", "fleet", "roles", "set",
            "new_worker", "worker", "--create", "--lead", "local_admin", *extra]


def test_new_graph_worker_is_host_scoped_and_next_projection_places_it(fleet, capsys):
    root, graph, _ = fleet
    assert cli.main(create_args(root)) == cli.OK
    new = graph.get("new_worker")
    assert (new.role, new.reports_to, new.host) == ("worker", "local_admin", "desktop")
    assert not (root / "crew/new_worker.json").exists()
    assert cli.main(["--root", str(root), "fleet", "roles", "sync", "--dry-run"]) == cli.OK
    output = capsys.readouterr().out
    assert "new_worker NEW CARD -> worker" in output
    assert "remote_admin@laptop" in output


def test_create_dry_run_has_no_graph_or_card_write(fleet):
    root, graph, _ = fleet
    assert cli.main(create_args(root, "--dry-run")) == cli.OK
    assert graph.writes == []
    assert not (root / "crew/new_worker.json").exists()


def test_registration_adopts_existing_local_launch_settings(fleet, monkeypatch):
    root, graph, _ = fleet
    card = root / "crew/new_worker.json"
    before = json.dumps({"role": "worker", "harness": "codex", "model": "example-model"})
    card.write_text(before)
    emitted = []
    monkeypatch.setattr(cli, "_emit_role_settings",
                        lambda *args, **kwargs: emitted.append(kwargs["harness_name"]) or [])
    assert cli.main(create_args(root)) == cli.OK
    assert graph.writes[0].harness == "codex"
    assert emitted == ["codex"]
    assert card.read_text() == before


def test_create_refuses_an_existing_agent_without_overwriting_it(fleet):
    root, graph, _ = fleet
    graph.agents["new_worker"] = Agent("new_worker", "worker", host="laptop")
    assert cli.main(create_args(root)) == cli.REFUSED
    assert graph.writes == [] and graph.get("new_worker").host == "laptop"


def test_create_requires_valid_supervisor(fleet):
    root, graph, _ = fleet
    graph.agents["local_admin"] = replace(graph.get("local_admin"), role="worker")
    assert cli.main(create_args(root)) == cli.REFUSED
    assert graph.writes == []


def test_create_requires_declared_host(fleet, monkeypatch):
    root, graph, _ = fleet
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    config = root / "shantytown.toml"
    config.write_text(config.read_text().replace('[host]\nname = "desktop"\n', ""))
    assert cli.main(create_args(root)) == cli.REFUSED
    assert graph.writes == []


def test_graph_write_without_readback_is_not_reported_as_created(fleet, monkeypatch, capsys):
    root, graph, _ = fleet
    monkeypatch.setattr(graph, "set", lambda agent: graph.writes.append(agent))
    assert cli.main(create_args(root)) == cli.CANNOT_TELL
    assert graph.writes
    assert "wrote:" not in capsys.readouterr().out


def test_role_plan_preserves_launch_fields_and_host():
    graph = Graph()
    graph.agents["worker"] = Agent("worker", "worker", reports_to="local_admin",
                                    host="desktop", harness="codex", model="example-model")
    planned = tier.plan_role_set(graph, "worker", "worker").writes[0]
    assert (planned.host, planned.harness, planned.model) == ("desktop", "codex", "example-model")


def test_reports_to_typo_is_a_usage_error_not_a_python_traceback(fleet, capsys):
    root, _, _ = fleet
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(root), "fleet", "roles", "set",
                  "new_worker", "worker", "--reports-to", "local_admin"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
