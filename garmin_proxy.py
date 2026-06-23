#!/usr/bin/env python3
"""
Garmin Proxy para Coach Vegabikes
Deploy en Render.com (free tier) — HTTPS automático

Variables de entorno en Render:
  ALLOWED_ORIGINS = https://sergioalejserrano.github.io  (o * para cualquier origen)
"""
import os, json, datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import garminconnect

app = FastAPI(title="Garmin Proxy — Coach Vegabikes")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGINS", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

def ds(offset=0):
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()

def safe(fn, *args, default=None):
    try: return fn(*args)
    except Exception as e:
        print(f"  warn {getattr(fn,'__name__',str(fn))}: {e}")
        return default

class CredsReq(BaseModel):
    email: str
    password: str

class WorkoutReq(BaseModel):
    email: str
    password: str
    name: str
    dot: str        # 'quality', 'z2', 'z1', 'race'
    duration: int   # minutes

def login(email, password):
    api = garminconnect.Garmin(email, password)
    api.login()
    return api

def build_payload(api):
    today, yesterday = ds(0), ds(1)

    # Body Battery
    bb_data = safe(api.get_body_battery, ds(6), today, default=[])
    bb7, bb_current = [], 0
    if bb_data:
        for day in bb_data:
            vals = day.get("bodyBatteryValuesArray") or []
            if vals:
                nn = [v[1] for v in vals if v and len(v)>1 and v[1] is not None]
                peak = max(nn, default=0)
                last_val = nn[-1] if nn else 0
            else:
                peak = day.get("charged") or 0
                last_val = 0
            bb7.append(int(peak) if peak else 0)
            if last_val: bb_current = int(last_val)

    # Resting HR
    resting_hr = 0
    for d in [today, yesterday]:
        hr = safe(api.get_heart_rates, d, default=None)
        if hr:
            resting_hr = hr.get("restingHeartRate") or 0
            if resting_hr: break

    # Readiness
    readiness = recovery_mins = hrv = sleep_score = 0
    for d in [today, yesterday]:
        raw = safe(api.get_training_readiness, d, default=[])
        if isinstance(raw, list) and raw:
            item = raw[0]
            readiness     = item.get("trainingReadinessScore") or 0
            recovery_mins = item.get("recoveryTime") or 0
            hrv           = item.get("avgHrv") or item.get("hrvWeeklyAverage") or 0
            sleep_score   = item.get("sleepScore") or item.get("sleepScoreValue") or 0
            if readiness: break
    if not hrv:
        for d in [today, yesterday]:
            hd = safe(api.get_hrv_data, d, default=None)
            if hd:
                sm = hd.get("hrvSummary") or {}
                hrv = sm.get("weeklyAvg") or sm.get("lastNight") or 0
                if hrv: break

    # Sleep
    sleep_hours = 0.0
    for offset in range(4):
        sr = safe(api.get_sleep_data, ds(offset), default={})
        if not sr: continue
        dto  = sr.get("dailySleepDTO") or {}
        secs = dto.get("sleepTimeSeconds") or 0
        if secs and secs > 0:
            sleep_hours = round(secs/3600, 2)
            sleep_score = sleep_score or dto.get("sleepScoreValue") or dto.get("sleepScore") or 0
            break

    # Training load
    aerobic_high = aerobic_low = anaerobic = 0
    ah_min, ah_max = 700, 1100
    al_min, al_max = 600, 900
    an_min, an_max = 200, 500
    ts = safe(api.get_training_status, yesterday, default=None)
    if isinstance(ts, dict):
        lb = ts.get("mostRecentTrainingLoadBalance") or {}
        tlm = lb.get("metricsTrainingLoadBalanceDTOMap") or {}
        if tlm:
            inner = next(iter(tlm.values()), {})
            if isinstance(inner, dict):
                aerobic_high = inner.get("monthlyLoadAerobicHigh") or 0
                aerobic_low  = inner.get("monthlyLoadAerobicLow")  or 0
                anaerobic    = inner.get("monthlyLoadAnaerobic")   or 0
                ah_min = inner.get("monthlyLoadAerobicHighTargetMin") or ah_min
                ah_max = inner.get("monthlyLoadAerobicHighTargetMax") or ah_max
                al_min = inner.get("monthlyLoadAerobicLowTargetMin")  or al_min
                al_max = inner.get("monthlyLoadAerobicLowTargetMax")  or al_max
                an_min = inner.get("monthlyLoadAnaerobicTargetMin")   or an_min
                an_max = inner.get("monthlyLoadAnaerobicTargetMax")   or an_max

    # Last activity
    acts = safe(api.get_activities, 0, 1, default=[])
    act_str, tss, norm_pwr, vo2max = "", 0, 0, 0
    if acts:
        a = acts[0]
        dist_km = round((a.get("distance") or 0)/1000, 1)
        hr_a    = a.get("averageHR") or a.get("maxHR") or 0
        power   = a.get("avgPower") or 0
        tss     = int(a.get("trainingStressScore") or 0)
        norm_pwr= int(a.get("normPower") or 0)
        vo2max  = float(a.get("vO2MaxValue") or 0)
        act_str = f"{a.get('activityName','')} {dist_km}km FC{hr_a} {power}W"

    return {
        "ts":         datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readiness":  int(readiness),
        "recovery":   int(recovery_mins),
        "hrv":        float(hrv),
        "sleep":      sleep_hours,
        "sleepScore": int(sleep_score) if sleep_score else 0,
        "bb":         int(bb_current),
        "bb7":        bb7,
        "restingHR":  int(resting_hr),
        "activity":   act_str,
        "tss":        tss,
        "normPower":  norm_pwr,
        "vo2max":     vo2max,
        "load": {
            "aerobicHigh": int(aerobic_high), "aerobicLow": int(aerobic_low),
            "anaerobic": int(anaerobic),
            "ahMin":ah_min,"ahMax":ah_max,"alMin":al_min,"alMax":al_max,
            "anMin":an_min,"anMax":an_max,
        },
    }

def build_garmin_workout(name, dot, duration_mins):
    """Build Garmin Connect workout JSON for structured workouts."""
    is_quality = dot == 'quality'
    steps = []
    if is_quality:
        raw = [
            ("Calentamiento", 900, 100, 160),
            ("Intervalo 1",   480, 175, 190), ("Recuperacion", 240, 90, 110),
            ("Intervalo 2",   480, 175, 190), ("Recuperacion", 240, 90, 110),
            ("Intervalo 3",   480, 175, 190), ("Recuperacion", 240, 90, 110),
            ("Intervalo 4",   480, 175, 190),
            ("Enfriamiento",  900, 100, 130),
        ]
    else:
        raw = [
            ("Calentamiento",   600, 100, 140),
            ("Bloque Z2",      2400, 140, 165),
            ("Enfriamiento",    600,  90, 120),
        ]
    for i, (n, secs, lo, hi) in enumerate(raw):
        steps.append({
            "type": "WorkoutStep",
            "stepOrder": i + 1,
            "stepType": {"stepTypeId": 3 if i == 0 else (5 if i == len(raw)-1 else 1), "stepTypeKey": "warmup" if i == 0 else ("cooldown" if i == len(raw)-1 else "interval")},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": secs,
            "targetType": {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone"},
            "targetValueOne": lo,
            "targetValueTwo": hi,
        })
    return {
        "workoutName": name,
        "description": "Generado por Coach Vegabikes",
        "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
            "workoutSteps": steps,
        }]
    }

@app.get("/health")
def health():
    return {"status": "ok", "service": "garmin-proxy-coach-vegabikes"}

@app.post("/data")
async def get_data(req: CredsReq):
    try:
        api = login(req.email, req.password)
        payload = build_payload(api)
        return {"success": True, "payload": payload}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/workout")
async def add_workout(req: WorkoutReq):
    try:
        api = login(req.email, req.password)
        wk_json = build_garmin_workout(req.name, req.dot, req.duration)
        # POST to Garmin workout service via authenticated session
        result = api.garth.post("connectapi", "/workout-service/workout", json=wk_json).json()
        wid = result.get("workoutId")
        return {"success": True, "workout_id": wid, "message": f"Entrenamiento '{req.name}' agregado a Garmin Connect (ID: {wid})"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
