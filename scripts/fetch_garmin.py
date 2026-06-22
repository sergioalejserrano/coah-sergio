#!/usr/bin/env python3
"""
fetch_garmin.py — Descarga y cifra datos de Garmin para Coach Vegabikes.
Versión final: todos los campos incluyendo load de entrenamiento, TSS y NP.
"""
import os, json, base64, secrets, datetime
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


# ── Fechas ─────────────────────────────────────────────────────────────────
def date_str(offset: int = 0) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()

def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Llamada segura a la API ────────────────────────────────────────────────
def safe(fn, *args, default=None):
    """Llama fn(*args); si lanza cualquier excepción retorna default y loguea."""
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception as e:
        print(f"  warn {getattr(fn,'__name__',str(fn))}{args}: {e}")
        return default


# ── Payload principal ──────────────────────────────────────────────────────
def build_payload(api):
    today     = date_str(0)
    yesterday = date_str(1)

    # ── Body Battery 7 días ─────────────────────────────────────────────────
    bb_data = safe(api.get_body_battery, date_str(6), today, default=[])
    bb7 = []
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
                non_none = [v[1] for v in vals if v and len(v) > 1 and v[1] is not None]
                peak     = max(non_none, default=0)
                last_val = non_none[-1] if non_none else 0
            else:
                peak     = day.get("charged") or 0
                last_val = 0
            bb7.append(int(peak) if peak else 0)
            # bb_current = ultimo valor registrado del dia mas reciente con datos
            if last_val:
                bb_current = int(last_val)

        print(f"  bb7 peaks: {bb7}  bb_current_lastval: {bb_current}")

    # ── Resting HR ─────────────────────────────────────────────────────────
    resting_hr = 0
    for d in [today, yesterday]:
        hr_data = safe(api.get_heart_rates, d, default=None)
        if hr_data:
            print(f"  DEBUG heart_rates keys ({d}): {list(hr_data.keys())[:12]}")
            resting_hr = hr_data.get("restingHeartRate") or 0
            if resting_hr:
                break
    if not resting_hr:
        rhr_fn = getattr(api, "get_rhr_day", None)
        for d in [today, yesterday]:
            rhr = safe(rhr_fn, d, default=None)
            if isinstance(rhr, dict):
                print(f"  DEBUG rhr_day keys ({d}): {list(rhr.keys())}")
                resting_hr = rhr.get("restingHeartRate") or rhr.get("value") or 0
                if resting_hr:
                    break

    print(f"  BB={bb_current} restingHR={resting_hr}")

    # ── Training Readiness ──────────────────────────────────────────────────
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

    # ── HRV dedicado ────────────────────────────────────────────────────────
    if not hrv:
        for d in [today, yesterday]:
            hrv_data = safe(api.get_hrv_data, d, default=None)
            if hrv_data:
                summary = hrv_data.get("hrvSummary") or {}
                hrv = summary.get("weeklyAvg") or summary.get("lastNight") or 0
                if hrv:
                    break

    # ── Sleep ───────────────────────────────────────────────────────────────
    sleep_hours = 0.0
    sleep_score = sleep_score_from_readiness

    for offset in range(0, 4):
        d         = date_str(offset)
        sleep_raw = safe(api.get_sleep_data, d, default={})
        if not sleep_raw:
            continue
        dto  = sleep_raw.get("dailySleepDTO") or {}
        secs = dto.get("sleepTimeSeconds") or 0
        print(f"  DEBUG sleep_dto ({d}): calendarDate={dto.get('calendarDate')} sleepTimeSeconds={secs}")
        if secs and secs > 0:
            sleep_hours = round(secs / 3600, 2)
            sleep_score = dto.get("sleepScoreValue") or dto.get("sleepScore") or sleep_score_from_readiness
            break

    print(f"  sleep={sleep_hours}h score={sleep_score}")

    # -- Training Load Balance (foco de carga) ---
    # get_training_status devuelve mostRecentTrainingLoadBalance con la data
    aerobic_high = aerobic_low = anaerobic = 0
    ah_min, ah_max = 700, 1100
    al_min, al_max = 600, 900
    an_min, an_max = 200, 500

    ts_data = safe(api.get_training_status, yesterday, default=None)
    if isinstance(ts_data, dict):
        print(f"  DEBUG training_status keys: {list(ts_data.keys())}")
        load_balance = ts_data.get("mostRecentTrainingLoadBalance") or {}
        if load_balance:
            print(f"  DEBUG load_balance keys: {list(load_balance.keys())}")
            # La data real esta en metricsTrainingLoadBalanceDTOMap
            tlb_map = load_balance.get("metricsTrainingLoadBalanceDTOMap") or {}
            if tlb_map:
                print(f"  DEBUG tlb_map keys: {list(tlb_map.keys())}")
                # Intentar extraer en orden: ALL > primera clave > suma
                inner = (tlb_map.get("ALL") or tlb_map.get("all")
                         or tlb_map.get("-1") or tlb_map.get("0")
                         or next(iter(tlb_map.values()), {}))
                print(f"  DEBUG inner keys: {list(inner.keys()) if isinstance(inner, dict) else inner}")
                if isinstance(inner, dict):
                    aerobic_high = (inner.get("aerobicHighLoad")
                                    or inner.get("aerobicHigh") or 0)
                    aerobic_low  = (inner.get("aerobicLowLoad")
                                    or inner.get("aerobicLow") or 0)
                    anaerobic    = (inner.get("anaerobicLoad")
                                    or inner.get("anaerobic") or 0)
                    tgt_ah = inner.get("aerobicHighLoadTarget") or {}
                    tgt_al = inner.get("aerobicLowLoadTarget") or {}
                    tgt_an = inner.get("anaerobicLoadTarget") or {}
                    ah_min = tgt_ah.get("minValue") or tgt_ah.get("min") or ah_min
                    ah_max = tgt_ah.get("maxValue") or tgt_ah.get("max") or ah_max
                    al_min = tgt_al.get("minValue") or tgt_al.get("min") or al_min
                    al_max = tgt_al.get("maxValue") or tgt_al.get("max") or al_max
                    an_min = tgt_an.get("minValue") or tgt_an.get("min") or an_min
                    an_max = tgt_an.get("maxValue") or tgt_an.get("max") or an_max
            else:
                print("  warn: metricsTrainingLoadBalanceDTOMap vacio")
        else:
            print("  warn: mostRecentTrainingLoadBalance vacio en training_status")

    print(f"  load aerobicHigh={aerobic_high} aerobicLow={aerobic_low} anaerobic={anaerobic}")

    # ── Última actividad ────────────────────────────────────────────────────
    acts = safe(api.get_activities, 0, 1, default=[])
    act_str  = ""
    tss      = 0
    norm_pwr = 0
    vo2max   = 0

    if acts:
        a = acts[0]
        print(f"  DEBUG activity[0] keys (primeras 10): {list(a.keys())[:10]}")
        for k in ["activityId", "activityName", "startTimeLocal", "startTimeGMT",
                  "activityType", "distance", "duration"]:
            if k in a:
                print(f"    {k}: {a[k]}")

        name     = a.get("activityName") or "Actividad"
        dist_km  = round((a.get("distance") or 0) / 1000, 1)
        hr       = a.get("averageHR") or a.get("maxHR") or 0
        power    = a.get("avgPower") or a.get("averagePower") or 0
        tss      = int(a.get("trainingStressScore") or 0)
        norm_pwr = int(a.get("normPower") or a.get("normalizedPower") or 0)
        vo2max   = float(a.get("vO2MaxValue") or 0)
        act_str  = f"{name} {dist_km}km FC{hr} {power}W"

    print(f"  actividad: {act_str}  TSS={tss} NP={norm_pwr}W VO2max={vo2max}")

    payload = {
        "ts":         now_utc(),
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
            "aerobicHigh": int(aerobic_high),
            "aerobicLow":  int(aerobic_low),
            "anaerobic":   int(anaerobic),
            "ahMin": ah_min, "ahMax": ah_max,
            "alMin": al_min, "alMax": al_max,
            "anMin": an_min, "anMax": an_max,
        },
    }

    print(f"Readiness {payload['readiness']} · BB {payload['bb']} · "
          f"sleep {payload['sleep']}h · restingHR {payload['restingHR']} · "
          f"actividad {payload.get('activity','—')}")
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
