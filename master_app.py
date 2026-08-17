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
async def get_status():
    return {
        "total_capacity": 35,
        "running_bots": 0,
        "available_bots": 35,
        "used_workers": 0,
        "available_workers": 35,
        "workers": [
            {
                "id": 1,
                "name": "zoom-worker-1",
                "url": "https://YOUR-WORKER-URL.up.railway.app",
                "capacity": 35,
                "status": "idle",
                "used_bots": 0
            }
        ],
        "active_meetings": {}
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
