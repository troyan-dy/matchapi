"""The README's service under matchapi, served by granian."""

import matchapi


@matchapi.app
async def handle(req):
    match req.route:
        case "GET", "ping":
            return "pong"
        case "GET", "users", user_id:
            return {"id": int(user_id), "name": "ann"}
        case "POST", "users":
            body = req.json()
            return 201, {"id": 7, "name": body["name"]}
        case _:
            return 404
