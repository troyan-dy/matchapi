"""The README's service in Starlette, for comparison."""

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

CREATED = 201


async def ping(request):
    return PlainTextResponse("pong")


async def user(request):
    return JSONResponse({"id": int(request.path_params["user_id"]), "name": "ann"})


async def create(request):
    body = await request.json()
    return JSONResponse({"id": 7, "name": body["name"]}, status_code=CREATED)


app = Starlette(
    routes=[
        Route("/ping", ping),
        Route("/users/{user_id}", user),
        Route("/users", create, methods=["POST"]),
    ]
)
