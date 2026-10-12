"""The bare RSGI application that also reads the body before answering, as matchapi must (design.md, D3)."""

from bare import app as answer


async def app(scope, protocol):
    await protocol()
    await answer(scope, protocol)
