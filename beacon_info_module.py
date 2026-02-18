# SPDX-License-Identifier: AGPL-3.0-only
# © ECAD Infra Inc.
#
# Derived from work by Papers GmbH (AirGap):
# https://github.com/airgap-it/beacon-node

import json
import logging
import os
import time
from typing import Any

from twisted.web.resource import Resource

logger = logging.getLogger(__name__)


class BeaconInfoResource(Resource):
    isLeaf = True

    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.config = config

    def render_GET(self, request):
        request.setHeader(b"content-type", b"application/json; charset=utf-8")
        request.setHeader(b"Access-Control-Allow-Origin", b"*")
        return json.dumps({
            "region": os.environ.get("SERVER_REGION", "region not set"),
            "known_servers": self.config.get("known_servers", []),
            "timestamp": time.time(),
        }).encode("utf-8")


class BeaconInfoModule:
    def __init__(self, config: dict[str, Any], api):
        logger.info("event=INIT config=%s", config)
        self.config = config
        self.api = api
        self.api.register_web_resource(
            path="/_synapse/client/beacon/info",
            resource=BeaconInfoResource(config),
        )

    @staticmethod
    def parse_config(config: dict[str, Any]) -> dict[str, Any]:
        return config
