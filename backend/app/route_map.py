"""Exports the route-to-permission map: `python -m app.route_map > route-map.json`."""

import json
import sys

from app.agent_tools_api import create_agent_tools_app
from app.authz.guard import build_route_map
from app.main import create_app
from app.worker_api import create_worker_app


def main() -> None:
    routes = build_route_map(create_app(), create_worker_app(), create_agent_tools_app())
    sys.stdout.write(json.dumps(routes, indent=2) + "\n")


if __name__ == "__main__":
    main()
