"""A stale D-Bus session GUID silently switches every pane ceiling off (aegis-ihl7ie).

`panemem.launch_env()` is the launcher's only defence, and it landed without a
test. The bug it guards is not "the bus is down" — it is that a session bus which
RESTARTS leaves its old GUID behind in every inherited environment. sd-bus then
rejects the connection at AUTH, tmux's scope call fails, and tmux logs it at
DEBUG and carries on, so the pane lands in the LAUNCHER's cgroup and there is no
per-pane scope for a ceiling to bind to. Measured on the crew host 2026-09-04:
the session dbus-daemon was OOM collateral at 17:30:59 and per-pane containment
was gone from 17:31 with nothing reporting it.

These tests are pure environment transforms — no bus, no tmux. A test that
reached the real session bus would pass or fail on the state of the machine it
ran on, which is precisely the wrong property for the regression guarding an
environment bug.
"""
from shantytown import panemem

ADDR = "unix:path=/run/user/1000/bus"
GUID = "guid=c235ff42a2a28944e9d536246a98d203"


def _env(monkeypatch, value):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", value)
    return panemem.launch_env()["DBUS_SESSION_BUS_ADDRESS"]


def test_a_guid_is_stripped(monkeypatch):
    assert _env(monkeypatch, f"{ADDR},{GUID}") == ADDR


def test_the_socket_path_survives_untouched(monkeypatch):
    """The path is the address. Stripping must never disturb it — an env that
    no longer names the socket is a worse failure than the one being fixed."""
    assert "path=/run/user/1000/bus" in _env(monkeypatch, f"{ADDR},{GUID}")


def test_other_parameters_are_kept(monkeypatch):
    out = _env(monkeypatch, f"unix:path=/run/user/1000/bus,{GUID},nonce=abc")
    assert out == "unix:path=/run/user/1000/bus,nonce=abc"


def test_a_guidless_address_is_returned_byte_identical(monkeypatch):
    assert _env(monkeypatch, ADDR) == ADDR


def test_an_address_that_is_ONLY_a_guid_is_left_alone(monkeypatch):
    """Nothing is left to connect with, so emptying the variable would turn a
    broken address into a missing one — a different failure, not a fix."""
    assert _env(monkeypatch, GUID) == GUID


def test_an_unset_address_is_not_invented(monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    assert "DBUS_SESSION_BUS_ADDRESS" not in panemem.launch_env()


def test_the_process_environment_is_not_mutated(monkeypatch):
    """launch_env returns a COPY. Editing os.environ here would change the bus
    address for st itself and for everything else it later spawns, which is a
    much larger claim than "this launch should reach the bus"."""
    import os
    stale = f"{ADDR},{GUID}"
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", stale)
    panemem.launch_env()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == stale


def test_the_rest_of_the_environment_is_carried_through(monkeypatch):
    """A launch env that drops PATH or HOME would break the pane far more
    thoroughly than a stale GUID does."""
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"{ADDR},{GUID}")
    monkeypatch.setenv("SHANTY_TEST_CANARY", "kept")
    env = panemem.launch_env()
    assert env["SHANTY_TEST_CANARY"] == "kept"
    assert "PATH" in env
