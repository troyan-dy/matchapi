"""The bare RSGI application that reads the body of every request, as matchapi must under HTTP/2 (design.md, D3)."""

from bare import answer


async def app(scope, protocol):
    await answer(scope, protocol, await protocol())
