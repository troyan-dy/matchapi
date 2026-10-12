# matchapi

An HTTP service is one function, and `match` over the route replaces the router. This glossary fixes the words
the README, the design notes and the code use for the pieces of that model.

## Language

**Handler**:
The user's function `(Request) -> value`: the whole service. It is called directly in tests.
_Avoid_: view, endpoint, controller, route function

**App**:
The handler wrapped by the `matchapi.app` decorator: the object a server runs. Calling it is calling the handler.
_Avoid_: application object, server, service object

**Request**:
The incoming request as plain data: method, path, route, query, headers, body. No transport underneath.
_Avoid_: scope, context

**Route**:
The tuple `(method, *segments)` that the handler's `match` is written over: `GET /users/42` is
`("GET", "users", "42")`.
_Avoid_: path (that is the string `/users/42`), URL, endpoint

**Response**:
What the server sends: status, headers, body. Built from the handler's return value by the response rules.
_Avoid_: result, reply

**Response rules**:
The mapping from a handler's return value to a Response: `dict` is JSON, `str` is text, `int` is a status, a tuple
adds status and headers, `Response` is returned as is.
_Avoid_: serialization, rendering

**Server**:
The program that speaks HTTP and calls the app once per request: granian. Not part of matchapi.
_Avoid_: runtime, runner, worker
