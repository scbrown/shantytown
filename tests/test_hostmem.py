"""hostmem — the physical brake beside the usage brake (aegis-do672).

What these hold is mostly about DIRECTION. A memory brake has two ways to be
wrong and they are not symmetric: refusing when there is room idles the fleet on
our own bug, and admitting when there is none hands the kernel a victim. So the
tests that matter are the ones about which way each uncertainty falls.
"""

from __future__ import annotations

import pytest

from shantytown import hostmem


GIB = 1024 ** 3


def meminfo(tmp_path, available_gib, total_gib=61.6, swap_free_gib=0.3, swap_total_gib=8.0):
    """A real-shaped /proc/meminfo. kB units, because that is what the kernel writes
    and a parser tested only against bytes would be off by 1024 in production."""
    path = tmp_path / "meminfo"
    path.write_text(
        f"MemTotal:       {int(total_gib * GIB / 1024)} kB\n"
        f"MemFree:        {int(0.2 * GIB / 1024)} kB\n"
        f"MemAvailable:   {int(available_gib * GIB / 1024)} kB\n"
        f"Buffers:        {int(0.1 * GIB / 1024)} kB\n"
        f"SwapTotal:      {int(swap_total_gib * GIB / 1024)} kB\n"
        f"SwapFree:       {int(swap_free_gib * GIB / 1024)} kB\n"
    )
    return path


def test_the_measured_incident_state_refuses(tmp_path):
    """5 GiB left on the 61 GiB crew host: the next cargo build is the OOM."""
    got = hostmem.read(meminfo(tmp_path, available_gib=5.0))
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0), got)
    assert not verdict.admits
    assert "5.0 GiB available" in verdict.refusal
    assert "global OOM" in verdict.refusal


def test_the_live_state_warns_but_admits(tmp_path):
    """17.4 GiB — the real reading when this was written. Between one build and
    two, so the honest answer is 'yes, and this is the last one'. A brake that
    refused here would refuse on a host that can still do the work."""
    got = hostmem.read(meminfo(tmp_path, available_gib=17.4))
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0), got)
    assert verdict.admits
    assert "hostmem LOW" in verdict.warning
    assert verdict.refusal == ""


def test_a_healthy_host_says_nothing_at_all(tmp_path):
    """42.4 GiB is this host's measured 7-day AVERAGE. A brake that warns at its
    resting state is a brake nobody reads."""
    got = hostmem.read(meminfo(tmp_path, available_gib=42.4))
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0), got)
    assert verdict.admits
    assert verdict.warning == ""
    assert verdict.alarm == ""


def test_a_small_host_is_not_gated(tmp_path):
    """0.2 GiB free on a 1 GiB container is trivially under a 12 GiB floor and must
    NOT refuse: an absolute build-sized floor is meaningless there. Same gate the
    Prometheus rules use, so the alert and the brake agree about 'full'."""
    got = hostmem.read(meminfo(tmp_path, available_gib=0.2, total_gib=1.0))
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0), got)
    assert verdict.admits
    assert verdict.refusal == ""


def test_an_unreadable_host_admits_and_alarms(tmp_path):
    """OPEN, LOUDLY. governor.py's standing invariant is that no probe bug may stop
    the whole crew, and the two errors are not symmetric: a broken probe that
    refuses is an outage we caused."""
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0),
                            hostmem.read(tmp_path / "does-not-exist"))
    assert verdict.admits
    assert "SIGNAL LOST" in verdict.alarm
    assert verdict.refusal == ""


def test_a_meminfo_without_memavailable_is_an_error_not_a_guess(tmp_path):
    """MemFree is routinely near zero on a healthy box with a warm page cache, so
    deriving MemAvailable would manufacture a refusal out of a normal state."""
    path = tmp_path / "meminfo"
    path.write_text("MemTotal:  64000000 kB\nMemFree:   180000 kB\n")
    got = hostmem.read(path)
    assert not got.readable
    assert "MemAvailable" in got.error


def test_kb_units_are_honoured(tmp_path):
    """Off by 1024 here is a floor that never fires or always does."""
    got = hostmem.read(meminfo(tmp_path, available_gib=12.0, total_gib=61.6))
    assert got.available_gib == pytest.approx(12.0, abs=0.01)
    assert got.total_gib == pytest.approx(61.6, abs=0.01)


def test_off_by_default(tmp_path):
    """No floor declared is OFF — declaring one is the enabling act, matching
    governor.Policy and session_budget.Limits."""
    got = hostmem.read(meminfo(tmp_path, available_gib=0.5))
    verdict = hostmem.check(hostmem.Limits(), got)
    assert verdict.admits
    assert verdict.warning == "" and verdict.alarm == ""


def test_warn_defaults_to_two_builds():
    assert hostmem.Limits(floor_gib=12.0).warn_at == 24.0
    assert hostmem.Limits(floor_gib=12.0, warn_gib=30.0).warn_at == 30.0


def test_swap_is_reported_and_never_gated_on(tmp_path):
    """Swap-free averages 18.7% on this host with excursions to 0%, so gating on it
    would refuse most days. It explains the pressure; it does not decide."""
    got = hostmem.read(meminfo(tmp_path, available_gib=42.4, swap_free_gib=0.0))
    verdict = hostmem.check(hostmem.Limits(floor_gib=12.0), got)
    assert verdict.admits and verdict.warning == ""
    warned = hostmem.check(hostmem.Limits(floor_gib=12.0),
                           hostmem.read(meminfo(tmp_path, available_gib=17.0,
                                                swap_free_gib=0.0)))
    assert "swap 0% free" in warned.warning


def test_parse_rejects_a_warn_below_the_floor():
    """The floor refuses first, so such a warning could never fire — dead config
    that reads as live."""
    with pytest.raises(hostmem.HostMemError, match="below floor_gib"):
        hostmem.parse({"floor_gib": 12.0, "warn_gib": 8.0})


def test_parse_rejects_a_warning_with_no_floor():
    with pytest.raises(hostmem.HostMemError, match="warn_gib is set but floor_gib"):
        hostmem.parse({"warn_gib": 24.0})


def test_parse_rejects_unknown_keys_and_names_the_known_ones():
    with pytest.raises(hostmem.HostMemError, match="unknown key"):
        hostmem.parse({"floor_gib": 12.0, "flor_gib": 24.0})


def test_parse_rejects_a_bool_as_a_number():
    """`floor_gib = true` is an int in Python and would become a 1 GiB floor."""
    with pytest.raises(hostmem.HostMemError, match="must be a number"):
        hostmem.parse({"floor_gib": True})


def test_parse_accepts_an_empty_table():
    assert hostmem.parse({}).active is False


def test_env_override_disables_with_zero(monkeypatch):
    monkeypatch.setenv("SHANTY_HOSTMEM_FLOOR_GIB", "0")
    assert hostmem.env_override().active is False


def test_env_override_sets_a_floor(monkeypatch):
    monkeypatch.setenv("SHANTY_HOSTMEM_FLOOR_GIB", "4")
    assert hostmem.env_override().floor_gib == 4.0


def test_env_override_absent_is_none(monkeypatch):
    monkeypatch.delenv("SHANTY_HOSTMEM_FLOOR_GIB", raising=False)
    assert hostmem.env_override() is None


def test_the_floor_and_the_prometheus_rules_agree():
    """Two enforcement points that disagree about 'full' are worse than either
    alone. If BUILD_GIB or MIN_TOTAL_GIB move here, the alert thresholds in
    goldblum's agent-host-memory-alerts.yml must move in the same commit."""
    assert hostmem.BUILD_GIB == 12.0
    assert hostmem.MIN_TOTAL_GIB == 24.0


# --- the WIRING, not the module ---------------------------------------------
# hostmem.py being correct proves nothing about st consulting it. These cover the
# seam, because a brake that is never called is the "shipped correct and INERT"
# failure the governor and session_budget both hit.

def test_the_cli_helper_is_off_when_nothing_declares_a_floor(monkeypatch):
    from shantytown import cli

    monkeypatch.delenv("SHANTY_HOSTMEM_FLOOR_GIB", raising=False)

    class Cfg:
        hostmem = hostmem.Limits()

    assert cli._hostmem_verdict(Cfg()) is None


def test_the_cli_helper_reads_the_config_table(monkeypatch, tmp_path):
    from shantytown import cli

    monkeypatch.delenv("SHANTY_HOSTMEM_FLOOR_GIB", raising=False)
    monkeypatch.setattr(hostmem, "MEMINFO", meminfo(tmp_path, available_gib=5.0))

    class Cfg:
        hostmem = hostmem.Limits(floor_gib=12.0)

    verdict = cli._hostmem_verdict(Cfg())
    assert verdict is not None and not verdict.admits


def test_the_env_override_beats_the_config_table(monkeypatch, tmp_path):
    """An operator disabling the brake for one run must not have to edit the
    deployment table, because the edit is what stays."""
    from shantytown import cli

    monkeypatch.setenv("SHANTY_HOSTMEM_FLOOR_GIB", "0")
    monkeypatch.setattr(hostmem, "MEMINFO", meminfo(tmp_path, available_gib=0.5))

    class Cfg:
        hostmem = hostmem.Limits(floor_gib=12.0)

    assert cli._hostmem_verdict(Cfg()) is None


def test_config_parses_a_hostmem_table(tmp_path):
    """Through config.load, so the table is reachable from a real file and not only
    from hostmem.parse."""
    from shantytown import config

    path = tmp_path / "shantytown.toml"
    path.write_text("[hostmem]\nfloor_gib = 12.0\nwarn_gib = 24.0\n")
    cfg = config.load(tmp_path)
    assert cfg.hostmem.floor_gib == 12.0
    assert cfg.hostmem.warn_at == 24.0


def test_config_names_the_file_on_a_bad_hostmem_table(tmp_path):
    from shantytown import config

    path = tmp_path / "shantytown.toml"
    path.write_text("[hostmem]\nfloor_gib = 12.0\nwarn_gib = 4.0\n")
    with pytest.raises(config.ConfigError, match=str(path)):
        config.load(tmp_path)
