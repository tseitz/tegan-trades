import os

from ingestion.env import load_env


def test_loads_keys_from_repo_root_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("WEBSHARE_PROXY_USERNAME=alice\nWEBSHARE_PROXY_PASSWORD=s3cret\n")
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    load_env(root=tmp_path)
    assert os.environ["WEBSHARE_PROXY_USERNAME"] == "alice"
    assert os.environ["WEBSHARE_PROXY_PASSWORD"] == "s3cret"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    """An explicitly exported var is a deliberate override — a checked-out .env must not
    silently replace it (e.g. running one-off against different credentials)."""
    (tmp_path / ".env").write_text("WEBSHARE_PROXY_USERNAME=from_file\n")
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "from_shell")
    load_env(root=tmp_path)
    assert os.environ["WEBSHARE_PROXY_USERNAME"] == "from_shell"


def test_missing_env_file_is_not_an_error(tmp_path):
    load_env(root=tmp_path)   # proxy is opt-in; absence is the normal unproxied path


def test_ignores_comments_blank_lines_and_export_prefix(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# a comment\n\n export FOO_BAR=baz \n#WEBSHARE_PROXY_USERNAME=nope\n"
    )
    monkeypatch.delenv("FOO_BAR", raising=False)
    load_env(root=tmp_path)
    assert os.environ["FOO_BAR"] == "baz"
    assert "WEBSHARE_PROXY_USERNAME" not in os.environ or \
        os.environ.get("WEBSHARE_PROXY_USERNAME") != "nope"


def test_strips_surrounding_quotes(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('QUOTED="hello world"\nSINGLE=\'x=y\'\n')
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("SINGLE", raising=False)
    load_env(root=tmp_path)
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["SINGLE"] == "x=y"   # only the FIRST '=' splits


def test_is_idempotent(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ONCE=1\n")
    monkeypatch.delenv("ONCE", raising=False)
    load_env(root=tmp_path)
    load_env(root=tmp_path)
    assert os.environ["ONCE"] == "1"
