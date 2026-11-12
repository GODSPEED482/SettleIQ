"""Kitebase's website: the store and checkout, the source view, the demo controls and the
judge guide. The source view and the reset exist here only, never in SettleIQ.

Each handler opens its own connection and leaves the block before it returns, so its writes are
committed before the response is sent and rolled back if it raises.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from shop import config, db, store
from shop.paypal import PayPal

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["ago"] = store.ago


class CheckoutBody(BaseModel):
    plan_id: str | None = Field(default=None, max_length=50)
    email: str = Field(default="", max_length=200)
    name: str = Field(default="", max_length=100)
    order_ref: str | None = Field(default=None, max_length=50)


class CaptureBody(BaseModel):
    order_ref: str = Field(max_length=50)
    paypal_order_id: str = Field(max_length=50)


def _refusal(error: store.ShopError) -> JSONResponse:
    body: dict[str, Any] = {"error": error.code, "message": error.message}
    order_ref = getattr(error, "order_ref", None)
    if order_ref:
        body["order_ref"] = order_ref
    return JSONResponse(body, status_code=error.status)


def _back(path: str, **params: str) -> RedirectResponse:
    query = "&".join(f"{key}={quote(value)}" for key, value in params.items())
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def create_app(settings: config.Settings | None = None, paypal: PayPal | None = None) -> FastAPI:
    settings = settings or config.load()
    paypal = paypal or PayPal(settings.paypal_api_base, settings.paypal_client_id,
                              settings.paypal_client_secret)
    reader = store.PayPalReader(paypal)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with db.connect(settings) as conn:
            db.setup(conn, settings)
            store.ensure_catalogue(conn)
            if store.status(conn) is None:
                store.reset(conn, settings, force=True)
        yield

    app = FastAPI(title="Kitebase", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {
            "settleiq_url": settings.settleiq_url, "paypal_ready": paypal.configured,
            **context,
        })

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        with db.connect(settings) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        with db.connect(settings) as conn:
            plans, meta = store.catalogue(conn), store.status(conn)
        return page(request, "store.html", plans=plans, client_id=settings.paypal_client_id,
                    meta=meta)

    @app.post("/api/checkout")
    def checkout(body: CheckoutBody):
        try:
            with db.connect(settings) as conn:
                return store.start_checkout(conn, settings, paypal, plan_id=body.plan_id,
                                            email=body.email, name=body.name,
                                            order_ref=body.order_ref)
        except store.ShopError as error:
            return _refusal(error)

    @app.post("/api/checkout/capture")
    def capture(body: CaptureBody):
        try:
            with db.connect(settings) as conn:
                return store.capture(conn, paypal, body.order_ref, body.paypal_order_id)
        except store.ShopError as error:
            return _refusal(error)

    @app.get("/orders/{order_ref}", response_class=HTMLResponse)
    def order(order_ref: str, request: Request):
        with db.connect(settings) as conn:
            store.provision(conn)
            conn.commit()
            found = [o for o in store.orders_view(conn, reader) if o["order_ref"] == order_ref]
        return page(request, "order.html", order=found[0] if found else None,
                    order_ref=order_ref)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request):
        with db.connect(settings) as conn:
            store.provision(conn)
            conn.commit()
            orders = store.orders_view(conn, reader)
            subscriptions = store.subscriptions_view(conn)
            meta = store.status(conn)
        return page(request, "sources.html", orders=orders, subscriptions=subscriptions,
                    meta=meta)

    @app.get("/demo", response_class=HTMLResponse)
    def demo(request: Request, message: str = "", error: str = ""):
        with db.connect(settings) as conn:
            meta = store.status(conn)
        return page(request, "demo.html", meta=meta, faults=store.FAULTS, message=message,
                    error=error)

    @app.post("/demo/reset")
    def reset():
        try:
            with db.connect(settings) as conn:
                generation = store.reset(conn, settings)
        except store.ShopError as error:
            return _back("/demo", error=error.message)
        return _back("/demo", message=f"Reset to generation {generation}.")

    @app.post("/demo/faults")
    def faults(queue: str = Form("")):
        try:
            with db.connect(settings) as conn:
                store.set_faults(conn, [f for f in queue.split(",") if f])
        except store.ShopError as error:
            return _back("/demo", error=error.message)
        return _back("/demo", message="Fault queue updated.")

    @app.get("/guide", response_class=HTMLResponse)
    def guide(request: Request):
        return page(request, "guide.html")

    return app
