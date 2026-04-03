"""Tests for GitHub URL parsing."""

import pytest

from repovore.github.parser import ParsedRepo, parse_github_url


class TestParseGithubUrl:
    def test_simple_repo(self) -> None:
        result = parse_github_url("https://github.com/owner/repo")
        assert result == ParsedRepo(
            instance_url="https://github.com",
            owner="owner",
            repo="repo",
        )

    def test_full_name(self) -> None:
        result = parse_github_url("https://github.com/owner/repo")
        assert result.full_name == "owner/repo"

    def test_strip_tree_path(self) -> None:
        result = parse_github_url("https://github.com/owner/repo/tree/main/src")
        assert result.full_name == "owner/repo"

    def test_strip_issues_path(self) -> None:
        result = parse_github_url("https://github.com/owner/repo/issues/42")
        assert result.full_name == "owner/repo"

    def test_strip_pull_path(self) -> None:
        result = parse_github_url("https://github.com/owner/repo/pull/10")
        assert result.full_name == "owner/repo"

    def test_strip_git_suffix(self) -> None:
        result = parse_github_url("https://github.com/owner/repo.git")
        assert result.full_name == "owner/repo"

    def test_trailing_slash(self) -> None:
        result = parse_github_url("https://github.com/owner/repo/")
        assert result.full_name == "owner/repo"

    def test_whitespace_stripped(self) -> None:
        result = parse_github_url("  https://github.com/owner/repo  ")
        assert result.full_name == "owner/repo"

    def test_http_scheme(self) -> None:
        result = parse_github_url("http://github.com/owner/repo")
        assert result.instance_url == "http://github.com"

    def test_no_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid URL"):
            parse_github_url("github.com/owner/repo")

    def test_single_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="owner/repo"):
            parse_github_url("https://github.com/onlyone")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_github_url("")

    def test_slug(self) -> None:
        result = parse_github_url("https://github.com/owner/repo")
        assert result.slug == "owner__repo"
