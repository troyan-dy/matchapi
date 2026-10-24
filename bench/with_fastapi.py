"""The README's service in FastAPI, for comparison."""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

CREATED = 201
app = FastAPI()


class NewUser(BaseModel):
    name: str


@app.get("/ping", response_class=PlainTextResponse)
async def ping():
    return "pong"


@app.get("/users/{user_id}")
async def user(user_id: int):
    return {"id": user_id, "name": "ann"}


@app.post("/users", status_code=CREATED)
async def create(body: NewUser):
    return {"id": 7, "name": body.name}
