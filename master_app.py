from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "message": "Master is ALIVE",
        "port": os.getenv("PORT"),
        "status": "ok"
    }

@app.get("/api/status")
async def status():
    return {
        "total_capacity": 35,
        "running_bots": 0,
        "available_bots": 35,
        "used_workers": 0,
        "available_workers": 35,
        "workers": [],
        "active_meetings": {}
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
