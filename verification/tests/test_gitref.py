from hsverify import gitref


def test_two_uses_of_one_sha_get_separate_trees_and_both_clean_up():
    sha = gitref.resolve("HEAD")
    with gitref.detached_worktree(sha) as a, gitref.detached_worktree(sha) as b:
        assert a != b
        assert (a / "verification").is_dir() and (b / "verification").is_dir()
    assert not a.exists() and not b.exists()


def test_show_returns_none_for_a_missing_file():
    assert gitref.show("HEAD", "no/such/file") is None
    assert gitref.show("HEAD", "verification/pyproject.toml") is not None


def test_fetch_prunes_deleted_refs(monkeypatch):
    calls = []
    monkeypatch.setattr(gitref, "git", lambda *args: calls.append(args))
    gitref.fetch()
    assert calls == [("fetch", "--quiet", "--prune", "origin")]
