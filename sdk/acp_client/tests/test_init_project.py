"""Tests for `acp init` — scaffolds .acp/policy.yaml + .acp/example.py."""
import pytest

from sdk.acp_client.init_project import init_project
from sdk.acp_client.policy import load_policy


def test_init_creates_expected_files(tmp_path) -> None:
    result = init_project(target_dir=tmp_path, agent_id="agent_test")
    names = {p.name for p in result.created}
    assert names == {"policy.yaml", "example.py"}
    assert result.skipped == []
    assert (tmp_path / ".acp" / "policy.yaml").exists()
    assert (tmp_path / ".acp" / "example.py").exists()


def test_scaffolded_policy_validates(tmp_path) -> None:
    init_project(target_dir=tmp_path, agent_id="agent_alpha")
    policy = load_policy(tmp_path / ".acp" / "policy.yaml")
    assert policy.agent == "agent_alpha"
    assert policy.version == 1
    assert len(policy.allow) >= 1
    assert len(policy.deny) >= 1
    assert policy.autonomy.max_actions_per_minute is not None


def test_init_does_not_overwrite_without_force(tmp_path) -> None:
    (tmp_path / ".acp").mkdir()
    (tmp_path / ".acp" / "policy.yaml").write_text("existing: kept")
    result = init_project(target_dir=tmp_path, agent_id="agent_test")
    assert any(p.name == "policy.yaml" for p in result.skipped)
    assert (tmp_path / ".acp" / "policy.yaml").read_text() == "existing: kept"


def test_init_force_overwrites(tmp_path) -> None:
    (tmp_path / ".acp").mkdir()
    (tmp_path / ".acp" / "policy.yaml").write_text("existing: kept")
    result = init_project(target_dir=tmp_path, agent_id="agent_test", force=True)
    assert any(p.name == "policy.yaml" for p in result.created)
    assert "existing: kept" not in (tmp_path / ".acp" / "policy.yaml").read_text()


def test_init_rejects_missing_target(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        init_project(target_dir=missing, agent_id="x")


def test_init_rejects_empty_agent_id(tmp_path) -> None:
    with pytest.raises(ValueError):
        init_project(target_dir=tmp_path, agent_id="")


def test_init_substitutes_agent_id_in_example(tmp_path) -> None:
    init_project(target_dir=tmp_path, agent_id="agent_42")
    example = (tmp_path / ".acp" / "example.py").read_text()
    assert 'agent_id="agent_42"' in example
