"""Complete GitHub overlap inventory and guarded PR creation (aegis-26m).

No merge or close authority lives here. Dispositions are explicit author decisions,
bound to the other PR's head; they are never inferred from a title or age.

# arming: agent-invoked -- st repo pr is the explicit foreground creation path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from .forgejo import parse_remote


class Refused(ValueError):
    pass


class Indeterminate(Refused):
    """A create may have committed; never suggest a blind retry."""


def github(path, body=None):
    argv = ["gh", "api", "--method", "GET" if body is None else "POST", path]
    if body is not None:
        argv += ["--input", "-"]
    result = subprocess.run(argv, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        # Errors may include URLs or request contents: retain category, not credentials.
        raise Refused("GitHub API failed; inventory/write outcome is unverified")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise Refused("GitHub returned invalid JSON") from error


def pages(api, path, *, maximum=3200):
    rows = []
    for page in range(1, maximum // 100 + 2):
        join = "&" if "?" in path else "?"
        batch = api(f"{path}{join}per_page=100&page={page}")
        if not isinstance(batch, list) or len(batch) > 100:
            raise Refused("invalid or incomplete paginated inventory")
        rows.extend(batch)
        if len(rows) >= maximum:
            raise Refused("inventory reached its coverage bound; cannot prove completeness")
        if len(batch) < 100:
            return rows
    raise Refused("inventory pagination did not terminate")


def inventory(api, repo):
    pulls = pages(api, f"repos/{repo}/pulls?state=open")
    result = []
    seen = set()
    for pull in pulls:
        if not isinstance(pull, dict):
            raise Refused("invalid PR inventory row")
        number = pull.get("number")
        if not isinstance(number, int) or number in seen:
            raise Refused("missing/duplicate PR in inventory")
        seen.add(number)
        path = f"repos/{repo}/pulls/{number}"
        detail = api(path)
        files = pages(api, path + "/files", maximum=3000)
        again = api(path)
        try:
            head = detail["head"]["sha"]
            if (not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head)
                    or not isinstance(detail["head"]["ref"], str) or not detail["head"]["ref"]
                    or type(detail["changed_files"]) is not int):
                raise Refused("invalid PR head/file metadata")
            if (detail["state"] != "open" or again["state"] != "open"
                    or head != again["head"]["sha"]
                    or detail["changed_files"] != len(files)
                    or again["changed_files"] != len(files)):
                raise Refused("PR changed or file inventory is incomplete")
            if any(not isinstance(row, dict) or not isinstance(row.get("filename"), str)
                   or not row["filename"]
                   for row in files):
                raise Refused("missing changed-file name")
            if len({row["filename"] for row in files}) != len(files):
                raise Refused("duplicate changed-file metadata")
            names = {name for row in files for name in
                     (row.get("filename"), row.get("previous_filename")) if name}
            if any(not isinstance(name, str) for name in names):
                raise Refused("invalid changed-file name")
            result.append({"number": number, "head": head,
                           "head_ref": detail["head"]["ref"],
                           "head_repo": (detail["head"].get("repo") or {}).get("full_name"),
                           "base": detail["base"]["sha"],
                           "base_ref": detail["base"]["ref"],
                           "body": detail.get("body") or "", "title": detail["title"],
                           "draft": bool(detail.get("draft")), "files": sorted(names)})
        except (KeyError, TypeError) as error:
            raise Refused("incomplete PR metadata") from error
    return sorted(result, key=lambda row: row["number"])


def git(checkout, *args):
    r = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True,
                       text=True, timeout=30)
    if r.returncode:
        raise Refused("git candidate could not be verified: " + args[0])
    return r.stdout if "-z" in args else r.stdout.strip()


def candidate(checkout, repo, base, api):
    parsed = parse_remote(git(checkout, "remote", "get-url", "origin"))
    if parsed != ("github.com", *repo.split("/")):
        raise Refused("candidate origin must be the explicitly named GitHub repository")
    head_ref = git(checkout, "symbolic-ref", "--short", "HEAD")
    git(checkout, "check-ref-format", "--branch", base)
    if base == head_ref:
        raise Refused("candidate cannot be the base branch")
    if git(checkout, "status", "--porcelain"):
        raise Refused("commit candidate changes before PR preflight")
    head = git(checkout, "rev-parse", "HEAD")
    remote_head = api(f"repos/{repo}/commits/{quote(head_ref, safe='')}").get("sha")
    base_sha = api(f"repos/{repo}/commits/{quote(base, safe='')}").get("sha")
    if head != remote_head or not isinstance(base_sha, str):
        raise Refused("push the exact candidate branch and fetch its base before preflight")
    git(checkout, "cat-file", "-e", base_sha + "^{commit}")
    files = git(checkout, "diff", "--no-renames", "--name-only", "-z", base_sha + "..." + head)
    names = sorted(name for name in files.split("\0") if name)
    if not names:
        raise Refused("candidate has no changed files")
    return {"head": head, "head_ref": head_ref, "base": base_sha,
            "base_ref": base, "files": names}


BEAD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*$")
MENTION = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*\b")


def family(bead):
    return bead.split(".", 1)[0]


def report(candidate, pulls, bead, bead_families=()):
    if not BEAD.fullmatch(bead) or any(not BEAD.fullmatch(b) for b in bead_families):
        raise Refused("bead/family must be a concrete work-item ID")
    families = {family(b) for b in (bead, *bead_families)}
    matches = []
    for pull in pulls:
        same_files = sorted(set(candidate["files"]) & set(pull["files"]))
        mentions = MENTION.findall(" ".join((pull["body"], pull["title"], pull["head_ref"])))
        same_family = sorted(families & {family(b) for b in mentions})
        if same_files or same_family:
            matches.append({"number": pull["number"], "head": pull["head"],
                            "title": pull["title"], "draft": pull["draft"],
                            "files": same_files, "families": same_family})
    return {"candidate": candidate, "bead": bead, "open_count": len(pulls),
            "overlaps": matches,
            "inventory_digest": hashlib.sha256(json.dumps(pulls, sort_keys=True).encode()).hexdigest()}


def dispositions(checkout, repo, candidate, review, decisions, api):
    if not isinstance(decisions, list):
        raise Refused("dispositions file must be a JSON list")
    by_number = {}
    for d in decisions:
        if not isinstance(d, dict) or not isinstance(d.get("number"), int):
            raise Refused("disposition needs a PR number")
        if d["number"] in by_number:
            raise Refused("duplicate PR disposition")
        if not isinstance(d.get("reason"), str) or len(d["reason"].strip()) < 12:
            raise Refused("each disposition needs a concrete reason")
        if d.get("action") not in {"stack", "absorb", "independent"}:
            raise Refused("disposition action must be stack, absorb or independent")
        by_number[d["number"]] = d
    for overlap in review["overlaps"]:
        d = by_number.get(overlap["number"])
        if not d or d.get("head") != overlap["head"]:
            raise Refused(f"PR #{overlap['number']} needs an exact-head disposition")
        if d["action"] == "absorb":
            raise Refused("absorb requires closing the referenced PR first; this command never closes others")
        if d["action"] == "stack":
            if candidate["base"] != d["head"]:
                raise Refused("stack requires the overlapping PR's exact head as candidate base")
            git(checkout, "merge-base", "--is-ancestor", d["head"], candidate["head"])
    matched = {o["number"] for o in review["overlaps"]}
    for number, d in by_number.items():
        if number in matched:
            continue
        old = api(f"repos/{repo}/pulls/{number}")
        if (d["action"] != "absorb" or old.get("state") != "closed"
                or (old.get("head") or {}).get("sha") != d.get("head")):
            raise Refused("extra disposition must identify an exact-head closed absorbed PR")
    return sorted(by_number.values(), key=lambda d: d["number"])


def run(checkout, repo, base, bead, body, title, decisions, *, bead_families=(),
        create=False, draft=False, dry_run=False, api=None):
    api = api or github
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise Refused("repository must be owner/name")
    first_candidate = candidate(checkout, repo, base, api)
    pulls = inventory(api, repo)
    existing = [p for p in pulls if p["head_repo"] == repo
                and p["head_ref"] == first_candidate["head_ref"]]
    if existing and create:
        raise Refused("candidate branch already has an open PR; update it rather than create another")
    others = [p for p in pulls if p not in existing]
    review = report(first_candidate, others, bead, bead_families)
    # Read mode surfaces all candidates even when no dispositions exist yet.
    if not create:
        return {**review, "open_count": len(pulls),
                "excluded_candidate_prs": [p["number"] for p in existing]}
    if not title.strip() or not body.strip():
        raise Refused("creation needs title and body")
    chosen = dispositions(checkout, repo, first_candidate, review, decisions, api)
    final_candidate = candidate(checkout, repo, base, api)
    final_pulls = inventory(api, repo)
    final_review = report(final_candidate, final_pulls, bead, bead_families)
    if review != final_review:
        raise Refused("candidate/open PR inventory changed; rerun preflight and review dispositions")
    dispositions(checkout, repo, final_candidate, final_review, chosen, api)
    primary = re.findall(r"^Bead:\s*(\S+)\s*$", body, re.M)
    if primary and primary != [bead]:
        raise Refused("body must identify exactly the requested primary bead")
    appendix = ("" if primary else "\n\nBead: " + bead)
    appendix += "\n\nOverlap preflight: " + review["inventory_digest"]
    for d in chosen:
        appendix += f"\n- PR #{d['number']} @ {d['head']}: {d['action']} — {d['reason']}"
    # GitHub lacks an atomic expected-head create API. Reconcile the result; do
    # not turn a lost response or raced head into a blind retry/new duplicate.
    if dry_run:
        return {**review, "would_create": True, "body": body + appendix}
    try:
        result = api(f"repos/{repo}/pulls", {"title": title, "body": body + appendix,
                                            "head": first_candidate["head_ref"],
                                            "base": base, "draft": draft})
    except (Refused, OSError, subprocess.SubprocessError) as error:
        raise Indeterminate("create outcome unknown; inspect remote PR before retrying") from error
    # Every field is still untrusted JSON after a successful POST. A non-object
    # head/base or missing number must not escape as AttributeError/KeyError and
    # lose the fact that the write may have committed (Wu's post-write control).
    valid = (isinstance(result, dict) and isinstance(result.get("head"), dict)
             and isinstance(result.get("base"), dict)
             and result["head"].get("sha") == first_candidate["head"]
             and result["base"].get("sha") == first_candidate["base"]
             and type(result.get("number")) is int and result["number"] > 0
             and result.get("html_url") == f"https://github.com/{repo}/pull/{result['number']}")
    if not valid:
        raise Indeterminate("create outcome/head/base unverified; inspect remote PR before retrying")
    return {**review, "created": result["html_url"], "number": result["number"]}


def command(args):
    try:
        body = args.body_file.read_text() if args.body_file else ""
        decisions = json.loads(args.dispositions_file.read_text()) if args.dispositions_file else []
        result = run(args.checkout, args.repository, args.base, args.bead, body,
                     args.title or "", decisions, bead_families=args.bead_family,
                     create=args.create, draft=args.draft, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    except Indeterminate as error:
        print(json.dumps({"refused": str(error), "created": None, "outcome": "indeterminate"}))
        return 2
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"refused": str(error), "created": False}))
        return 2
