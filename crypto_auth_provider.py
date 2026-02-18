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
#   password_providers:
#     - module: 'crypto_auth_provider.CryptoAuthProvider'
#       config:
#         enabled: true

import logging
import time

from twisted.internet import defer
import pysodium

__version__ = "0.2"
logger = logging.getLogger(__name__)


class CryptoAuthProvider:
    __version__ = "0.2"

    def __init__(self, config, account_handler):
        self.account_handler = account_handler
        self.config = config
        self.log = logging.getLogger(__name__)

    @defer.inlineCallbacks
    def check_password(self, user_id: str, password: str):
        try:
            public_key_hash = bytes.fromhex(user_id.split(":", 1)[0][1:])
            signature = bytes.fromhex(password.split(":")[1])
            public_key = bytes.fromhex(password.split(":")[2])
            public_key_digest = pysodium.crypto_generichash(public_key)
        except (ValueError, IndexError) as exc:
            self.log.warning(
                "event=AUTH_FAIL user=%s reason=malformed_credentials err=%s",
                user_id, exc)
            defer.returnValue(False)
            return

        if public_key_hash.hex() != public_key_digest.hex():
            self.log.warning(
                "event=AUTH_FAIL user=%s reason=pubkey_mismatch", user_id)
            defer.returnValue(False)
            return

        current_time_window = int(time.time() / (5 * 60))

        # Check current, previous (-5min), and next (+5min) time windows
        # to handle reasonable clock skew between client and server
        verified = False
        for offset, label in ((0, "current"), (-1, "previous"), (1, "next")):
            message_digest = pysodium.crypto_generichash(
                "login:{}".format(current_time_window + offset).encode())
            try:
                pysodium.crypto_sign_verify_detached(
                    signature, message_digest, public_key)
            except Exception:
                continue
            verified = True
            self.log.info("event=AUTH_OK user=%s window=%s", user_id.lower(), label)
            break

        if not verified:
            self.log.warning(
                "event=AUTH_FAIL user=%s reason=signature_invalid", user_id)
            defer.returnValue(False)
            return

        if not (yield self.account_handler.check_user_exists(user_id)):
            self.log.info("event=REGISTER user=%s", user_id.lower())
            try:
                yield self.account_handler.register(localpart=public_key_digest.hex())
            except Exception as exc:
                # Handle race: concurrent first-login may have registered already
                if (yield self.account_handler.check_user_exists(user_id)):
                    self.log.info("event=REGISTER_RACE user=%s (already registered)", user_id.lower())
                else:
                    raise exc

        defer.returnValue(True)

    @staticmethod
    def parse_config(config):
        return config
