"""Private bare-remote CLI demonstration; no network or installed-source edits."""
import contextlib, io, os, pathlib, subprocess, tempfile
from shantytown.cli import main

def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL, text=True).strip()

with tempfile.TemporaryDirectory() as scratch:
    root = pathlib.Path(scratch)
    source = root / "gt" / "project" / "crew" / "worker"
    source.mkdir(parents=True)
    remote = root / "origin.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(remote))
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "file").write_text("before\n")
    git(source, "add", "file"); git(source, "commit", "-qm", "base")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "config", "remote.origin.st-push-allowed", "true")
    git(source, "push", "-qu", "origin", "main")
    os.environ["GT_ROOT"] = str(root / "gt")
    os.environ["WORKSPACE_ROOT"] = str(root / "workspace")
    log = io.StringIO()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        assert main(["repo", "worktree", str(source), "worker"]) == 0
    tree = source.parent / "worker-wt" / "worker"
    git(tree, "switch", "-qc", "worker/topic")
    (tree / "file").write_text("after\n")
    git(tree, "add", "file"); git(tree, "commit", "-qm", "candidate")
    target = git(tree, "rev-parse", "HEAD")
    before = git(remote, "rev-parse", "main")
    os.chdir(tree)
    print("CALL: st repo push project worker --source-branch worker/topic --branch main")
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        code = main(["repo", "push", "project", "worker", "--source-branch",
                     "worker/topic", "--branch", "main"])
    print("EXIT:", code)
    print("TARGET PUBLISHED:", git(remote, "rev-parse", "main") == target)
    print("REMOTE UNCHANGED:", git(remote, "rev-parse", "main") == before)
    for line in log.getvalue().splitlines():
        if "refused:" in line or "pushed " in line:
            print(line.strip().replace(scratch, "<fixture>"))
    os.chdir(root.parent)
