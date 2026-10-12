"""The README's service in Litestar, for comparison."""

from litestar import Litestar, MediaType, get, post


@get("/ping", media_type=MediaType.TEXT)
async def ping() -> str:
    return "pong"


@get("/users/{user_id:int}")
async def user(user_id: int) -> dict:
    return {"id": user_id, "name": "ann"}


@post("/users")
async def create(data: dict) -> dict:
    return {"id": 7, "name": data["name"]}


app = Litestar([ping, user, create])
