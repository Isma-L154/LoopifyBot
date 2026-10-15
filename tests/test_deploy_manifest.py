"""
deploy/manifest must cover every file under deploy/.

ilserver4-deploy refuses to deploy a commit with an unmapped file there, so a
new script that is not listed would stop every deployment of the bot rather
than be skipped. This catches it in the pull request instead.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def entries() -> list[tuple[str, str, list[str]]]:
    lines = (DEPLOY / "manifest").read_text(encoding="utf-8").splitlines()
    rows = [line.split("#", 1)[0].split() for line in lines]
    return [(row[0], row[1], row[2:]) for row in rows if row]


def test_every_file_under_deploy_is_mapped():
    mapped = [path for _, first, rest in entries() for path in [first, *rest]]
    for file in DEPLOY.rglob("*"):
        if not file.is_file():
            continue
        name = file.relative_to(ROOT).as_posix()
        assert any(name == p or (p.endswith("/") and name.startswith(p)) for p in mapped), \
            f"{name} is not in deploy/manifest"


def test_every_input_exists():
    """A misspelt input would never trigger a deployment."""
    for _kind, installer, inputs in entries():
        for path in [installer, *inputs]:
            assert (ROOT / path).exists(), f"{path} in deploy/manifest does not exist"
