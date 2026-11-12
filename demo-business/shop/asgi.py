"""The ASGI entry point uvicorn serves. Settings load here, not at import of shop.main."""

from shop.main import create_app

app = create_app()
