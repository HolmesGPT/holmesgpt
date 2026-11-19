import time

import pytest

from holmes.plugins.toolsets.utils import process_timestamps_to_int


@pytest.mark.parametrize("placeholder", ["None", "none", "null", "", " NULL "])
def test_placeholder_end_means_now(placeholder):
    start, end = process_timestamps_to_int("-3600", placeholder, 3600)
    now = int(time.time())
    assert abs(end - now) <= 2
    assert end - start == 3600


def test_placeholder_start_uses_default_span():
    start, end = process_timestamps_to_int("None", None, 600)
    assert end - start == 600


def test_now_end():
    _, end = process_timestamps_to_int("-60", "now", 3600)
    assert abs(end - int(time.time())) <= 2
