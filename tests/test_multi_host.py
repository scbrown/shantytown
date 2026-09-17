"""Multi-host st: cards carry a host, sync is host-scoped, delivery routes by host
(aegis-5du1bz, Steve 2026-09-16).

What produced this, measured on the MacBook before it existed: `st fleet roles sync
--dry-run` there would have minted 13 cards for vati's ENTIRE crew, demoted the
Mac's administrator (hammond) to worker and orphaned it — because neither the
card nor the graph said which host anyone runs on, so "the graph's crew" and
"this host's crew" were the same set.

The contract these pin:
  · a card and a graph member CARRY a host; absent reads None, never "here"
  · `[host] name` is DECLARED; local_host never guesses from hostname(1)
  · a graph that names no host projects exactly as before (single-host fleets
    keep working, including every existing test in test_project_guard.py)
  · once the graph places anyone, a deployment with no declared host REFUSES
    to sync (could-not-tell), and a declared host projects ONLY its own members,
    naming what it skipped
  · a cross-host lead is NOT "not in the registry"
  · a sync NEVER demotes the administrator: named on dry-run, refused on a run,
    no flag overrides it
  · an ephemeral inbox to an off-host agent relays via the declared peer, and
    REFUSES naming the host when no peer is declared
  · the projection's graph client is built WITH the deployment root, so `[env]`
    is read (the plain-shell fallback-namespace bug hammond measured)
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shantytown import cli, config
from shantytown.answer import Answer
from shantytown.cli import main, OK, REFUSED, CANNOT_TELL
from shantytown.deployment import local_host
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.quipu import all_query, derive_agents


# ---------------------------------------------------------------- fixtures --

def crew(tmp_path: Path, **agents) -> Path:
    d = tmp_path / "crew"; d.mkdir(exist_ok=True)
    for n, spec in agents.items():
        (d / f"{n}.json").write_text(json.dumps(spec))
    return tmp_path


def declare_host(root: Path, name: str, peers: dict | None = None) -> None:
    body = f'[host]\nname = "{name}"\n'
    for pname, spec in (peers or {}).items():
        body += (f'\n[host.peers.{pname}]\nssh = "{spec["ssh"]}"\n'
                 f'root = "{spec["root"]}"\n')
    (root / "shantytown.toml").write_text(body)


def graph(monkeypatch, *agents):
    class FakeQuipu:
        def all(self):
            return Answer.complete_read(list(agents), how="test graph")
    monkeypatch.setattr(cli, "QuipuRegistry", FakeQuipu)


def panes(monkeypatch, *live):
    class FakeTmux:
        def __init__(self, socket=None):
            self.socket = socket
        def exists(self, pane):
            return pane in live
        def send(self, pane, msg):
            self.sent = (pane, msg)
        def capture(self, pane):
            return ""
    monkeypatch.setattr(cli, "Tmux", FakeTmux)


def card(root, name):
    return json.loads((root / "crew" / f"{name}.json").read_text())


# --------------------------------------------------------- the card + graph --

def test_card_round_trips_host_and_absent_reads_none(tmp_path):
    reg = FilesRegistry(tmp_path / "crew")
    reg.set(Agent(name="hammond", role="administrator", host="macbookair-stiwi"))
    assert reg.get("hammond").host == "macbookair-stiwi"
    reg.set(Agent(name="lex", role="worker", reports_to="hammond"))
    assert reg.get("lex").host is None, "nobody said is None, never 'here'"
    # a role set that carries no host must not un-scope a scoped card
    reg.set(Agent(name="hammond", role="lead", reports_to="sattler"))
    assert reg.get("hammond").host == "macbookair-stiwi"


def test_roster_query_carries_the_host_column():
    q = all_query("http://x/")
    assert "?h" in q and "OPTIONAL { ?s a:runsOn ?h }" in q


def test_derive_agents_reads_the_host_per_member():
    rows = [{"s": "http://x/sattler"},
            {"s": "http://x/wu", "rt": "http://x/sattler", "h": "http://x/vati"},
            {"s": "http://x/hammond", "h": "http://x/macbookair-stiwi"}]
    got = {a.name: a.host for a in derive_agents(rows)}
    assert got == {"sattler": None, "wu": "vati", "hammond": "macbookair-stiwi"}


# ------------------------------------------------------------- local_host --

def test_local_host_is_declared_then_env_then_none(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    assert local_host(tmp_path) is None
    monkeypatch.setenv("SHANTY_HOST", "from-env")
    assert local_host(tmp_path) == "from-env"
    declare_host(tmp_path, "vati")
    assert local_host(tmp_path) == "vati", "the file beats the env"


def test_host_table_parses_peers_and_refuses_half_a_peer(tmp_path):
    declare_host(tmp_path, "vati", {"macbookair-stiwi": {
        "ssh": "stiwi@mac.example", "root": "/opt/st/.shanty"}})
    cfg = config.load(tmp_path)
    assert cfg.host_name == "vati"
    assert cfg.host_peers["macbookair-stiwi"].ssh == "stiwi@mac.example"
    (tmp_path / "shantytown.toml").write_text(
        '[host]\nname = "vati"\n[host.peers.mac]\nssh = "x@y"\n')
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


# ------------------------------------------------------------ roles sync --

def test_a_graph_with_no_hosts_projects_as_before(tmp_path, monkeypatch):
    """Single-host fleets keep working: nothing placed, nothing scoped."""
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="administrator"),
          Agent(name="wu", role="worker", reports_to="sattler"))
    panes(monkeypatch)
    assert main(["--root", str(root), "roles", "sync"]) == OK
    assert card(root, "wu")["role"] == "worker"


def test_placed_graph_and_no_declared_host_is_could_not_tell(tmp_path, monkeypatch, capsys):
    """The MacBook failure: a sync that cannot tell which host it is must not
    default to 'all of them'."""
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    root = crew(tmp_path, hammond={"role": "administrator", "pane": "shanty-hammond"})
    graph(monkeypatch,
          Agent(name="hammond", role="administrator", host="macbookair-stiwi"),
          Agent(name="sattler", role="administrator", host="vati"),
          Agent(name="wu", role="worker", reports_to="sattler", host="vati"))
    panes(monkeypatch)
    rc = main(["--root", str(root), "roles", "sync"])
    assert rc == CANNOT_TELL
    assert not (root / "crew" / "wu.json").exists()
    assert "declares no host" in capsys.readouterr().err


def test_declared_host_projects_only_its_own_members_and_names_the_rest(
        tmp_path, monkeypatch, capsys):
    root = crew(tmp_path, hammond={"role": "administrator", "pane": "shanty-hammond"})
    declare_host(root, "macbookair-stiwi")
    graph(monkeypatch,
          Agent(name="hammond", role="administrator", host="macbookair-stiwi"),
          Agent(name="lex", role="worker", reports_to="hammond", host="macbookair-stiwi"),
          Agent(name="sattler", role="administrator", host="vati"),
          Agent(name="wu", role="worker", reports_to="sattler", host="vati"),
          Agent(name="ghost", role="worker", reports_to="sattler"))       # unscoped
    panes(monkeypatch)
    rc = main(["--root", str(root), "roles", "sync"])
    out = capsys.readouterr().out
    assert rc == OK
    assert card(root, "lex")["role"] == "worker"
    assert card(root, "lex")["host"] == "macbookair-stiwi"
    for absent in ("sattler", "wu", "ghost"):
        assert not (root / "crew" / f"{absent}.json").exists(), absent
    assert "skipping 2 on other host(s): sattler@vati, wu@vati" in out
    assert "skipping 1 UNSCOPED" in out and "ghost" in out


def test_a_cross_host_lead_is_not_an_orphan(tmp_path, monkeypatch, capsys):
    """hammond = lead reporting to sattler (on vati) is the ruled shape; the
    orphan guard must not read the remote lead as 'not in the registry'."""
    root = crew(tmp_path, hammond={"role": "administrator", "pane": "shanty-hammond"})
    declare_host(root, "macbookair-stiwi")
    graph(monkeypatch,
          Agent(name="sattler", role="administrator", host="vati"),
          Agent(name="lex", role="worker", reports_to="hammond", host="macbookair-stiwi"),
          Agent(name="hammond", role="administrator", host="macbookair-stiwi"))
    panes(monkeypatch)
    rc = main(["--root", str(root), "roles", "sync"])
    assert rc == OK, capsys.readouterr()
    assert "NEWLY BROKEN" not in capsys.readouterr().out


def test_sync_never_demotes_the_administrator(tmp_path, monkeypatch, capsys):
    """The exact MacBook dry-run: hammond reads back as worker from the graph."""
    root = crew(tmp_path, hammond={"role": "administrator", "pane": "shanty-hammond"})
    declare_host(root, "macbookair-stiwi")
    graph(monkeypatch,
          Agent(name="hammond", role="worker", reports_to="sattler", host="macbookair-stiwi"),
          Agent(name="sattler", role="administrator", host="vati"))
    panes(monkeypatch)          # hammond NOT live: the liveness guard must not be what saves it
    rc = main(["--root", str(root), "roles", "sync", "--dry-run"])
    err = capsys.readouterr().err
    assert rc == OK, "dry-run keeps its contract"
    assert "DEMOTE the administrator hammond" in err
    for flags in ([], ["--force"], ["--allow-breakage"], ["--force", "--allow-breakage"]):
        rc = main(["--root", str(root), "roles", "sync", *flags])
        assert rc == REFUSED, flags
        assert card(root, "hammond")["role"] == "administrator", flags


def test_the_graph_client_is_built_with_the_deployment_root(tmp_path, monkeypatch):
    """hammond's config fact: a plain-shell sync ignored [env] because the
    factory built QuipuRegistry() unrooted."""
    seen = {}
    class Rooted:
        def __init__(self, root=None):
            seen["root"] = root
        def all(self):
            return Answer.complete_read(
                [Agent(name="sattler", role="administrator")], how="t")
    monkeypatch.setattr(cli, "QuipuRegistry", Rooted)
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "p"})
    panes(monkeypatch)
    assert main(["--root", str(root), "roles", "sync", "-n"]) == OK
    assert Path(seen["root"]) == root


# ---------------------------------------------------------------- inbox --

def _inbox_args(root, agent, msg):
    return ["--root", str(root), "inbox", agent, msg]


def test_ephemeral_send_to_an_off_host_agent_refuses_naming_the_host(
        tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("SHANTY_AGENT", "sattler")
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler", "host": "vati"},
                hammond={"role": "administrator", "pane": "shanty-hammond",
                         "host": "macbookair-stiwi"})
    declare_host(root, "vati")
    panes(monkeypatch, "shanty-hammond")   # a pane of that NAME exists here — irrelevant
    rc = main(_inbox_args(root, "hammond", "ping"))
    err = capsys.readouterr().err
    assert rc == REFUSED
    assert "lives on host macbookair-stiwi" in err
    assert "no [host.peers.macbookair-stiwi]" in err


def test_ephemeral_send_relays_through_the_declared_peer(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("SHANTY_AGENT", "sattler")
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler", "host": "vati"},
                hammond={"role": "administrator", "pane": "shanty-hammond",
                         "host": "macbookair-stiwi"})
    declare_host(root, "vati", {"macbookair-stiwi": {
        "ssh": "stiwi@mac.example", "root": "/opt/st/.shanty"}})
    panes(monkeypatch)
    calls = {}
    class Res:
        returncode = 0; stdout = "  -> hammond    sent to pane shanty-hammond\n"; stderr = ""
    def fake_run(argv, **kw):
        calls["argv"] = argv; return Res()
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = main(_inbox_args(root, "hammond", "ping from vati"))
    out = capsys.readouterr().out
    assert rc == OK
    assert calls["argv"][0] == "ssh" and "stiwi@mac.example" in calls["argv"]
    remote = calls["argv"][-1]
    assert "SHANTY_AGENT=sattler" in remote
    assert "--root /opt/st/.shanty inbox hammond 'ping from vati'" in remote
    assert "relayed to host macbookair-stiwi" in out


def test_ephemeral_send_stays_local_when_nobody_declared_hosts(tmp_path, monkeypatch, capsys):
    """Every existing deployment: no [host], no host on cards -> send-keys as before."""
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    monkeypatch.setenv("SHANTY_AGENT", "sattler")
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler"},
                wu={"role": "lead", "reports_to": "sattler", "pane": "shanty-wu"})
    panes(monkeypatch, "shanty-wu")
    monkeypatch.setattr(cli, "_looks_stranded", lambda panes, pane: False)
    rc = main(_inbox_args(root, "wu", "hello"))
    assert rc == OK
    assert "sent to pane shanty-wu" in capsys.readouterr().out


# ------------------------------------------------------------ stop event --

def test_offhost_stop_event_goes_durable_and_local_one_does_not(tmp_path, monkeypatch):
    from shantytown import stop_event
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler", "host": "vati"},
                hammond={"role": "lead", "reports_to": "sattler", "pane": "shanty-hammond",
                         "host": "macbookair-stiwi"},
                wu={"role": "lead", "reports_to": "sattler", "pane": "shanty-wu",
                    "host": "vati"})
    declare_host(root, "macbookair-stiwi")
    reg = FilesRegistry(root / "crew")
    calls = []
    class Res:
        returncode = 0; stdout = "delivered to inbox as aegis-x (br)\n"; stderr = ""
    monkeypatch.setattr(stop_event.subprocess, "run",
                        lambda argv, **kw: (calls.append(argv), Res())[1])
    stop_event._offhost_durable(reg, root, "sattler", "hammond", "aegis-1", "ev-1")
    assert len(calls) == 1 and calls[0][-3:-1] == ["-d", "sattler"]
    assert "not drainable from vati" in calls[0][-1]
    # a recipient on THIS host: nothing relayed
    stop_event._offhost_durable(reg, root, "hammond", "lex", None, "ev-2")
    assert len(calls) == 1
