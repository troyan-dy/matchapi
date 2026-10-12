"""A bare RSGI application with the README's routes: the ceiling, what granian does with no framework at all."""

import json

TEXT = [("content-type", "text/plain; charset=utf-8")]
JSON = [("content-type", "application/json")]


async def app(scope, protocol):
    if scope.method == "GET" and scope.path == "/ping":
        protocol.response_str(200, TEXT, "pong")
    elif scope.method == "GET" and scope.path.startswith("/users/"):
        body = json.dumps({"id": int(scope.path[7:]), "name": "ann"}, separators=(",", ":"))
        protocol.response_bytes(200, JSON, body.encode())
    else:
        protocol.response_str(404, TEXT, "Not Found")
