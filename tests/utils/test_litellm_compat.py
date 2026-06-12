from holmes.utils.litellm_compat import (
    get_litellm_version,
    is_litellm_compatible,
)


def test_get_litellm_version_returns_installed_version():
    # litellm is a hard dependency, so this is always resolvable in tests.
    version = get_litellm_version()
    assert version is not None
    assert version[0].isdigit()


def test_is_litellm_compatible_basic():
    assert is_litellm_compatible("1.85.5", "1.87.2") is True
    assert is_litellm_compatible("1.87.2", "1.87.2") is True
    assert is_litellm_compatible("1.89.0", "1.87.2") is False


def test_is_litellm_compatible_fails_closed_on_missing_minimum():
    assert is_litellm_compatible(None, "1.99.0") is False
    assert is_litellm_compatible("", "1.99.0") is False


def test_is_litellm_compatible_unknown_local_version_assumed_ok():
    # If we can't read our own litellm version, don't hide models.
    assert is_litellm_compatible("1.85.5", None) is True
    assert is_litellm_compatible("1.85.5", "") is True


def test_is_litellm_compatible_numeric_not_lexicographic():
    # 1.83.10 > 1.83.7 numerically (string compare would get this wrong).
    assert is_litellm_compatible("1.83.10", "1.83.7") is False
    assert is_litellm_compatible("1.83.7", "1.83.10") is True


def test_is_litellm_compatible_unparseable_minimum_fails_closed():
    assert is_litellm_compatible("not-a-version", "1.99.0") is False
