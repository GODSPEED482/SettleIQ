"""The signing key's custody, read from the workflow files (task 5.4; spec 9.7.4, AC-REL-06).

Only the `sign` job of attest.yml may name the protected `release-attestation` environment. It
holds no cloud role and checks out only the signer, the deploy and CI workflows never name the
environment, no image copies a key, and the signer runs no application code.
"""

import ast
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ENVIRONMENT = "release-attestation"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_only_the_sign_job_names_the_key_environment():
    jobs = workflow("attest.yml")["jobs"]
    holders = [name for name, job in jobs.items() if job.get("environment") == ENVIRONMENT]
    assert holders == ["sign"]
    for name, job in jobs.items():
        if name != "sign":
            assert "ATTESTATION_SIGNING_KEY" not in yaml.safe_dump(job)


def test_the_sign_job_has_no_cloud_role_and_checks_out_only_the_signer():
    sign = workflow("attest.yml")["jobs"]["sign"]
    assert sign["permissions"] == {"contents": "read"}
    [checkout] = [s for s in sign["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout["with"]["sparse-checkout"] == ".github/attest"
    assert checkout["with"]["persist-credentials"] is False
    assert not any("configure-aws-credentials" in s.get("uses", "") for s in sign["steps"])
    assert not any("docker" in s.get("run", "") for s in sign["steps"])


def test_issuance_runs_only_from_prod_by_hand():
    attest = workflow("attest.yml")
    # PyYAML reads the bare `on` key as True.
    assert set(attest[True]) == {"workflow_dispatch"}
    assert attest["permissions"] == {}
    assert attest["jobs"]["prepare"]["if"] == "github.ref == 'refs/heads/prod'"


def test_no_other_workflow_names_the_environment_or_the_key():
    for path in WORKFLOWS.glob("*.y*ml"):
        if path.name == "attest.yml":
            continue
        text = path.read_text(encoding="utf-8")
        assert ENVIRONMENT not in text, path.name
        assert "ATTESTATION_SIGNING_KEY" not in text, path.name


def test_no_dockerfile_or_compose_file_carries_a_key():
    for path in [*REPO.rglob("*Dockerfile*"), *REPO.rglob("*compose*.y*ml")]:
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "ATTESTATION_SIGNING_KEY" not in text, path
        assert ".github/attest" not in text or path.name == "Harness.Dockerfile", path


def test_the_signer_imports_no_application_code():
    tree = ast.parse((REPO / ".github" / "attest" / "sign.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"app", "packs", "settleiq_agent", "tests"}
    third_party = imported - {
        "argparse", "base64", "datetime", "hashlib", "json", "os", "sys", "typing", "uuid",
        "xml", "pathlib", "__future__",
    }
    assert third_party <= {"cryptography", "rfc8785", "yaml"}


def test_the_signers_pins_match_the_backend_lock():
    lock = (REPO / "backend" / "uv.lock").read_text(encoding="utf-8")
    pins = (REPO / ".github" / "attest" / "requirements.txt").read_text(encoding="utf-8")
    for line in pins.splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            assert f'name = "{name}"\nversion = "{version}"' in lock, line
