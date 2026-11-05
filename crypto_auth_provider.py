# SPDX-License-Identifier: AGPL-3.0-only
# © ECAD Infra Inc.
#
# Derived from work by Papers GmbH (AirGap):
# https://github.com/airgap-it/beacon-node
#
# Ed25519 authentication provider for Synapse.
#
# Replaces password-based login with cryptographic signature verification
# for the Tezos Beacon relay network. Users authenticate by signing a
# time-windowed challenge with their Ed25519 keypair.
#
# Username: BLAKE2b hash of the public key
# Password: ed:<hex_signature>:<hex_public_key>
# Signature covers: BLAKE2b("login:<5-minute-time-window>")
#
# Enable in homeserver.yaml:
#   modules:
#     - module: 'crypto_auth_provider.CryptoAuthProvider'
#       config: {}

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

import nacl.exceptions
import nacl.signing

if TYPE_CHECKING:
    from synapse.module_api import ModuleApi

__version__ = "0.4"
logger = logging.getLogger(__name__)


class CryptoAuthProvider:
    __version__ = __version__

    def __init__(self, config: Mapping[str, object], api: ModuleApi) -> None:
        self.api = api
        api.register_password_auth_provider_callbacks(
            auth_checkers={("m.login.password", ("password",)): self.check_auth}
        )

    async def check_auth(
        self, user: str, login_type: str, login_dict: Mapping[str, object]
    ) -> tuple[str, None] | None:
        if login_type != "m.login.password":
            logger.warning("event=AUTH_FAIL reason=invalid_login")
            return None

        user_id = user if user.startswith("@") else f"@{user}:{self.api.server_name}"
        localpart, separator, server_name = user_id[1:].partition(":")
        if (
            not separator
            or server_name != self.api.server_name
            or re.fullmatch(r"[0-9a-f]{64}", localpart) is None
        ):
            logger.warning("event=AUTH_FAIL reason=invalid_user")
            return None

        password = login_dict.get("password")
        if not isinstance(password, str):
            logger.warning(
                "event=AUTH_FAIL user=%s reason=malformed_credentials", user_id
            )
            return None
        parts = password.split(":")
        if len(parts) != 3 or parts[0] != "ed":
            logger.warning(
                "event=AUTH_FAIL user=%s reason=malformed_credentials", user_id
            )
            return None
        try:
            signature, public_key = bytes.fromhex(parts[1]), bytes.fromhex(parts[2])
        except ValueError:
            logger.warning(
                "event=AUTH_FAIL user=%s reason=malformed_credentials", user_id
            )
            return None
        if len(signature) != 64 or len(public_key) != 32:
            logger.warning(
                "event=AUTH_FAIL user=%s reason=malformed_credentials", user_id
            )
            return None
        if hashlib.blake2b(public_key, digest_size=32).hexdigest() != localpart:
            logger.warning("event=AUTH_FAIL user=%s reason=pubkey_mismatch", user_id)
            return None

        key = nacl.signing.VerifyKey(public_key)
        bucket = int(time.time()) // 300
        for offset, label in ((0, "current"), (-1, "previous"), (1, "next")):
            digest = hashlib.blake2b(
                f"login:{bucket + offset}".encode(), digest_size=32
            ).digest()
            try:
                key.verify(digest, signature)
            except nacl.exceptions.BadSignatureError:
                continue
            break
        else:
            logger.warning("event=AUTH_FAIL user=%s reason=signature_invalid", user_id)
            return None

        try:
            if await self.api.check_user_exists(user_id) is None:
                try:
                    await self.api.register_user(localpart=localpart)
                except Exception:
                    # Another first login may have registered the same account.
                    if await self.api.check_user_exists(user_id) is None:
                        raise
                    logger.info("event=REGISTER_RACE user=%s", user_id)
                else:
                    logger.info("event=REGISTER user=%s", user_id)
        except Exception:
            # Backend exceptions may contain request data; log only the outcome.
            logger.warning("event=AUTH_FAIL user=%s reason=account_backend", user_id)
            return None

        logger.info("event=AUTH_OK user=%s window=%s", user_id, label)
        return user_id, None

    @staticmethod
    def parse_config(config: Mapping[str, object]) -> Mapping[str, object]:
        return config
