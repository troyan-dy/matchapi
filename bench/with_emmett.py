"""The README's service in Emmett, for comparison."""

from emmett import App, request, response
from emmett.tools import service

app = App(__name__)


@app.route("/ping", methods="get", output="str")
async def ping():
    return "pong"


@app.route("/users/<int:user_id>", methods="get")
@service.json
async def user(user_id):
    return {"id": user_id, "name": "ann"}


@app.route("/users", methods="post")
@service.json
async def create():
    body = await request.body_params
    response.status = 201
    return {"id": 7, "name": body["name"]}
