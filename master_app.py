from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx
import asyncio
import os
from typing import List, Optional
from datetime import datetime, timezone, timedelta

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION
# ============================================
WORKERS_FILE = os.getenv("WORKERS_FILE", "workers.txt")
ALL_WORKERS = []  # list of dict: {"url": str, "used": bool, "meeting": str or None}

def normalize_url(url: str) -> str:
    """Ensure URL starts with http:// or https://"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def load_workers():
    """Load worker URLs from file, ignoring empty lines, and normalize protocol."""
    global ALL_WORKERS
    ALL_WORKERS = []
    try:
        with open(WORKERS_FILE, "r") as f:
            for line in f:
                url = line.strip()
                if url:
                    # Normalize: add https:// if missing
                    url = normalize_url(url)
                    ALL_WORKERS.append({"url": url, "used": False, "meeting": None})
    except FileNotFoundError:
        print(f"⚠️ Workers file '{WORKERS_FILE}' not found. Create one with URLs.")
    print(f"✅ Loaded {len(ALL_WORKERS)} workers.")

# Load on startup
load_workers()

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 1

class KillMeetingRequest(BaseModel):
    meeting_code: str

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}  # meeting_code -> {"workers": [urls], "total_bots": int, "started_at": ...}
used_workers = set()  # urls currently assigned to any meeting

# ============================================
# API ENDPOINTS
# ============================================
@app.get("/")
async def root():
    return {
        "message": "Master Controller Running",
        "total_capacity": len(ALL_WORKERS),
        "used": len(used_workers),
        "billing_enabled": billing_enabled
    }

@app.post("/api/start-bots")
async def start_bots(request: StartBotsRequest):
    global billing_enabled, active_meetings, used_workers
    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled.")

    total_needed = request.bot_count
    if total_needed < 1:
        raise HTTPException(status_code=400, detail="Bot count must be at least 1.")

    # Find available workers
    available = [w for w in ALL_WORKERS if not w["used"]]
    if len(available) < total_needed:
        raise HTTPException(status_code=400, detail=f"Only {len(available)} workers available, requested {total_needed}.")

    # Allocate workers
    allocated = available[:total_needed]
    results = []
    success_count = 0

    for w in allocated:
        # Mark as used before trying
        w["used"] = True
        w["meeting"] = request.meeting_code
        used_workers.add(w["url"])

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{w['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": 1,
                        "duration_minutes": request.duration_minutes,
                        "name_type": request.name_type,
                        "custom_names": request.custom_names[:1] if request.custom_names else None
                    },
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    success_count += 1
                    results.append({"url": w["url"], "status": "success"})
                else:
                    # Free worker on failure
                    w["used"] = False
                    w["meeting"] = None
                    used_workers.discard(w["url"])
                    results.append({"url": w["url"], "status": "failed", "error": resp.text})
        except Exception as e:
            w["used"] = False
            w["meeting"] = None
            used_workers.discard(w["url"])
            results.append({"url": w["url"], "status": "failed", "error": str(e)})

    # Update meeting state only if at least one bot started
    if success_count > 0:
        if request.meeting_code not in active_meetings:
            active_meetings[request.meeting_code] = {
                "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
                "workers": [w["url"] for w in allocated if w["used"]],  # only successful ones
                "total_bots": success_count,
                "status": "running"
            }
        else:
            meeting = active_meetings[request.meeting_code]
            meeting["workers"].extend([w["url"] for w in allocated if w["used"]])
            meeting["total_bots"] += success_count

    return {
        "success": True,
        "message": f"Started {success_count} bots for meeting {request.meeting_code}.",
        "total_bots": success_count,
        "results": results
    }

@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    global active_meetings, used_workers, ALL_WORKERS

    meeting_code = request.meeting_code
    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = active_meetings[meeting_code]
    worker_urls = meeting["workers"]
    results = []

    for url in worker_urls:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{url}/api/stop-bots",
                    json={"meeting_code": meeting_code},
                    follow_redirects=True
                )
                if resp.status_code == 200:
                    results.append({"url": url, "status": "stopped"})
                else:
                    results.append({"url": url, "status": "failed", "error": resp.text})
        except Exception as e:
            results.append({"url": url, "status": "failed", "error": str(e)})

    # Free all workers that were used for this meeting
    for w in ALL_WORKERS:
        if w["url"] in worker_urls:
            w["used"] = False
            w["meeting"] = None
            used_workers.discard(w["url"])

    del active_meetings[meeting_code]

    return {
        "success": True,
        "message": f"Killed meeting {meeting_code}.",
        "details": results,
        "total_bots_killed": len(worker_urls)
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: dict):
    global billing_enabled, active_meetings, used_workers, ALL_WORKERS
    enabled = request.get("enabled", True)
    billing_enabled = enabled

    if not enabled:
        # Kill all active meetings
        for meeting_code in list(active_meetings.keys()):
            meeting = active_meetings[meeting_code]
            for url in meeting["workers"]:
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{url}/api/stop-bots",
                            json={"meeting_code": meeting_code},
                            follow_redirects=True
                        )
                except:
                    pass
                # Free worker
                for w in ALL_WORKERS:
                    if w["url"] == url:
                        w["used"] = False
                        w["meeting"] = None
                        used_workers.discard(url)
            del active_meetings[meeting_code]

    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots killed)"
    }

@app.get("/api/status")
async def get_status():
    total_capacity = len(ALL_WORKERS)
    used = len(used_workers)
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_capacity": total_capacity,
        "used_workers": used,
        "available_workers": total_capacity - used,
        "workers": ALL_WORKERS
    }

@app.post("/api/reload-workers")
async def reload_workers():
    """Reload workers from file (useful when file changes)."""
    load_workers()
    return {"success": True, "message": f"Reloaded {len(ALL_WORKERS)} workers."}

@app.get("/health")
async def health():
    return {"online": True, "workers": len(ALL_WORKERS)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
