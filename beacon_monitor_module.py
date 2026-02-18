# SPDX-License-Identifier: AGPL-3.0-only
# © ECAD Infra Inc.
#
# Beacon activity monitor module for Synapse.
#
# Hooks into Synapse's module API to log room activity, membership changes,
# and login events. Designed for debugging wallet/dApp connection failures
# in the Tezos Beacon relay network.
#
# Each log line includes origin=local|remote so you can distinguish
# same-server connections from federated ones at a glance.
#
# Register in homeserver.yaml:
#   modules:
#     - module: beacon_monitor_module.BeaconMonitorModule
#       config: {}

import logging
import time
from typing import Any

from synapse.events import EventBase
from synapse.module_api import ModuleApi
from synapse.types import StateMap

log = logging.getLogger(__name__)


def _event_age_ms(event: EventBase) -> int:
    """How many ms ago the event was created (origin_server_ts vs now)."""
    return int(time.time() * 1000) - event.origin_server_ts


def _user_server(user_id: str) -> str:
    """Extract the server part from a Matrix user ID (@local:server)."""
    return user_id.split(":", 1)[1] if ":" in user_id else "unknown"


class BeaconMonitorModule:
    def __init__(self, config: dict[str, Any], api: ModuleApi):
        self._api = api
        self._server_name = api.server_name

        api.register_third_party_rules_callbacks(
            on_new_event=self._on_new_event,
        )
        api.register_account_validity_callbacks(
            on_user_login=self._on_user_login,
        )

        log.info("event=INIT server=%s", self._server_name)

    def _origin(self, user_id: str) -> str:
        """Return 'local' if the user belongs to this server, 'remote' otherwise."""
        return "local" if _user_server(user_id) == self._server_name else "remote"

    async def _on_user_login(
        self,
        user_id: str,
        auth_provider_type: str | None,
        auth_provider_id: str | None,
    ) -> None:
        log.info(
            "event=LOGIN user=%s origin=%s provider_type=%s provider_id=%s",
            user_id,
            self._origin(user_id),
            auth_provider_type,
            auth_provider_id,
        )

    async def _on_new_event(
        self,
        event: EventBase,
        state_events: StateMap[EventBase],
    ) -> None:
        if event.type == "m.room.member":
            membership = event.content.get("membership", "unknown")
            target_user = event.state_key or "unknown"

            # Count local vs remote members currently in the room
            local_members = 0
            remote_members = 0
            for (etype, skey), state_event in state_events.items():
                if etype != "m.room.member":
                    continue
                if state_event.content.get("membership") != "join":
                    continue
                if _user_server(skey) == self._server_name:
                    local_members += 1
                else:
                    remote_members += 1

            room_type = "local" if remote_members == 0 else "federated"

            log.info(
                "event=MEMBERSHIP room=%s user=%s membership=%s sender=%s "
                "user_origin=%s sender_origin=%s "
                "room_type=%s local_members=%d remote_members=%d "
                "event_ts=%d age_ms=%d",
                event.room_id,
                target_user,
                membership,
                event.sender,
                self._origin(target_user),
                self._origin(event.sender),
                room_type,
                local_members,
                remote_members,
                event.origin_server_ts,
                _event_age_ms(event),
            )

        elif event.type == "m.room.create":
            log.info(
                "event=ROOM_CREATED room=%s creator=%s origin=%s "
                "event_ts=%d age_ms=%d",
                event.room_id,
                event.sender,
                self._origin(event.sender),
                event.origin_server_ts,
                _event_age_ms(event),
            )

        elif event.type == "m.room.message":
            body = event.content.get("body", "")
            log.info(
                "event=MESSAGE room=%s sender=%s origin=%s msgtype=%s "
                "body_bytes=%d event_ts=%d age_ms=%d",
                event.room_id,
                event.sender,
                self._origin(event.sender),
                event.content.get("msgtype", "unknown"),
                len(body.encode("utf-8")) if isinstance(body, str) else 0,
                event.origin_server_ts,
                _event_age_ms(event),
            )

        elif event.type == "m.room.encryption":
            log.info(
                "event=ENCRYPTION_ENABLED room=%s sender=%s origin=%s algorithm=%s "
                "event_ts=%d age_ms=%d",
                event.room_id,
                event.sender,
                self._origin(event.sender),
                event.content.get("algorithm", "unknown"),
                event.origin_server_ts,
                _event_age_ms(event),
            )

    @staticmethod
    def parse_config(config: dict[str, Any]) -> dict[str, Any]:
        return config
