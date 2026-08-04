import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.models.db import init_db
from app.api.routes import router
from app.seed.generate import generate_network


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.seed_on_startup:
        stats = generate_network()
        app.state.seed_stats = stats
    yield


app = FastAPI(
    title="KSPDB Fault Localization",
    description="Outage detection and span localization for a Karnataka ESCOM subdivision",
    version="1.0.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True, "service": "kspdb-outage"}


@app.get("/api/meta")
def meta(request: Request):
    return {
        "name": "KSPDB Fault Localization",
        "seed": getattr(request.app.state, "seed_stats", None),
        "operator": "Control Room — SD07",
    }


# Simple ingest timing middleware for demo metrics
@app.middleware("http")
async def timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return response
