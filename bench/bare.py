"""A bare RSGI application with the README's routes: the ceiling, what granian does with no framework at all.

It reads the request body only for the route that has one, as a hand-written application would.
"""

import json

TEXT = (("content-type", "text/plain; charset=utf-8"),)
JSON = (("content-type", "application/json"),)
OK = 200
CREATED = 201
NOT_FOUND = 404


async def answer(scope, protocol, body):
    if scope.method == "GET" and scope.path == "/ping":
        protocol.response_str(OK, TEXT, "pong")
    elif scope.method == "GET" and scope.path.startswith("/users/"):
        user = {"id": int(scope.path[7:]), "name": "ann"}
        protocol.response_str(OK, JSON, json.dumps(user, separators=(",", ":")))
    elif scope.method == "POST" and scope.path == "/users":
        user = {"id": 7, "name": json.loads(body)["name"]}
        protocol.response_str(CREATED, JSON, json.dumps(user, separators=(",", ":")))
    else:
        protocol.response_str(NOT_FOUND, TEXT, "Not Found")


async def app(scope, protocol):
    body = await protocol() if scope.method == "POST" else b""
    await answer(scope, protocol, body)
