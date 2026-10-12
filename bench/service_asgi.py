"""The README's service as matchapi 0.1.0 wrote it: no import, served by `matchapi service_asgi` under uvicorn."""


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
