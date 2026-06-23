#!/usr/bin/env python3
"""
Garmin Proxy para Coach Vegabikes
- Reintentos automáticos en errores 429
- Caché de tokens en /tmp para evitar login repetido
- Mensajes de error detallados
"""
import os, datetime, time, json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import garminconnect

app = FastAPI(title="Garmin Proxy — Coach Vegabikes")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGINS", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

TOKEN_DIR = Path("/tmp/garmin_tokens")

def ds(offset=0):
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()

def safe(fn, *args, default=None):
    try: return fn(*args)
    except Exception as e:
        print(f"  warn {getattr(fn,'__name__',str(fn))}: {e}")
        return default

def get_token_path(email: str) -> Path:
    key = email.replace("@","_").replace(".","_")
    return TOKEN_DIR / key

def login_with_cache(email: str, password: str):
    """Login con caché de tokens para evitar 429 por login repetido."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_path = get_token_path(email)

    # Intentar reusar tokens guardados
    if token_path.exists():
        try:
            api = garminconnect.Garmin(email, password)
            api.garth.load(str(token_path))
            # Validar que el token sigue funcionando
            api.get_user_summary(ds(0))
            print(f"  Token cache hit for {email}")
            return api
        except Exception as e:
            print(f"  Token cache invalid: {e} — logging in fresh")
            token_path.unlink(missing_ok=True)

    # Login fresco con reintentos
    last_err = None
    for attempt in range(3):
        try:
            api = garminconnect.Garmin(email, password)
            api.login()
            # Guardar token para próximas llamadas
            api.garth.dump(str(token_path))
            print(f"  Fresh login OK — token cached (attempt {attempt+1})")
            return api
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"  429 rate limit, waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
            else:
                # Error que no es de rate limiting — no reintentar
                print(f"  Login error (no retry): {e}")
                raise

    raise Exception(f"Login fallido después de 3 intentos: {last_err}")


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
                peak, last_val = max(nn, default=0), (nn[-1] if nn else 0)
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
        lb  = ts.get("mostRecentTrainingLoadBalance") or {}
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
        dist_km  = round((a.get("distance") or 0)/1000, 1)
        hr_a     = a.get("averageHR") or a.get("maxHR") or 0
        power    = a.get("avgPower") or 0
        tss      = int(a.get("trainingStressScore") or 0)
        norm_pwr = int(a.get("normPower") or 0)
        vo2max   = float(a.get("vO2MaxValue") or 0)
        act_str  = f"{a.get('activityName','')} {dist_km}km FC{hr_a} {power}W"

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
            "ahMin": ah_min, "ahMax": ah_max,
            "alMin": al_min, "alMax": al_max,
            "anMin": an_min, "anMax": an_max,
        },
    }

def build_garmin_workout(name, dot, duration_mins):
    is_quality = dot == "quality"
    raw = ([
        ("Calentamiento", 900), ("Intervalo 1", 480), ("Rec", 240),
        ("Intervalo 2", 480), ("Rec", 240), ("Intervalo 3", 480), ("Rec", 240),
        ("Intervalo 4", 480), ("Enfriamiento", 900),
    ] if is_quality else [
        ("Calentamiento", 600), ("Bloque Z2", 2400), ("Enfriamiento", 600),
    ])
    steps = []
    for i, (n, secs) in enumerate(raw):
        stype = "warmup" if i == 0 else ("cooldown" if i == len(raw)-1 else "interval")
        stid  = 3 if i == 0 else (5 if i == len(raw)-1 else 1)
        steps.append({
            "type": "WorkoutStep",
            "stepOrder": i + 1,
            "stepType": {"stepTypeId": stid, "stepTypeKey": stype},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": secs,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
        })
    return {
        "workoutName": name,
        "description": "Generado por Coach Vegabikes",
        "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
            "workoutSteps": steps,
        }],
    }

# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "Garmin Proxy — Coach Vegabikes",
        "status": "running",
        "endpoints": {
            "health":  "GET  /health",
            "data":    "POST /data    {email, password}",
            "workout": "POST /workout {email, password, name, dot, duration}",
            "clear":   "POST /clear-cache {email}"
        }
    }

@app.get("/health")
def health():
    return {"status": "ok", "service": "garmin-proxy-coach-vegabikes"}

@app.post("/data")
async def get_data(request: Request):
    try:
        body     = await request.json()
        email    = body.get("email", "").strip()
        password = body.get("password", "")
        if not email or not password:
            raise ValueError("Faltan email o password")
        print(f"GET /data for {email}")
        api     = login_with_cache(email, password)
        payload = build_payload(api)
        print(f"  payload OK: readiness={payload['readiness']} bb={payload['bb']}")
        return {"success": True, "payload": payload}
    except Exception as e:
        err = str(e)
        print(f"  ERROR: {err}")
        raise HTTPException(status_code=400, detail=err)

@app.post("/workout")
async def add_workout(request: Request):
    try:
        body     = await request.json()
        email    = body.get("email", "").strip()
        password = body.get("password", "")
        name     = body.get("name", "Entrenamiento KICKR")
        dot      = body.get("dot", "z2")
        duration = int(body.get("duration", 60))
        if not email or not password:
            raise ValueError("Faltan email o password")
        api     = login_with_cache(email, password)
        wk_json = build_garmin_workout(name, dot, duration)
        result  = api.garth.post("connectapi", "/workout-service/workout", json=wk_json).json()
        wid     = result.get("workoutId")
        return {"success": True, "workout_id": wid,
                "message": f"Entrenamiento '{name}' agregado a Garmin Connect (ID: {wid})"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/clear-cache")
async def clear_cache(request: Request):
    """Elimina los tokens cacheados para forzar un login fresco."""
    try:
        body  = await request.json()
        email = body.get("email", "").strip()
        if email:
            path = get_token_path(email)
            path.unlink(missing_ok=True)
            return {"success": True, "message": f"Cache limpiado para {email}"}
        # Limpiar todo
        for f in TOKEN_DIR.glob("*"):
            f.unlink()
        return {"success": True, "message": "Cache completo limpiado"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
