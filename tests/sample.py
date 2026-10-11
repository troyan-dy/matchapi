"""The service of the README: one function, `match` instead of a router."""

from matchapi import Request


async def handle(req: Request) -> object:
    match req.route:
        case "GET", "ping":
            return "pong"
        case "GET", "users", user_id:
            return {"id": int(user_id)}
        case "POST", "users":
            body = req.json()
            return 201, {"name": body["name"]}
        case _:
            return 404
