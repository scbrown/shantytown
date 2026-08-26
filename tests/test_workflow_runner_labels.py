from pathlib import Path


def test_forgejo_workflows_target_the_provisioned_runner_label():
    """A GitHub-only label queues forever on the self-hosted Forgejo runner."""
    root = Path(__file__).parents[1]
    for relative in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        workflow = (root / relative).read_text()
        assert "runs-on: ubuntu-22.04" in workflow, relative
        assert "runs-on: ubuntu-latest" not in workflow, relative
