# SPDX-License-Identifier: AGPL-3.0-only
"""ModuleApi authentication contract, using real Ed25519 signatures."""

import asyncio
import hashlib
import logging
from unittest.mock import AsyncMock, Mock

import nacl.signing
import pytest

from crypto_auth_provider import CryptoAuthProvider

SERVER = "beacon-1.example.com"
NOW = 1_800_000_123
KEY = nacl.signing.SigningKey(bytes(range(32)))
PUBLIC_KEY = bytes(KEY.verify_key)
LOCALPART = hashlib.blake2b(PUBLIC_KEY, digest_size=32).hexdigest()
USER_ID = f"@{LOCALPART}:{SERVER}"


def credential(offset=0):
    digest = hashlib.blake2b(
        f"login:{NOW // 300 + offset}".encode(), digest_size=32
    ).digest()
    signature = KEY.sign(digest).signature
    return f"ed:{signature.hex()}:{PUBLIC_KEY.hex()}"


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setattr("crypto_auth_provider.time.time", lambda: NOW)
    api = Mock(
        spec=[
            "server_name",
            "register_password_auth_provider_callbacks",
            "check_user_exists",
            "register_user",
        ]
    )
    api.server_name = SERVER
    api.check_user_exists = AsyncMock(return_value=USER_ID)
    api.register_user = AsyncMock(return_value=USER_ID)
    return CryptoAuthProvider({}, api), api


def login(provider, user=USER_ID, password=None, login_type="m.login.password"):
    return asyncio.run(
        provider.check_auth(
            user,
            login_type,
            {"password": credential() if password is None else password},
        )
    )


def test_registers_module_api_callback(auth):
    provider, api = auth
    api.register_password_auth_provider_callbacks.assert_called_once_with(
        auth_checkers={("m.login.password", ("password",)): provider.check_auth}
    )


@pytest.mark.parametrize(
    "offset,label", [(0, "current"), (-1, "previous"), (1, "next")]
)
def test_valid_buckets_existing_user(auth, caplog, offset, label):
    provider, api = auth
    caplog.set_level(logging.INFO)
    assert login(provider, password=credential(offset)) == (USER_ID, None)
    api.check_user_exists.assert_awaited_once_with(USER_ID)
    api.register_user.assert_not_awaited()
    assert "event=AUTH_OK" in caplog.text
    assert f"window={label}" in caplog.text
    assert credential(offset) not in caplog.text
    assert credential(offset).split(":")[1] not in caplog.text


@pytest.mark.parametrize("offset", [-2, 2, -100, 100])
def test_expired_or_future_buckets(auth, offset):
    provider, api = auth
    assert login(provider, password=credential(offset)) is None
    api.check_user_exists.assert_not_awaited()
    api.register_user.assert_not_awaited()


def test_normalizes_bare_localpart(auth):
    provider, api = auth
    assert login(provider, user=LOCALPART) == (USER_ID, None)
    api.check_user_exists.assert_awaited_once_with(USER_ID)


@pytest.mark.parametrize(
    "user",
    [
        f"@{LOCALPART.upper()}:{SERVER}",
        LOCALPART.upper(),
        f"@{'0' * 64}:{SERVER}",
        f"@{LOCALPART}:other.example.com",
        f"@{LOCALPART}",
        f"{LOCALPART}:{SERVER}",
        f"@{LOCALPART[:-1]}:{SERVER}",
        f"@{'g' * 64}:{SERVER}",
        f"@{LOCALPART} :{SERVER}",
        f"@{LOCALPART}:{SERVER}:extra",
        "",
    ],
)
def test_invalid_user(auth, user):
    provider, api = auth
    assert login(provider, user=user) is None
    api.check_user_exists.assert_not_awaited()
    api.register_user.assert_not_awaited()


@pytest.mark.parametrize(
    "password",
    [
        credential().replace("ed:", "other:", 1),
        credential().replace("ed:", "ED:", 1),
        credential() + ":extra",
        "ed",
        "ed:",
        "",
        f"ed:zz:{PUBLIC_KEY.hex()}",
        f"ed:{'00' * 64}:zz",
        *[f"ed:{'00' * length}:{PUBLIC_KEY.hex()}" for length in (0, 63, 65)],
        *[f"ed:{'00' * 64}:{'00' * length}" for length in (0, 31, 33)],
        f"ed:{'00' * 64}:{PUBLIC_KEY.hex()}",
        123,
        [],
        {},
    ],
)
def test_invalid_password(auth, caplog, password):
    provider, api = auth
    assert login(provider, password=password) is None
    api.check_user_exists.assert_not_awaited()
    api.register_user.assert_not_awaited()
    assert "event=AUTH_FAIL" in caplog.text


@pytest.mark.parametrize("login_dict", [{}, {"password": None}])
def test_missing_or_null_password(auth, login_dict):
    provider, api = auth
    assert (
        asyncio.run(provider.check_auth(USER_ID, "m.login.password", login_dict))
        is None
    )
    api.check_user_exists.assert_not_awaited()


def test_wrong_login_type(auth):
    provider, api = auth
    assert login(provider, login_type="m.login.token") is None
    api.check_user_exists.assert_not_awaited()


def test_new_user_registration(auth, caplog):
    provider, api = auth
    caplog.set_level(logging.INFO)
    api.check_user_exists.return_value = None
    assert login(provider) == (USER_ID, None)
    api.register_user.assert_awaited_once_with(localpart=LOCALPART)
    assert "event=REGISTER " in caplog.text
    assert "event=AUTH_OK" in caplog.text


def test_concurrent_first_login_recovers_registration_race(auth, caplog):
    provider, api = auth
    caplog.set_level(logging.INFO)

    async def concurrent_logins():
        both_checked = asyncio.Event()
        checks = 0
        registered = False

        async def check_user_exists(user_id):
            nonlocal checks
            assert user_id == USER_ID
            checks += 1
            if checks <= 2:
                if checks == 2:
                    both_checked.set()
                await both_checked.wait()
                return None
            return USER_ID if registered else None

        async def register_user(*, localpart):
            nonlocal registered
            assert localpart == LOCALPART
            if registered:
                raise RuntimeError("already registered")
            registered = True
            return USER_ID

        api.check_user_exists.side_effect = check_user_exists
        api.register_user.side_effect = register_user
        return await asyncio.gather(
            *[
                provider.check_auth(
                    USER_ID, "m.login.password", {"password": credential()}
                )
                for _ in range(2)
            ]
        )

    assert asyncio.run(concurrent_logins()) == [(USER_ID, None), (USER_ID, None)]
    assert api.register_user.await_count == 2
    assert "event=REGISTER_RACE" in caplog.text


def test_registration_failure_without_existing_user_fails_closed(auth, caplog):
    provider, api = auth
    caplog.set_level(logging.INFO)
    api.check_user_exists.return_value = None
    api.register_user.side_effect = RuntimeError("backend unavailable")
    assert login(provider) is None
    assert api.check_user_exists.await_count == 2
    assert "event=AUTH_FAIL" in caplog.text
    assert "event=AUTH_OK" not in caplog.text
    assert "event=REGISTER_RACE" not in caplog.text


@pytest.mark.parametrize(
    "user,password",
    [
        (f"@{LOCALPART.upper()}:{SERVER}", credential()),
        (f"@{'0' * 64}:{SERVER}", credential()),
        (USER_ID, credential().replace("ed:", "other:", 1)),
        (USER_ID, f"ed:{'00' * 63}:{PUBLIC_KEY.hex()}"),
        (USER_ID, f"ed:{'00' * 65}:{PUBLIC_KEY.hex()}"),
        (USER_ID, f"ed:{'00' * 64}:{'00' * 31}"),
        (USER_ID, f"ed:{'00' * 64}:{'00' * 33}"),
    ],
)
def test_validation_precedes_signature_verification(auth, monkeypatch, user, password):
    provider, _ = auth
    verify = Mock(wraps=nacl.signing.VerifyKey.verify)
    monkeypatch.setattr(nacl.signing.VerifyKey, "verify", verify)
    assert login(provider, user=user, password=password) is None
    verify.assert_not_called()


@pytest.mark.parametrize(
    "seconds,offset,accepted",
    [
        (299, -1, True),
        (300, -1, False),
        (299, 2, False),
        (300, 2, True),
    ],
)
def test_bucket_boundary(auth, monkeypatch, seconds, offset, accepted):
    provider, _ = auth
    monkeypatch.setattr(
        "crypto_auth_provider.time.time", lambda: NOW // 300 * 300 + seconds
    )
    expected = (USER_ID, None) if accepted else None
    assert login(provider, password=credential(offset)) == expected


def test_server_name_with_port(auth):
    provider, api = auth
    api.server_name = "relay.example.com:8448"
    user_id = f"@{LOCALPART}:{api.server_name}"
    api.check_user_exists.return_value = user_id
    assert login(provider, user=user_id) == (user_id, None)
    api.check_user_exists.assert_awaited_once_with(user_id)


@pytest.mark.parametrize("backend_failure", [False, True])
def test_failure_logs_omit_credentials(auth, caplog, backend_failure):
    provider, api = auth
    caplog.set_level(logging.INFO)
    password = (
        credential() if backend_failure else credential().replace("ed:", "invalid:", 1)
    )
    token = "access-token-sentinel"
    if backend_failure:
        api.check_user_exists.return_value = None
        api.register_user.side_effect = RuntimeError(f"{password} {token}")
    assert (
        asyncio.run(
            provider.check_auth(
                USER_ID,
                "m.login.password",
                {
                    "password": password,
                    "access_token": token,
                },
            )
        )
        is None
    )
    assert "event=AUTH_FAIL" in caplog.text
    assert password not in caplog.text
    assert password.split(":")[1] not in caplog.text
    assert token not in caplog.text
