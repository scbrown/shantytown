"""Discriminating create controls: none of the refusal arms may post a PR."""
from copy import deepcopy
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

from shantytown import pr_preflight as p


REPO = "example/project"
HEAD = "a" * 40
BASE = "b" * 40
OTHER = "c" * 40


def pull(number=1, files=("src/shared.py",), bead="project-abc", head=OTHER):
    return {"number": number, "state": "open", "title": "Existing work",
            "body": f"Bead: {bead}", "draft": False, "changed_files": len(files),
            "head": {"sha": head, "ref": f"other-{number}", "repo": {"full_name": REPO}},
            "base": {"sha": BASE, "ref": "main"},
            "file_rows": [{"filename": f} for f in files]}


class Forge:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.writes = []
        self.list_calls = 0
        self.race = None
        self.lost_response = False

    def __call__(self, path, body=None):
        if body is not None:
            self.writes.append(body)
            if self.lost_response:
                raise p.Refused("lost response")
            return {"number": 99, "html_url": "https://github.com/example/project/pull/99",
                    "head": {"sha": HEAD}, "base": {"sha": BASE}}
        parsed = urlsplit(path)
        tail = parsed.path.removeprefix(f"repos/{REPO}/")
        query = parse_qs(parsed.query)
        if tail.startswith("commits/"):
            return {"sha": HEAD if tail == "commits/feature" else BASE}
        if tail == "pulls":
            self.list_calls += 1
            if self.list_calls == 2 and self.race:
                self.race(self)
            page = int(query["page"][0])
            return deepcopy(self.rows[(page-1)*100:page*100])
        number = int(tail.split("/")[1])
        row = next(r for r in self.rows if r["number"] == number)
        if tail.endswith("/files"):
            page = int(query["page"][0])
            return deepcopy(row["file_rows"][(page-1)*100:page*100])
        return deepcopy(row)


@pytest.fixture
def candidate(monkeypatch):
    value = {"head": HEAD, "head_ref": "feature", "base": BASE,
             "base_ref": "main", "files": ["src/shared.py"]}
    monkeypatch.setattr(p, "candidate", lambda *a: deepcopy(value))
    return value


def create(forge, decisions=(), **kw):
    return p.run("unused", REPO, "main", "project-new", "Concrete change",
                 "New work", list(decisions), create=True, api=forge, **kw)


def test_read_surfaces_file_and_family_overlap_and_disjoint(candidate):
    forge = Forge([pull(1), pull(2, ("other.py",), "project-new.3"),
                   pull(3, ("other.py",), "project-unrelated")])
    result = p.run("unused", REPO, "main", "project-new.2", "", "", [], api=forge)
    assert [(r["number"], r["files"], r["families"]) for r in result["overlaps"]] == [
        (1, ["src/shared.py"], []), (2, [], ["project-new"])]
    assert result["open_count"] == 3
    assert not forge.writes


def test_disjoint_positive_creates_once_and_records_primary(candidate):
    forge = Forge([pull(files=("other.py",))])
    result = create(forge)
    assert result["number"] == 99
    assert len(forge.writes) == 1
    assert "Bead: project-new" in forge.writes[0]["body"]
    assert "Overlap preflight:" in forge.writes[0]["body"]


def test_overlap_independent_exact_head_and_reason_are_recorded(candidate):
    forge = Forge([pull()])
    d = {"number": 1, "head": OTHER, "action": "independent",
         "reason": "Different behavior in the shared module; integration covered."}
    create(forge, [d])
    assert f"PR #1 @ {OTHER}: independent" in forge.writes[0]["body"]
    assert d["reason"] in forge.writes[0]["body"]


@pytest.mark.parametrize("decision", [None,
    {"number": 1, "head": HEAD, "action": "independent", "reason": "Reason long enough"},
    {"number": 1, "head": OTHER, "action": "independent", "reason": "short"},
    {"number": 1, "head": OTHER, "action": "absorb", "reason": "Copies and closes prior work"},
    {"number": 1, "head": OTHER, "action": "stack", "reason": "Stacks on existing work"},
])
def test_unresolved_or_invalid_overlap_refuses_before_write(candidate, decision):
    forge = Forge([pull()])
    with pytest.raises(p.Refused):
        create(forge, [decision] if decision else [])
    assert not forge.writes


@pytest.mark.parametrize("change", [
    lambda f: f.rows[0]["head"].update(sha="d" * 40),
    lambda f: f.rows[0].update(body="Changed work-item binding"),
    lambda f: f.rows.append(pull(2)),
])
def test_inventory_race_refuses_before_write(candidate, change):
    forge = Forge([pull(files=("other.py",))]); forge.race = change
    with pytest.raises(p.Refused, match="changed"):
        create(forge)
    assert not forge.writes


def test_incomplete_files_refuses_before_write(candidate):
    row = pull(); row["changed_files"] = 2
    forge = Forge([row])
    with pytest.raises(p.Refused, match="incomplete"):
        create(forge)
    assert not forge.writes


def test_renamed_old_path_is_an_overlap(candidate):
    row = pull(files=("src/renamed.py",)); row["file_rows"][0]["previous_filename"] = "src/shared.py"
    forge = Forge([row])
    with pytest.raises(p.Refused, match="exact-head"):
        create(forge)
    assert not forge.writes


def test_complete_pagination_and_bound():
    rows = list(range(101)); calls = []
    def api(path):
        calls.append(path); page = int(parse_qs(urlsplit(path).query)["page"][0])
        return rows[(page-1)*100:page*100]
    assert p.pages(api, "pulls?state=open") == rows
    assert len(calls) == 2
    with pytest.raises(p.Refused, match="coverage bound"):
        p.pages(lambda _: list(range(100)), "files", maximum=200)


def test_pr_head_changes_during_file_read():
    forge = Forge([pull()]); original = forge.__call__; details = 0
    def api(path):
        nonlocal details
        value = original(path)
        if path.endswith("pulls/1"):
            details += 1
            if details == 2:
                value["head"]["sha"] = HEAD
        return value
    with pytest.raises(p.Refused, match="changed"):
        p.inventory(api, REPO)


def test_dry_run_exercises_gates_but_never_posts(candidate):
    forge = Forge([pull()])
    with pytest.raises(p.Refused):
        create(forge, dry_run=True)
    result = create(Forge(), dry_run=True)
    assert result["would_create"]
    assert "Bead: project-new" in result["body"]
    assert not forge.writes


def test_same_branch_already_open_refuses_even_disjoint(candidate):
    row = pull(files=("other.py",)); row["head"]["ref"] = "feature"
    forge = Forge([row])
    with pytest.raises(p.Refused, match="already has"):
        create(forge)
    assert not forge.writes


def test_lost_create_response_is_indeterminate_not_replayed(candidate):
    forge = Forge(); forge.lost_response = True
    with pytest.raises(p.Indeterminate, match="inspect remote"):
        create(forge)
    assert len(forge.writes) == 1


def test_candidate_real_git_and_unpushed_negative(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.org"); git("config", "user.name", "Test")
    (tmp_path / "file.py").write_text("base\n")
    git("add", "."); git("commit", "-qm", "base"); base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "feature"); (tmp_path / "file.py").write_text("change\n")
    git("commit", "-qam", "change"); head = git("rev-parse", "HEAD")
    git("remote", "add", "origin", "https://github.com/example/project.git")
    def api(path):
        return {"sha": head if path.endswith("feature") else base}
    result = p.candidate(tmp_path, REPO, "main", api)
    assert result["files"] == ["file.py"] and result["head"] == head
    with pytest.raises(p.Refused, match="exact candidate"):
        p.candidate(tmp_path, REPO, "main", lambda _: {"sha": base})


def test_cli_surface_parses_create_and_dry_run():
    from shantytown.cli import build_parser
    args = build_parser().parse_args(["repo", "pr", REPO, "--bead", "project-new",
                                     "--create", "--dry-run"])
    assert args.create and args.dry_run and args.repository == REPO


def test_stack_positive_proves_base_and_ancestry(candidate, monkeypatch):
    candidate['base'] = OTHER
    ancestry = []
    monkeypatch.setattr(p, 'git', lambda checkout, *args: ancestry.append(args) or '')
    d = {'number': 1, 'head': OTHER, 'action': 'stack',
         'reason': 'Builds the follow-on behavior directly on this reviewed branch.'}
    result = create(Forge([pull()]), [d], dry_run=True)
    assert result['would_create']
    assert ancestry == [('merge-base', '--is-ancestor', OTHER, HEAD)] * 2


def test_absorbed_closed_head_rechecked_before_create(candidate):
    row = pull(); row['state'] = 'closed'
    class ClosedForge(Forge):
        def __call__(self, path, body=None):
            if path.endswith('/pulls/1'):
                return deepcopy(row)
            return super().__call__(path, body)
    forge = ClosedForge()
    d = {'number': 1, 'head': OTHER, 'action': 'absorb',
         'reason': 'Superseded implementation is closed; copied behavior is tested.'}
    create(forge, [d])
    assert len(forge.writes) == 1
    assert 'absorb' in forge.writes[0]['body']


def test_command_real_dispatch_runs_gate_and_never_posts_without_create(candidate, monkeypatch, tmp_path, capsys):
    from shantytown.cli import main
    forge = Forge([pull()]); monkeypatch.setattr(p, 'github', forge)
    result = main(['--root', str(tmp_path), '--backend', 'files', 'repo', 'pr',
                   REPO, '--bead', 'project-new'])
    assert result == 0
    assert '"overlaps"' in capsys.readouterr().out
    assert not forge.writes


def test_missing_file_name_is_not_a_disjoint_result(candidate):
    row = pull(); row['file_rows'] = [{}]
    forge = Forge([row])
    with pytest.raises(p.Refused, match='missing changed-file'):
        create(forge)
    assert not forge.writes


def test_candidate_race_during_recheck_refuses(candidate, monkeypatch):
    calls = 0
    def changing(*args):
        nonlocal calls
        calls += 1
        value = deepcopy(candidate)
        if calls > 1:
            value['head'] = 'd' * 40
        return value
    monkeypatch.setattr(p, 'candidate', changing)
    forge = Forge()
    with pytest.raises(p.Refused, match='changed'):
        create(forge)
    assert not forge.writes


@pytest.mark.parametrize('mutate', [
    lambda row: row['file_rows'][0].update(filename=''),
    lambda row: row['head'].update(sha=''),
    lambda row: row.update(changed_files=True),
    lambda row: row.update(file_rows=[{'filename': 'src/shared.py'}] * 2, changed_files=2),
])
def test_malformed_metadata_never_becomes_a_disjoint_pass(candidate, mutate):
    row = pull(); mutate(row); forge = Forge([row])
    with pytest.raises(p.Refused):
        create(forge)
    assert not forge.writes


@pytest.mark.parametrize('response', [
    {'head': 'malformed'},
    {'head': ['malformed'], 'base': {'sha': BASE}},
    {'head': {'sha': HEAD}, 'base': 'malformed'},
    {'head': {'sha': HEAD}, 'base': 42},
    {'head': {'sha': HEAD}, 'base': {'sha': BASE}, 'html_url': 'missing-number'},
    {'head': {'sha': HEAD}, 'base': {'sha': BASE}, 'number': True, 'html_url': 'wrong-number'},
    None,
    [],
])
def test_cli_malformed_post_response_is_indeterminate_once(candidate, monkeypatch, tmp_path, capsys, response):
    from shantytown.cli import main
    forge = Forge()
    def api(path, body=None):
        if body is not None:
            forge.writes.append(body)
            return deepcopy(response)
        return forge(path)
    monkeypatch.setattr(p, 'github', api)
    body = tmp_path / 'body.md'; body.write_text('Concrete change')
    rc = main(['--root', str(tmp_path), '--backend', 'files', 'repo', 'pr',
               REPO, '--bead', 'project-new', '--create', '--title', 'New work',
               '--body-file', str(body)])
    captured = capsys.readouterr()
    import json
    output = json.loads(captured.out)
    assert rc == 2 and output['outcome'] == 'indeterminate' and output['created'] is None
    assert 'Traceback' not in captured.err
    assert len(forge.writes) == 1


def test_read_existing_candidate_lists_other_overlaps(candidate):
    own = pull(99); own['head']['ref'] = 'feature'; own['head']['sha'] = HEAD
    forge = Forge([own, pull(1)])
    result = p.run('unused', REPO, 'main', 'project-new', '', '', [], api=forge)
    assert result['open_count'] == 2
    assert result['excluded_candidate_prs'] == [99]
    assert [p['number'] for p in result['overlaps']] == [1]
    assert not forge.writes
