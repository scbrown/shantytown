"""Run the actual SessionStart directive on base and candidate; no graph requests."""
import subprocess
import sys

BASE = "ce995db60403bb1ccfd084a15cf2133f69b04ebc"
HEAD = "2ccaf32"
for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
    source = subprocess.run(["git", "show", revision + ":shantytown/query_first.py"],
                            check=True, capture_output=True, text=True).stdout
    result = subprocess.run([sys.executable, "-c", source], check=True,
                            capture_output=True, text=True)
    print(label, revision, flush=True)
    print(result.stdout, flush=True)
    assert ("X-Quipu-Purpose: CONTEXT" in result.stdout) == (label == "HEAD")
print("Same SessionStart trigger: omitted purpose -> explicit CONTEXT; zero graph reads.", flush=True)
