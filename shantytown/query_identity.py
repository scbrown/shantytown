"""Caller-declared read context, separate from components and write actors."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
import socket

_caller = ContextVar("query_caller", default=None)
_source = ContextVar("query_source", default="shantytown")
_operation = ContextVar("query_operation", default="query")


def current_query_identity():
    return _caller.get()


def _value(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= 128 and all(32 <= ord(c) <= 126 for c in value) else None


def _key(value):
    value = _value(value)
    return value.lower() if value and len(value) <= 64 and re.fullmatch(r"[A-Za-z0-9._-]+", value) else None


def _host():
    # A malformed explicit declaration remains UNKNOWN rather than silently
    # becoming a different host. This is attribution, never authentication.
    if "QUIPU_HOST" in os.environ:
        return _key(os.environ["QUIPU_HOST"])
    try:
        return _key(socket.gethostname())
    except OSError:
        return None


def read_context():
    """Resolve known declarations without guessing identity from an owner/actor."""
    env = os.environ
    in_session = env.get("CLAUDECODE") == "1" or bool(env.get("CODEX_HOME"))
    caller = _value(next((v for v in (
        env.get("QUIPU_AGENT"), current_query_identity(), env.get("SEEDS_AGENT_NAME"),
        env.get("SHANTY_AGENT") if in_session else None,
    ) if v), None))
    session = _value(env.get("QUIPU_SESSION")) or _value(env.get("SEEDS_SESSION"))
    if not session:
        session = (_value(env.get("CODEX_SESSION_ID")) or _value(env.get("CODEX_THREAD_ID"))
                   if env.get("CODEX_HOME") and env.get("CLAUDECODE") != "1"
                   else _value(env.get("CLAUDE_CODE_SESSION_ID")) if in_session else None)
    # Invalid explicit task is omitted, never transformed into another identity.
    task = _key(env.get("QUIPU_TASK") if env.get("QUIPU_TASK") else env.get("SEEDS_TASK"))
    return {k: v for k, v in {
        "QUIPU_AGENT": caller, "QUIPU_SESSION": session, "QUIPU_TASK": task,
        "QUIPU_HOST": _host(),
        "QUIPU_SOURCE": _key(env.get("QUIPU_SOURCE")) or _source.get(),
        "QUIPU_OPERATION": _key(env.get("QUIPU_OPERATION")) or _operation.get(),
    }.items() if v is not None}


def read_headers():
    return {"X-Quipu-" + key.removeprefix("QUIPU_").title(): value
            for key, value in read_context().items()}


def sdk_subprocess_options(*, read_operation=None):
    """Scope read metadata to the SDK child; retain legacy write provenance."""
    if read_operation is not None:
        with declared_query_identity(current_query_identity(), operation=read_operation):
            return {"env": dict(os.environ, **read_context())}
    name = current_query_identity() or (_source.get() if _source.get() == "st-tend" else None)
    return {"env": dict(os.environ, QUIPU_AGENT=name)} if name else {}


@contextmanager
def declared_query_identity(name, *, source=None, operation=None):
    tokens = [(_caller, _caller.set(name))]
    for variable, value in ((_source, source), (_operation, operation)):
        if value is not None:
            tokens.append((variable, variable.set(_key(value) or "unknown")))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


@contextmanager
def declared_read_operation(operation):
    """Name a finite reader operation without replacing its caller or source."""
    token = _operation.set(_key(operation) or "unknown")
    try:
        yield
    finally:
        _operation.reset(token)
