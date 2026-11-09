"""The DAL reads used to decide who may approve a tool call (ROB-1671). Every
failure must read as "may not approve"."""

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from cachetools import TTLCache

from holmes.core.supabase_dal import (
    CONFIRMATION_MODE_CACHE_TTL_SEC,
    ConfirmationMode,
    SupabaseDal,
)


def _dal() -> SupabaseDal:
    dal = SupabaseDal.__new__(SupabaseDal)
    dal.enabled = True
    dal.account_id = "acc-1"
    dal.client = MagicMock()
    dal.lock = threading.Lock()
    dal.confirmation_mode_cache = TTLCache(maxsize=1, ttl=CONFIRMATION_MODE_CACHE_TTL_SEC)
    return dal


def _settings_query(dal: SupabaseDal) -> MagicMock:
    return dal.client.table.return_value.select.return_value.eq.return_value.execute


def _with_settings(dal: SupabaseDal, rows: Any) -> None:
    _settings_query(dal).return_value = MagicMock(data=rows)


@pytest.mark.parametrize(
    ("rows", "mode"),
    [
        ([{"settings": {"allowConfirmationMode": "enable-all"}}], ConfirmationMode.ENABLE_ALL),
        ([{"settings": {"allowConfirmationMode": "enable-admins"}}], ConfirmationMode.ENABLE_ADMINS),
        ([{"settings": {"allowConfirmationMode": "disable-all"}}], ConfirmationMode.DISABLED),
        ([{"settings": {"allowConfirmationMode": "yes"}}], ConfirmationMode.DISABLED),
        ([{"settings": {}}], ConfirmationMode.DISABLED),
        ([{"settings": None}], ConfirmationMode.DISABLED),
        ([], ConfirmationMode.DISABLED),
    ],
    ids=["enable-all", "enable-admins", "disable-all", "unknown", "unset", "null settings", "no row"],
)
def test_confirmation_mode_reads_the_account_setting(rows, mode):
    dal = _dal()
    _with_settings(dal, rows)

    assert dal.get_confirmation_mode() == mode
    dal.client.table.assert_called_once_with("AccountSettings")
    dal.client.table.return_value.select.return_value.eq.assert_called_once_with("account_id", "acc-1")


def test_confirmation_mode_is_cached():
    dal = _dal()
    _with_settings(dal, [{"settings": {"allowConfirmationMode": "enable-all"}}])

    dal.get_confirmation_mode()
    dal.get_confirmation_mode()

    assert _settings_query(dal).call_count == 1


def test_a_failed_confirmation_mode_read_is_disabled_and_not_cached():
    dal = _dal()
    _settings_query(dal).side_effect = [
        RuntimeError("boom"),
        MagicMock(data=[{"settings": {"allowConfirmationMode": "enable-all"}}]),
    ]

    assert dal.get_confirmation_mode() == ConfirmationMode.DISABLED
    assert dal.get_confirmation_mode() == ConfirmationMode.ENABLE_ALL


def test_confirmation_mode_is_disabled_without_supabase():
    dal = _dal()
    dal.enabled = False

    assert dal.get_confirmation_mode() == ConfirmationMode.DISABLED
    dal.client.table.assert_not_called()


def _with_admin_answer(dal: SupabaseDal, data: Any) -> None:
    dal.client.rpc.return_value.execute.return_value = MagicMock(data=data)


@pytest.mark.parametrize(("data", "admin"), [(True, True), (False, False), (None, False), ("true", False), ([True], False)])
def test_is_account_admin_accepts_only_a_boolean_true(data, admin):
    dal = _dal()
    _with_admin_answer(dal, data)

    assert dal.is_account_admin("user-1") is admin
    dal.client.rpc.assert_called_once_with("is_account_admin", {"_account_id": "acc-1", "_user_id": "user-1"})


def test_a_failed_admin_lookup_is_not_an_admin():
    dal = _dal()
    dal.client.rpc.return_value.execute.side_effect = RuntimeError("42501")

    assert dal.is_account_admin("user-1") is False


@pytest.mark.parametrize("user_id", ["", None])
def test_no_user_is_not_an_admin(user_id):
    dal = _dal()

    assert dal.is_account_admin(user_id) is False
    dal.client.rpc.assert_not_called()
