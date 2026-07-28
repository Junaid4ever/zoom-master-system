from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import os
import random
from typing import List, Optional
from datetime import datetime  # <-- YEH ADD KARO

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
PROJECTS = []
for i in range(1, 11):
    PROJECTS.append({
        "id": i,
        "name": f"zoom-bot-{i}",
        "url": f"https://zoom-bot-{i}.railway.app",
        "replicas": 42,
        "bots_per_replica": 5,
        "total_bots": 210,
        "status": "stopped"
    })

TOTAL_CAPACITY = sum(p["total_bots"] for p in PROJECTS)

# ============================================
# INDIAN NAMES
# ============================================
INDIAN_FIRST_NAMES = [
    'Aarav', 'Vivaan', 'Aditya', 'Vihaan', 'Arjun', 'Reyansh', 'Ayaan', 
    'Krishna', 'Ishaan', 'Shaurya', 'Rahul', 'Rohan', 'Priya', 'Ananya',
    'Diya', 'Saanvi', 'Aadhya', 'Kavya', 'Riya', 'Anika', 'Amit', 'Rajesh',
    'Sneha', 'Pooja', 'Neha', 'Vikram', 'Karan', 'Manish', 'Suresh', 'Deepak'
]

INDIAN_LAST_NAMES = [
    'Sharma', 'Verma', 'Patel', 'Kumar', 'Singh', 'Reddy', 'Gupta', 'Joshi',
    'Malhotra', 'Mehta', 'Chopra', 'Khanna', 'Agarwal', 'Jain', 'Saxena',
    'Bansal', 'Srivastava', 'Mishra', 'Pandey', 'Rao', 'Desai', 'Nair'
]

ENGLISH_FIRST_NAMES = [
    'James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard', 'Joseph',
    'Thomas', 'Charles', 'Christopher', 'Daniel', 'Matthew', 'Anthony', 'Donald'
]

ENGLISH_LAST_NAMES = [
    'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis',
    'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Wilson', 'Anderson', 'Thomas'
]

def generate_names(name_type, custom_names=None, count=1):
    names = []
    if name_type == "indian":
        for _ in range(count):
            first = random.choice(INDIAN_FIRST_NAMES)
            last = random.choice(INDIAN_LAST_NAMES)
            names.append(f"{first} {last}")
    elif name_type == "english":
        for _ in range(count):
            first = random.choice(ENGLISH_FIRST_NAMES)
            last = random.choice(ENGLISH_LAST_NAMES)
            names.append(f"{first} {last}")
    elif name_type == "custom" and custom_names:
        names = custom_names[:count]
        while len(names) < count:
            first = random.choice(INDIAN_FIRST_NAMES)
            last = random.choice(INDIAN_LAST_NAMES)
            names.append(f"{first} {last}")
    return names[:count]

# ============================================
# MODELS
# ============================================
class StartBotsRequest(BaseModel):
    meeting_code: str
    passcode: str = ""
    duration_minutes: int = 10
    name_type: str = "indian"
    custom_names: Optional[List[str]] = None
    projects: Optional[List[int]] = None

class ToggleBillingRequest(BaseModel):
    enabled: bool

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
        raise HTTPException(status_code=403, detail="Billing is disabled")
    
    projects_to_use = PROJECTS
    if request.projects:
        projects_to_use = [p for p in PROJECTS if p["id"] in request.projects]
    
    total_bots = sum(p["total_bots"] for p in projects_to_use)
    names = generate_names(request.name_type, request.custom_names, total_bots)
    
    results = []
    name_index = 0
    
    for project in projects_to_use:
        try:
            project_names = names[name_index:name_index + project["total_bots"]]
            name_index += project["total_bots"]
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{project['url']}/api/start-bots",
                    json={
                        "meeting_code": request.meeting_code,
                        "passcode": request.passcode,
                        "bot_count": 5,
                        "duration_minutes": request.duration_minutes,
                        "names": project_names[:5]
                    }
                )
                
                if response.status_code == 200:
                    project["status"] = "running"
                    results.append({"project": project["name"], "status": "success", "bots": project["total_bots"]})
                else:
                    results.append({"project": project["name"], "status": "failed"})
        except Exception as e:
            results.append({"project": project["name"], "status": "failed", "error": str(e)})
    
    if request.meeting_code not in active_meetings:
        active_meetings[request.meeting_code] = {
            "meeting_code": request.meeting_code,
            "started_at": datetime.now().isoformat(),
            "total_bots": total_bots,
            "projects": projects_to_use,
            "status": "running"
        }
    
    return {
        "success": True,
        "message": f"Started {total_bots} bots",
        "total_bots": total_bots,
        "results": results
    }

@app.post("/api/toggle-billing")
async def toggle_billing(request: ToggleBillingRequest):
    global billing_enabled
    billing_enabled = request.enabled
    
    if not billing_enabled:
        for meeting_code, meeting in active_meetings.items():
            if meeting["status"] == "running":
                meeting["status"] = "paused"
    
    return {
        "success": True,
        "billing_enabled": billing_enabled
    }

@app.get("/api/status")
async def get_status():
    return {
        "billing_enabled": billing_enabled,
        "active_meetings": active_meetings,
        "projects": [{"name": p["name"], "status": p["status"], "total_bots": p["total_bots"]} for p in PROJECTS]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
