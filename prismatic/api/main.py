from fastapi import FastAPI
from prismatic.api.routers import credits, jobs

app = FastAPI(
    title="Prismatic Engine API Gateway",
    description="Public-facing API for Prismatic Engine with OAuth2 Bearer auth",
    version="0.1.0",
)

# Include routers
app.include_router(credits.router, prefix="/v1", tags=["credits"])
app.include_router(jobs.router, prefix="/v1", tags=["jobs"])

@app.get("/")
async def root():
    return {"message": "Prismatic Engine API Gateway is running"}

@app.get("/health")
async def health():
    return {"status": "ok"}
