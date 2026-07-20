"""THE RATCHET. No internal identifier gets back into this repo.

This repo is public. We hand-scrubbed ~200 internal references, and ~15 came
back within HOURS — one into shipped output. Then, on the night the scrub rule
was written, six more arrived in `docs/cli.md` from someone documenting real
tool output faithfully. Nobody was careless. Nothing told them.

A SCRUB IS A STATE AND STATES ROT. The deliverable was never "129 fewer
strings" — 129 fewer strings decays back to 130. The deliverable is this file:
the thing that fails the build on number 130.

WHY THIS EXISTS AS WELL AS test_no_internal_ids_in_output.py: that one walks
argparse help and print() literals, which is a narrow reading of "user-facing".
It MISSED a live leak — `roles.check` BUILT a note containing an internal ticket
id and cli.py printed it somewhere else entirely, so no print() literal ever
contained it, and a test asserted on the id to hold it in place. A guard that
only inspects the call it expects will keep missing the indirection. This one
inspects every string literal in the package instead.

WHAT IT ALLOWS, DELIBERATELY: comments and docstrings. A citation next to the
code is how the reasoning stays findable, and it leaks no topology. A string
LITERAL is different: it is a value the program can emit, log, write to a file
or hand to a user. That is the line, and it is the same line the graph rule
draws with its per-pattern tiers.

SCOPE: this is an offline subset of POLICY RULE #1, which lives in the knowledge
graph and is authoritative. It is duplicated here on purpose and only in part,
because a public repo's test suite must name no internal service and must pass
with no network. If you want to change WHAT is forbidden, change the graph rule,
not this file.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "shantytown"

# Generic by construction: none of these name a real host, network or person, so
# this file does not reintroduce the thing it forbids.
FORBIDDEN = {
    "internal hostname": re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:lan|svc)\b"),
    "private address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    # Any /home/<account>/ except the conventional placeholders. Matching those
    # too would flag the documented fix itself.
    "operator home path": re.compile(
        r"/home/(?!(?:user|you|alice|bob|someone|example|x)/)[a-z][a-z0-9_-]*/"),
    "internal ticket id": re.compile(r"\b(?:aegis|hq|gassy|qp)-[a-z0-9]{3,6}\b"),
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Constants that ARE docstrings — allowed, like comments."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def live_string_literals(source: str):
    """Every string literal that is NOT a docstring. Comments never appear in
    the AST at all, so they are allowed for free."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in skip:
            yield node.lineno, node.value


# Block-tier classes: forbidden in EVERY tracked file, not just python. Six of
# the twelve leaks this ratchet was written for arrived in docs/cli.md, so a
# guard that only reads .py would not have caught the very regression that
# motivated it. Ticket ids are excluded here on purpose — they are warn-tier in
# the graph rule (a bead reference leaks no topology), they appear legitimately
# as fixture data in tests, and forbidding them everywhere would produce an
# enormous diff and get this file deleted.
BLOCK_TIER = {k: v for k, v in FORBIDDEN.items() if k != "internal ticket id"}

# Generated/vendored things nobody hand-edits.
GUARD_FILES = {"test_internal_identifier_ratchet.py",
               "test_no_internal_ids_in_output.py",
               "pre-push-scrub-guard.sh"}
SKIP_DIR = ("/.git/", "/node_modules/", "/target/", "/dist/")
SKIP_SUFFIX = {".png", ".jpg", ".svg", ".ico", ".pdf", ".zip", ".gz", ".lock",
               ".bin", ".wasm"}


def tracked_text_files():
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                       capture_output=True, text=True)
    for rel in r.stdout.splitlines():
        f = ROOT / rel
        if f.suffix.lower() in SKIP_SUFFIX or any(d in f"/{rel}" for d in SKIP_DIR):
            continue
        try:
            yield rel, f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def test_no_internal_hostnames_addresses_or_paths_anywhere():
    """The one that would have caught tonight's regression: prose in docs."""
    offenders = []
    for rel, text in tracked_text_files():
        # A guard's positive control MUST contain the thing it forbids, or it
        # is a check nobody has seen catch anything. Same exclusion the policy
        # graph carries for these files.
        if Path(rel).name in GUARD_FILES:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for label, rx in BLOCK_TIER.items():
                m = rx.search(line)
                if m:
                    offenders.append(f"{rel}:{i}: {label} {m.group(0)!r}")
    assert not offenders, (
        f"{len(offenders)} internal identifier(s) in a PUBLIC repo:\n  "
        + "\n  ".join(offenders[:40])
        + ("\n  ..." if len(offenders) > 40 else "")
        + "\n\nUse a neutral example (RFC 2606 reserves .invalid/.example for "
          "exactly this) or a placeholder. These are hostnames, private "
          "addresses and home paths — they map the private estate."
    )


def test_no_internal_identifiers_in_any_live_string():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        for lineno, text in live_string_literals(path.read_text(encoding="utf-8")):
            for label, rx in FORBIDDEN.items():
                m = rx.search(text)
                if m:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {label} "
                        f"{m.group(0)!r} in {text[:60]!r}")
    assert not offenders, (
        "internal identifier(s) in a live string literal — this repo is "
        "PUBLIC:\n  " + "\n  ".join(offenders) +
        "\n\nA citation belongs in a COMMENT beside the code, where it stays "
        "findable and leaks nothing. A string literal is a value the program "
        "can emit."
    )


def test_the_ratchet_catches_each_class():
    """Positive control, one per pattern. A ratchet that has never been seen
    catching anything is a function that returns an empty list — and it would
    look exactly like a clean repo. Each class is planted and must be caught."""
    # Every planted value is SYNTHETIC. db.lan, thing.svc, jsmith and aegis-1234
    # already were; the address was not — it was a real host on the real subnet,
    # which leaked the /24 in the one file guaranteed to be read by anyone
    # studying how we prevent leaks. RFC 5737's documentation ranges are not
    # RFC1918 and so would not match this pattern, hence the canonical textbook
    # private address instead: it exercises the class and names no real host.
    planted = (
        'x = "connect to db.lan now"\n'
        'y = "http://thing.svc/mcp"\n'
        'z = "addr 192.168.0.1"\n'
        'w = "/home/jsmith/src/x"\n'
        'v = "see aegis-1234"\n'
    )
    found = set()
    for _, text in live_string_literals(planted):
        for label, rx in FORBIDDEN.items():
            if rx.search(text):
                found.add(label)
    assert found == set(FORBIDDEN), (
        f"the ratchet missed a planted class: {set(FORBIDDEN) - found}")


def test_docstrings_and_comments_are_deliberately_allowed():
    """The negative control, and it is not an oversight — it is the tiering
    argument. If this ever starts failing, someone has widened the rule to
    forbid citing your reasoning, and the diff will be enormous and pointless."""
    src = '"""Module doc mentioning aegis-1234."""\n# comment about db.lan\nx = 1\n'
    hits = [t for _, t in live_string_literals(src)
            if any(rx.search(t) for rx in FORBIDDEN.values())]
    assert not hits, f"docstring/comment wrongly flagged: {hits}"
