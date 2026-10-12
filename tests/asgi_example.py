# asgi.py of the README: the handler for a server of your own
from sample import handle

import matchapi

app = matchapi.app(handle)  # uvicorn asgi:app
