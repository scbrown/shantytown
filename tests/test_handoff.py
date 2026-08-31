import json
from copy import deepcopy

import pytest

from shantytown.handoff import InvalidHandoff, canonical_bytes, transition, validate


IDENTITY = {
    "harness": "shantytown", "agent": "worker-1", "session": "session-1",
    "key_id": "sha256:key-1", "introducer": "st", "binding": "binding-1",
}


def queued():
    return {
        "version": "crew-handoff-v1", "id": "handoff-1",
        "origin": deepcopy(IDENTITY),
        "target": {"harness": "creel", "agent": "browser-worker"},
        "task": {"id": "task-1", "title": "Measure parity", "pointer": "tracker:task-1"},
        "ownership": {"lease_id": "lease-1", "owner": None},
        "state": "queued",
    }


def test_canonical_fixture_matches_browser_implementation():
    expected = b'{"id":"handoff-1","origin":{"agent":"worker-1","binding":"binding-1","harness":"shantytown","introducer":"st","key_id":"sha256:key-1","session":"session-1"},"ownership":{"lease_id":"lease-1","owner":null},"state":"queued","target":{"agent":"browser-worker","harness":"creel"},"task":{"id":"task-1","pointer":"tracker:task-1","title":"Measure parity"},"version":"crew-handoff-v1"}'
    assert canonical_bytes(queued()) == expected


def test_canonicalization_is_idempotent_order_independent_and_detached():
    original = queued()
    reordered = {key: original[key] for key in reversed(original)}
    first = validate(original)
    first["task"]["title"] = "mutated detached copy"
    assert original["task"]["title"] == "Measure parity"
    assert canonical_bytes(original) == canonical_bytes(reordered)
    assert canonical_bytes(json.loads(canonical_bytes(original))) == canonical_bytes(original)


def test_claim_success_preserves_attested_owner_and_requires_result_pointer():
    claimed = transition(queued(), "claimed", ownership={
        "lease_id": "lease-1", "owner": IDENTITY,
        "claimed_at": "2026-08-31T12:00:00Z", "expires_at": "2026-08-31T12:05:00Z",
    })
    done = transition(claimed, "succeeded", result={"pointer": "git:abc123", "summary": "landed"})
    assert done["ownership"]["owner"]["session"] == "session-1"
    assert done["result"]["pointer"] == "git:abc123"


@pytest.mark.parametrize("mutation, message", [
    (lambda e: e["origin"].pop("key_id"), "origin.key_id"),
    (lambda e: e["ownership"].update(owner=IDENTITY), "queued envelope"),
    (lambda e: (e["ownership"].update(owner=deepcopy(IDENTITY)), e.update(state="succeeded", result={"summary": "no pointer"})), "result.pointer"),
    (lambda e: (e["ownership"].update(owner=deepcopy(IDENTITY)), e.update(state="failed", failure={"code": "x", "message": "bad", "retryable": "yes"})), "retryable"),
])
def test_rejects_identity_and_terminal_state_ambiguity(mutation, message):
    envelope = queued()
    mutation(envelope)
    with pytest.raises(InvalidHandoff, match=message):
        validate(envelope)


def test_terminal_state_cannot_be_reopened():
    claimed = transition(queued(), "claimed", ownership={"lease_id": "lease-1", "owner": IDENTITY})
    failed = transition(claimed, "failed", failure={"code": "blocked", "message": "no access", "retryable": False})
    with pytest.raises(InvalidHandoff, match="illegal transition"):
        transition(failed, "queued")
