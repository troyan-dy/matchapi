"""The README's service in BlackSheep, for comparison."""

from blacksheep import Application, FromJSON, json, text

app = Application()


@app.router.get("/ping")
async def ping():
    return text("pong")


@app.router.get("/users/{user_id}")
async def user(user_id: int):
    return json({"id": user_id, "name": "ann"})


@app.router.post("/users")
async def create(body: FromJSON[dict]):
    return json({"id": 7, "name": body.value["name"]}, status=201)
