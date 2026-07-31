from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import asyncio
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
# CONFIGURATION - 4 WORKERS (or as many as you have)
# ============================================
PROJECTS = [
    {
        "id": 1,
        "name": "zoom-worker-1",
        "url": "https://zoom-worker-production-9981.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 2,
        "name": "zoom-worker-2",
        "url": "https://zoom-worker-production-c2b3.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 3,
        "name": "zoom-worker-3",
        "url": "https://zoom-worker-production-fd51.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    },
    {
        "id": 4,
        "name": "zoom-worker-4",
        "url": "https://zoom-worker-production-ffd8.up.railway.app",
        "capacity": 5,
        "replicas": 42,
        "total_bots": 210,
        "status": "idle",
        "active_meeting": None,
        "used_bots": 0
    }
]

TOTAL_CAPACITY = sum(p["total_bots"] for p in PROJECTS)  # 840

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 60
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    bot_count: int = 5

class KillMeetingRequest(BaseModel):
    meeting_code: str

# ============================================
# STATE
# ============================================
billing_enabled = True
active_meetings = {}

# ============================================
# API ENDPOINTS
# ============================================
@app.get("/")
async def root():
    return {
        "message": "Master Controller Running",
        "total_capacity": TOTAL_CAPACITY,
        "projects": len(PROJECTS),
        "billing_enabled": billing_enabled
    }

@app.post("/api/start-bots")
async def start_bots(request: StartBotsRequest):
    global billing_enabled, active_meetings

    if not billing_enabled:
        raise HTTPException(status_code=403, detail="Billing is disabled.")

    total_bots_requested = request.bot_count
    if total_bots_requested < 1 or total_bots_requested > TOTAL_CAPACITY:
        raise HTTPException(status_code=400, detail=f"Requested {total_bots_requested} bots, but capacity is {TOTAL_CAPACITY}.")

    # Calculate available capacity
    used_bots = sum(p["used_bots"] for p in PROJECTS)
    available = TOTAL_CAPACITY - used_bots
    if total_bots_requested > available:
        raise HTTPException(status_code=400, detail=f"Only {available} bots available.")

    # Distribute across idle projects
    available_projects = [p for p in PROJECTS if p["status"] == "idle" and p["used_bots"] == 0]
    if not available_projects:
        raise HTTPException(status_code=400, detail="No idle projects.")

    # Allocate bots to projects
    allocated = []
    remaining = total_bots_requested
    for project in available_projects:
        if remaining <= 0:
            break
        take = min(project["total_bots"], remaining)
        allocated.append({"project": project, "bots": take})
        remaining -= take

    if remaining > 0:
        raise HTTPException(status_code=400, detail="Not enough capacity.")

    results = []
    total_started = 0
    assigned_project_ids = []

    for alloc in allocated:
        project = alloc["project"]
        count = alloc["bots"]
        chunks = [5] * (count // 5)
        if count % 5 != 0:
            chunks.append(count % 5)

        for chunk in chunks:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{project['url']}/api/start-bots",
                        json={
                            "meeting_code": request.meeting_code,
                            "passcode": request.passcode,
                            "bot_count": chunk,
                            "duration_minutes": request.duration_minutes,
                            "name_type": request.name_type,
                            "custom_names": request.custom_names[:chunk] if request.custom_names else None
                        }
                    )
                    if resp.status_code == 200:
                        project["status"] = "running"
                        project["active_meeting"] = request.meeting_code
                        project["used_bots"] += chunk
                        if project["id"] not in assigned_project_ids:
                            assigned_project_ids.append(project["id"])
                        total_started += chunk
                        results.append({"project": project["name"], "status": "success", "bots": chunk})
                    else:
                        results.append({"project": project["name"], "status": "failed", "error": resp.text})
            except Exception as e:
                results.append({"project": project["name"], "status": "failed", "error": str(e)})

    # Update meeting
    if request.meeting_code in active_meetings:
        meeting = active_meetings[request.meeting_code]
        meeting["total_bots"] += total_started
        meeting["projects"] = list(set(meeting.get("projects", []) + assigned_project_ids))
        meeting["status"] = "running"
    else:
        active_meetings[request.meeting_code] = {
            "meeting_code": request.meeting_code,
            "started_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "total_bots": total_started,
            "projects": assigned_project_ids,
            "duration": request.duration_minutes,
            "status": "running"
        }

    return {
        "success": True,
        "message": f"Started {total_started} bots. Meeting now has {active_meetings[request.meeting_code]['total_bots']} bots.",
        "total_bots": active_meetings[request.meeting_code]['total_bots'],
        "results": results
    }

# ============================================
# KILL MEETING — Master Controlled (No Redis)
# ============================================
@app.post("/api/kill-meeting")
async def kill_meeting(request: KillMeetingRequest):
    """
    Kill all bots for a given meeting across ALL workers and their replicas.
    This sends multiple stop requests to each worker to hit as many replicas as possible.
    """
    meeting_code = request.meeting_code

    if meeting_code not in active_meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = active_meetings[meeting_code]
    results = []

    # For each worker, send stop requests multiple times (to hit all replicas)
    for project in PROJECTS:
        project_results = []
        # Send 20 stop requests per worker (adjust based on replicas count)
        for attempt in range(20):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{project['url']}/api/stop-bots",
                        json={"meeting_code": meeting_code}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        killed = data.get("bots_killed_local", 0)
                        project_results.append(killed)
                    else:
                        project_results.append(0)
            except Exception:
                project_results.append(0)
            # Small delay between attempts to let load balancer rotate
            await asyncio.sleep(0.2)

        # Total killed for this worker (sum of all attempts)
        total_killed = sum(project_results)
        results.append({
            "project": project["name"],
            "attempts": len(project_results),
            "total_killed": total_killed
        })

        # Reset project state
        project["status"] = "idle"
        project["active_meeting"] = None
        project["used_bots"] = 0

    # Remove meeting from active
    del active_meetings[meeting_code]

    return {
        "success": True,
        "message": f"Killed meeting {meeting_code} across all workers.",
        "details": results,
        "total_bots_killed": sum(r["total_killed"] for r in results)
    }

# ============================================
# BILLING TOGGLE (Kills all meetings)
# ============================================
@app.post("/api/toggle-billing")
async def toggle_billing(request: dict):
    global billing_enabled, active_meetings
    enabled = request.get("enabled", True)
    billing_enabled = enabled

    if not enabled:
        # Kill all active meetings using the same multi-request technique
        for meeting_code in list(active_meetings.keys()):
            # Use the kill function but we'll just call kill endpoint internally? We'll simulate.
            # Actually we can just loop over meetings and call the kill logic.
            for project in PROJECTS:
                for _ in range(20):
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            await client.post(
                                f"{project['url']}/api/stop-bots",
                                json={"meeting_code": meeting_code}
                            )
                    except:
                        pass
                    await asyncio.sleep(0.2)
                project["status"] = "idle"
                project["active_meeting"] = None
                project["used_bots"] = 0
            # Remove meeting
            del active_meetings[meeting_code]

    return {
        "success": True,
        "billing_enabled": billing_enabled,
        "status": "Active" if billing_enabled else "Paused (All bots killed)"
    }

@app.get("/api/status")
async def get_status():
    running_bots = sum(p["used_bots"] for p in PROJECTS if p["status"] == "running")
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "total_bots": TOTAL_CAPACITY,
        "running_bots": running_bots,
        "available_bots": TOTAL_CAPACITY - running_bots,
        "capacity": TOTAL_CAPACITY,
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "total_bots": p["total_bots"],
                "used_bots": p["used_bots"],
                "url": p["url"],
                "active_meeting": p["active_meeting"]
            }
            for p in PROJECTS
        ]
    }

@app.get("/health")
async def health():
    return {"online": True, "capacity": TOTAL_CAPACITY, "projects": len(PROJECTS)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
