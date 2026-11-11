"""Runtime-identity service (spec 9.7.6, check 5; AC-REL-09).

The only container with access to the container runtime. `GET /identity` answers with the
repository digest of the container at the caller's network address, and nothing else: the
caller cannot name a container, image or digest, and no header or query is read. A container
without a registry digest (a locally built image) gets 404, so it can never pass POL-14.

Standard library only, so it runs on a stock Python image with the Docker socket mounted
read-only and no published port.
"""

import http.client
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

DOCKER_SOCKET = "/var/run/docker.sock"
PORT = 8010


class _DockerConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost", timeout=5)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(DOCKER_SOCKET)


def _docker(path: str) -> object:
    connection = _DockerConnection()
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise LookupError(f"docker returned {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()


def digest_for(address: str) -> str | None:
    """The registry digest of the one running container that holds `address`."""
    matches = []
    for container in _docker("/containers/json"):
        networks = container.get("NetworkSettings", {}).get("Networks", {}) or {}
        if any(n.get("IPAddress") == address for n in networks.values()):
            matches.append(container)
    if len(matches) != 1:
        return None
    image = _docker(f"/images/{quote(matches[0]['ImageID'], safe='')}/json")
    digests = {d.split("@", 1)[1] for d in image.get("RepoDigests") or [] if "@" in d}
    return digests.pop() if len(digests) == 1 else None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        if self.path != "/identity":
            self._send(404, {"error": "not found"})
            return
        try:
            digest = digest_for(self.client_address[0])
        except (OSError, LookupError, ValueError, KeyError):
            self._send(503, {"error": "runtime unavailable"})
            return
        if digest is None:
            self._send(404, {"error": "no registry digest for this caller"})
        else:
            self._send(200, {"digest": digest})

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
