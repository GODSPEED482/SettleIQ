"""Exports the route-to-permission map: `python -m app.route_map > route-map.json`."""

import json
import sys

from app.authz.guard import build_route_map
from app.main import create_app
from app.worker_api import create_worker_app


# TODO(G1 1.3 Rishav): owner review of the exported map; G1 exit needs every route in it.
def main() -> None:
    routes = build_route_map(create_app(), create_worker_app())
    sys.stdout.write(json.dumps(routes, indent=2) + "\n")


if __name__ == "__main__":
    main()
