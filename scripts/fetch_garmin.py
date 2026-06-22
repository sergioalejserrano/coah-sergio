#!/usr/bin/env python3
"""
fetch_garmin.py — Descarga y cifra datos de Garmin para Coach Vegabikes.
"""
import os, sys, json, base64, secrets, datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import garminconnect

GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
DATA_PASSPHRASE = os.environ["DATA_PASSPHRASE"]
OUT_FILE        = "data/garmin.enc"
META_FILE       = "data/meta.json"


# ── Cifrado ────────────────────────────────────────────────────────────────
def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000
    )
    return kdf.derive(passphrase.encode())

def encrypt(data: str, passphrase: str) -> bytes:
    salt = secrets.token_bytes(16)
    iv   = secrets.token_bytes(12)
    key  = derive_key(passphrase, salt)
    ct   = AESGCM(key).encrypt(iv, data.encode(), None)
    return base64.b64encode(salt + iv + ct)


# ── Utilidades de fecha ────────────────────────────────────────────────────
def date_str(offset=0) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()

def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:
        print(f"  warn {fn.__name__}{args}: {e}")
        return default


# ── Payload ────────────────────────────────────────────────────────────────
def build_payload(api):
    today     = date_str(0)
    yesterday = date_str(1)

    # ── Body Battery 7 días ────────────────────────────────────────────────
    # FIX 2: usar el último valor no-cero de bb7 como BB actual
    bb_data = safe(api.get_body_battery, date_str(6), date_str(0), default=[])

    bb7        = []
    bb_current = 0

    if bb_data:
        print(f"  DEBUG bb_day[0] keys: {list(bb_data[0].keys())}")
        for k in ["date", "charged", "drained", "startTimestampGMT", "endTimestampGMT",
                  "startTimestampLocal", "endTimestampLocal"]:
            if k in bb_data[0]:
                print(f"    {k}: {bb_data[0][k]}")

        for day in bb_data:
            vals = day.get("bodyBatteryValuesArray") or []
            if vals:
                # cada entrada es [timestamp_ms, valor]; v[1] puede ser None → filtrar
                peak = max(
                    (v[1] for v in vals if v and len(v) > 1 and v[1] is not None),
                    default=0
                )
            else:
                peak = day.get("charged") or 0
            bb7.append(int(peak) if peak else 0)

        print(f"  bb7 peaks: {bb7}")

        # Último valor no-cero = BB más reciente disponible
        for v in reversed(bb7):
            if v and v > 0:
                bb_current = v
                break

    # ── Resting HR ────────────────────────────────────────────────────────
    # get_stats() no tiene la clave; probar get_heart_rates y get_rhr_day
    resting_hr = 0
    for d in [today, yesterday]:
        hr_data = safe(api.get_heart_rates, d, default=None)
        if hr_data:
            print(f"  DEBUG heart_rates keys ({d}): {list(hr_data.keys())[:10]}")
            resting_hr = hr_data.get("restingHeartRate") or 0
            if resting_hr:
                break
    if not resting_hr:
        for d in [today, yesterday]:
            rhr_fn = getattr(api, 'get_rhr_day', None)
            if rhr_fn is None:
                break
            rhr = safe(rhr_fn, d, default=None)
            if rhr and isinstance(rhr, dict):
                print(f"  DEBUG rhr_day keys ({d}): {list(rhr.keys())}")
                resting_hr = rhr.get("restingHeartRate") or rhr.get("value") or 0
                if resting_hr:
                    break

    print(f"  BB={bb_current} restingHR={resting_hr}")

    # ── Training Readiness ────────────────────────────────────────────────
    # FIX 1: datos pueden no estar disponibles aún hoy; probar hoy y ayer
    readiness = recovery_mins = hrv = sleep_score_from_readiness = 0

    for d in [today, yesterday]:
        raw = safe(api.get_training_readiness, d, default=[])
        print(f"  DEBUG readiness_raw ({d}): {type(raw).__name__} = {repr(raw)[:120]}")
        if isinstance(raw, list) and raw:
            item          = raw[0]
            readiness     = item.get("trainingReadinessScore") or item.get("score") or item.get("value") or 0
            recovery_mins = item.get("recoveryTime") or item.get("recoveryTimeInMinutes") or 0
            hrv           = item.get("avgHrv") or item.get("hrvWeeklyAverage") or 0
            sleep_score_from_readiness = item.get("sleepScore") or item.get("sleepScoreValue") or 0
            if readiness:
                break
        elif isinstance(raw, dict) and raw:
            readiness     = raw.get("trainingReadinessScore") or raw.get("score") or 0
            recovery_mins = raw.get("recoveryTime") or 0
            hrv           = raw.get("avgHrv") or 0
            sleep_score_from_readiness = raw.get("sleepScore") or 0
            if readiness:
                break

    print(f"  readiness={readiness} recovery={recovery_mins}min hrv={hrv}ms")

    # ── HRV dedicado (si readiness no lo trajo) ────────────────────────────
    if not hrv:
        for d in [today, yesterday]:
            hrv_data = safe(api.get_hrv_data, d, default=None)
            if hrv_data:
                summary = hrv_data.get("hrvSummary") or {}
                hrv = summary.get("weeklyAvg") or summary.get("lastNight") or 0
                if hrv:
                    break

    # ── Sleep ─────────────────────────────────────────────────────────────
    # FIX 4: el script pedía fecha+1 (mañana) → sueño vacío.
    # Garmin guarda el sueño bajo la fecha del DESPERTAR.
    # Probamos hoy, ayer, anteayer hasta encontrar sleepTimeSeconds > 0.
    sleep_hours = 0.0
    sleep_score = sleep_score_from_readiness

    for offset in range(0, 4):   # hoy, ayer, anteayer, 3 días atrás
        d         = date_str(offset)
        sleep_raw = safe(api.get_sleep_data, d, default={})
        if not sleep_raw:
            continue
        dto = sleep_raw.get("dailySleepDTO") or {}
        print(f"  DEBUG sleep_dto keys ({d}): {list(dto.keys())[:8]}")
        print(f"    calendarDate: {dto.get('calendarDate')}  sleepTimeSeconds: {dto.get('sleepTimeSeconds')}")

        secs = dto.get("sleepTimeSeconds") or 0
        if secs > 0:
            sleep_hours = round(secs / 3600, 2)
            sleep_score = dto.get("sleepScoreValue") or dto.get("sleepScore") or sleep_score_from_readiness
            break

    print(f"  sleep={sleep_hours}h score={sleep_score}")

    # ── Última actividad ──────────────────────────────────────────────────
    acts    = safe(api.get_activities, 0, 1, default=[])
    act_str = ""
    if acts:
        a = acts[0]
        print(f"  DEBUG activity[0] keys: {list(a.keys())}")
        for k in ["activityId", "activityName", "startTimeLocal", "startTimeGMT",
                  "activityType", "distance", "duration"]:
            if k in a:
                print(f"    {k}: {a[k]}")

        name    = a.get("activityName") or "Actividad"
        dist_km = round((a.get("distance") or 0) / 1000, 1)
        hr      = a.get("averageHR") or a.get("maxHR") or 0
        power   = a.get("avgPower") or a.get("averagePower") or 0
        act_str = f"{name} {dist_km}km FC{hr} {power}W"

    print(f"  actividad: {act_str}")

    payload = {
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
    }

    print(f"Readiness {payload['readiness']} · BB {payload['bb']} · actividad {payload.get('activity','—')}")
    return payload


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("Login Garmin con usuario y clave…")
    api = garminconnect.Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    print("Login con usuario+clave OK.")
    print("Sesión Garmin OK. Bajando datos…")

    payload = build_payload(api)

    os.makedirs("data", exist_ok=True)

    enc = encrypt(json.dumps(payload), DATA_PASSPHRASE)
    with open(OUT_FILE, "wb") as f:
        f.write(enc)
    print(f"Escrito {OUT_FILE} ({len(enc)} bytes cifrados). Listo.")

    with open(META_FILE, "w") as f:
        json.dump({"updated": payload["ts"]}, f)


if __name__ == "__main__":
    main()
