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

def decrypt(b64: bytes, passphrase: str) -> str:
    raw  = base64.b64decode(b64)
    salt, iv, ct = raw[:16], raw[16:28], raw[28:]
    key  = derive_key(passphrase, salt)
    return AESGCM(key).decrypt(iv, ct, None).decode()


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
                    # Claves confirmadas por el log del 22 jun 2026
                    aerobic_high = inner.get("monthlyLoadAerobicHigh") or 0
                    aerobic_low  = inner.get("monthlyLoadAerobicLow")  or 0
                    anaerobic    = inner.get("monthlyLoadAnaerobic")   or 0
                    ah_min = inner.get("monthlyLoadAerobicHighTargetMin") or ah_min
                    ah_max = inner.get("monthlyLoadAerobicHighTargetMax") or ah_max
                    al_min = inner.get("monthlyLoadAerobicLowTargetMin")  or al_min
                    al_max = inner.get("monthlyLoadAerobicLowTargetMax")  or al_max
                    an_min = inner.get("monthlyLoadAnaerobicTargetMin")   or an_min
                    an_max = inner.get("monthlyLoadAnaerobicTargetMax")   or an_max
                    print(f"  DEBUG load: aeroHigh={aerobic_high} aeroLow={aerobic_low} anaerobic={anaerobic}")
                    print(f"  DEBUG ranges: ah={ah_min}-{ah_max} al={al_min}-{al_max} an={an_min}-{an_max}")
            else:
                print("  warn: metricsTrainingLoadBalanceDTOMap vacio")
        else:
            print("  warn: mostRecentTrainingLoadBalance vacio en training_status")

    print(f"  load aerobicHigh={aerobic_high} aerobicLow={aerobic_low} anaerobic={anaerobic}")

    # ── Última actividad ────────────────────────────────────────────────────
    acts = safe(api.get_activities, 0, 1, default=[])
    act_str  = ""
    act_date = ""
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
        # Fecha/hora de inicio de la actividad (local) para mostrar en la app.
        act_date = (a.get("startTimeLocal") or a.get("startTimeGMT") or "")

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
        "activityDate": act_date,
        "tss":        tss,
        "normPower":  norm_pwr,
        "avgPower":   int(power or 0),
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


# ── Histórico (serie temporal para PMC y tendencias) ────────────────────────
# Estrategia barata + incremental:
#   · TSS diario de ~1 año: una sola llamada en bloque a get_activities (sirve
#     para CTL/ATL/TSB que se calculan en la app).
#   · Wellness (HRV/FC reposo/sueño/peso): solo los dias que faltan respecto al
#     histórico previo, con un tope por corrida para no abusar de la API.
# Sergio tiene ~2 años en Garmin (~1 con el Fénix 7X). Bajamos histórico profundo:
#   · TSS: 2 años de una (barato, por rango de fechas).
#   · Wellness: ventana de 2 años pero llenada de a poco (tope por corrida).
#     Con el cron cada 4h se completa solo en ~1-2 semanas sin abusar de la API.
TSS_HISTORY_DAYS   = 760  # ~2 años de carga para el PMC
WELLNESS_BACKFILL  = 760  # objetivo de wellness hacia atrás
MAX_WELLNESS_FETCH = 30   # tope de días nuevos de wellness por corrida (anti-429)

def load_prev_history():
    """Lee el garmin.enc previo (si existe) y devuelve su lista 'history'."""
    if not os.path.exists(OUT_FILE):
        return []
    try:
        with open(OUT_FILE, "rb") as f:
            prev = json.loads(decrypt(f.read(), DATA_PASSPHRASE))
        h = prev.get("history") or []
        print(f"  histórico previo: {len(h)} dias")
        return h
    except Exception as e:
        print(f"  warn no se pudo leer histórico previo: {e}")
        return []

def build_history(api, prev_history, today_payload):
    import collections
    today = datetime.date.today()
    hist = {h["d"]: dict(h) for h in (prev_history or [])
            if isinstance(h, dict) and h.get("d")}

    # ── TSS diario por rango de fechas (cubre ~2 años de una) ───────────────
    start = (today - datetime.timedelta(days=TSS_HISTORY_DAYS)).isoformat()
    acts = safe(api.get_activities_by_date, start, today.isoformat(), default=None)
    if not acts:   # fallback al endpoint por cantidad
        acts = safe(api.get_activities, 0, 1000, default=[]) or []
    tss_by_date = collections.defaultdict(float)
    cutoff = start
    for a in (acts or []):
        st = (a.get("startTimeLocal") or a.get("startTimeGMT") or "")[:10]
        if not st or st < cutoff:
            continue
        tss_by_date[st] += float(a.get("trainingStressScore") or 0)
    for d, tss in tss_by_date.items():
        hist.setdefault(d, {"d": d})["tss"] = round(tss, 1)
    print(f"  TSS: {len(tss_by_date)} dias con actividad")

    # ── Wellness incremental (solo dias no consultados, con tope) ───────────
    # Marcamos cada día con "_chk" tras consultarlo una vez, así no re-pedimos
    # días viejos sin datos (anteriores al reloj) en cada corrida. Hoy y ayer
    # siempre se refrescan.
    fetched = 0
    for off in range(0, WELLNESS_BACKFILL):
        d = (today - datetime.timedelta(days=off)).isoformat()
        row = hist.setdefault(d, {"d": d})
        if off >= 2 and row.get("_chk"):
            continue   # día viejo ya consultado -> no re-pedir
        if fetched >= MAX_WELLNESS_FETCH and off >= 2:
            continue   # respetamos el tope salvo hoy/ayer
        fetched += 1
        row["_chk"] = 1
        hr = safe(api.get_heart_rates, d, default=None)
        if isinstance(hr, dict) and hr.get("restingHeartRate"):
            row["rhr"] = int(hr["restingHeartRate"])
        sl  = safe(api.get_sleep_data, d, default={}) or {}
        dto = sl.get("dailySleepDTO") or {}
        secs = dto.get("sleepTimeSeconds") or 0
        if secs:
            row["sleep"] = round(secs / 3600, 2)
        hv = safe(api.get_hrv_data, d, default=None)
        if isinstance(hv, dict):
            summ = hv.get("hrvSummary") or {}
            val = summ.get("lastNightAvg") or summ.get("weeklyAvg")
            if val:
                row["hrv"] = float(val)
    print(f"  wellness: {fetched} dias consultados")

    # ── Peso (bulk, best-effort) ────────────────────────────────────────────
    try:
        start = (today - datetime.timedelta(days=WELLNESS_BACKFILL)).isoformat()
        bc = safe(api.get_body_composition, start, today.isoformat(), default=None)
        items = (bc or {}).get("dateWeightList") if isinstance(bc, dict) else None
        for w in (items or []):
            raw_d = w.get("calendarDate") or w.get("date")
            kg = w.get("weight")
            if not kg:
                continue
            if isinstance(raw_d, (int, float)):   # epoch ms
                raw_d = datetime.date.fromtimestamp(raw_d / 1000).isoformat()
            d = str(raw_d)[:10]
            if d:
                hist.setdefault(d, {"d": d})["weight"] = round(float(kg) / 1000, 1)
    except Exception as e:
        print(f"  warn peso histórico: {e}")

    # ── Snapshot de hoy desde el payload (no perder lo recién bajado) ────────
    td = today.isoformat()
    row = hist.setdefault(td, {"d": td})
    if today_payload.get("readiness"): row["readiness"] = today_payload["readiness"]
    if today_payload.get("hrv"):       row.setdefault("hrv", float(today_payload["hrv"]))
    if today_payload.get("restingHR"): row.setdefault("rhr", int(today_payload["restingHR"]))
    if today_payload.get("sleep"):     row.setdefault("sleep", today_payload["sleep"])

    return [hist[k] for k in sorted(hist.keys())]


# ── Potencia: tiempo en zonas + polarización ────────────────────────────────
POLAR_RIDES = 8   # salidas recientes para el análisis de polarización

def _zone_secs(api, act_id):
    """(tipo, {zona: segundos}) probando potencia y luego FC."""
    for kind, fn in (("power", api.get_activity_power_in_timezones),
                     ("hr",    api.get_activity_hr_in_timezones)):
        data = safe(fn, act_id, default=None)
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = (data.get("powerTimeInZones") or data.get("hrTimeInZones")
                    or next((v for v in data.values() if isinstance(v, list)), None))
        z = {}
        for r in (rows or []):
            try:
                zn   = int(r.get("zoneNumber"))
                secs = float(r.get("secsInZone") or 0)
                if secs:
                    z[zn] = z.get(zn, 0.0) + secs
            except Exception:
                continue
        if z:
            return kind, z
    return None, {}

def build_power(api):
    """Zonas de la última salida + polarización de las últimas N salidas."""
    import collections
    acts = safe(api.get_activities, 0, 30, default=[]) or []
    cyc = [a for a in acts
           if (a.get("activityType") or {}).get("parentTypeId") == 2
           or "cycling" in ((a.get("activityType") or {}).get("typeKey") or "")]
    out = {}
    if not cyc:
        return out
    # Última salida
    k, z = _zone_secs(api, cyc[0].get("activityId"))
    if z:
        out["lastZonesType"] = k
        out["lastZones"] = [{"z": zn, "secs": round(s)} for zn, s in sorted(z.items())]
    # Polarización (no mezclamos potencia y FC)
    agg = collections.defaultdict(float); ptype = None; rides = 0
    for a in cyc[:POLAR_RIDES]:
        k, z = _zone_secs(api, a.get("activityId"))
        if not z:
            continue
        if ptype is None:
            ptype = k
        if k != ptype:
            continue
        for zn, s in z.items():
            agg[zn] += s
        rides += 1
    if agg:
        out["polarType"] = ptype
        out["polarSecs"] = {int(k): round(v) for k, v in agg.items()}
        out["polarRides"] = rides
    print(f"  power: {len(out.get('lastZones',[]))} zonas última · polar {out.get('polarRides',0)} salidas")
    return out


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("Login Garmin con usuario y clave…")
    api = garminconnect.Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    print("Login con usuario+clave OK.")
    print("Sesión Garmin OK. Bajando datos…")

    payload = build_payload(api)

    # ── Potencia (NO fatal) ─────────────────────────────────────────────────
    try:
        payload["power"] = build_power(api)
    except Exception as e:
        print(f"  warn build_power falló (no fatal): {e}")

    # ── Histórico (NO fatal: si algo falla, igual escribimos el snapshot) ────
    prev_history = load_prev_history()
    try:
        payload["history"] = build_history(api, prev_history, payload)
        print(f"  history: {len(payload['history'])} dias en total")
    except Exception as e:
        print(f"  warn build_history falló (no fatal): {e}")
        if prev_history:
            payload["history"] = prev_history   # conservamos lo que ya teníamos

    os.makedirs("data", exist_ok=True)

    enc = encrypt(json.dumps(payload), DATA_PASSPHRASE)
    with open(OUT_FILE, "wb") as f:
        f.write(enc)
    print(f"Escrito {OUT_FILE} ({len(enc)} bytes cifrados). Listo.")

    with open(META_FILE, "w") as f:
        json.dump({"updated": payload["ts"]}, f)


if __name__ == "__main__":
    main()
