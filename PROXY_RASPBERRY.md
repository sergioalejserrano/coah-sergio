# Garmin Proxy en Raspberry Pi (Opción 1)

Guía para correr `garmin_proxy.py` en una Raspberry Pi en tu casa y exponerlo a
internet con una URL estable. Así tus amigos abren tu página, escriben su
usuario y contraseña de Garmin, y funciona — **sin el error 429** (Garmin no
bloquea IPs residenciales como sí bloquea las de servidores en la nube).

Resumen del flujo final:

```
amigo abre tu página → escribe email + clave de Garmin
   → la página llama a TU proxy (Pi en tu casa, IP residencial)
   → el proxy habla con Garmin → devuelve los datos al navegador del amigo
```

El proxy **no guarda contraseñas en disco** (solo cachea tokens temporales).
No hace falta cambiar el código del proxy: sirve tal cual está en el repo.

---

## Parte A — Preparar la Pi

La librería de Garmin (`garminconnect` → `garth`) necesita **Python 3.10 o
mayor**. Una Pi que estuvo mucho tiempo guardada casi seguro trae un OS viejo
(Python 3.7). Tenés dos caminos:

### A.1 — Recomendado: reflashear el OS (más confiable, ~15 min)

1. En tu compu, instalá **Raspberry Pi Imager**: https://www.raspberrypi.com/software/
2. Meté la microSD de la Pi.
3. En el Imager:
   - **Dispositivo:** Raspberry Pi 3
   - **Sistema operativo:** *Raspberry Pi OS Lite (64-bit)* (sin escritorio, liviano)
   - **Almacenamiento:** tu microSD
4. Antes de grabar, abrí la **rueda de ajustes** (⚙️) y configurá:
   - **Hostname:** `coachpi`
   - **Activar SSH** (con contraseña)
   - **Usuario y contraseña** (anotalos)
   - **Wi-Fi:** tu red y clave + país (CR)
   - **Zona horaria / idioma**
5. Grabá, meté la SD en la Pi, encendela. Esperá ~1 min.
6. Desde tu compu, conectate por SSH:
   ```bash
   ssh coachpi@coachpi.local
   # si .local no resuelve, usá la IP que te dé tu router: ssh coachpi@192.168.x.x
   ```

> 64-bit es importante: garantiza que `pip` baje los paquetes ya compilados
> (wheels) para ARM y no tenga que compilar nada pesado.

### A.2 — Alternativa: actualizar el OS existente

Solo si ya está en Raspberry Pi OS **Bullseye o Bookworm**. Si es más viejo
(Buster/Stretch), mejor reflashear.

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
# después de reiniciar:
python3 --version   # tiene que decir 3.10 o más; si dice 3.7/3.9, reflashear
```

---

## Parte B — Instalar el proxy

Ya conectado por SSH a la Pi:

```bash
# 1. Herramientas base
sudo apt update
sudo apt install -y git python3-venv python3-pip

# 2. Traer el código
git clone https://github.com/sergioalejserrano/coah-sergio.git
cd coah-sergio

# 3. Entorno virtual (obligatorio en Bookworm) + dependencias
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements_proxy.txt
```

La instalación en una Pi 3 puede tardar unos minutos. Si `pip` se queda
compilando mucho rato, es normal la primera vez.

---

## Parte C — Probar el proxy localmente

```bash
# (con el venv activado)
uvicorn garmin_proxy:app --host 0.0.0.0 --port 8000
```

Debería decir `Uvicorn running on http://0.0.0.0:8000`. Dejalo corriendo y,
desde otra terminal en la Pi (o tu compu en la misma red), probá:

```bash
curl -X POST http://coachpi.local:8000/data \
  -H "Content-Type: application/json" \
  -d '{"email":"TU_EMAIL_GARMIN","password":"TU_CLAVE"}'
```

Si devuelve un JSON con tus datos (`"success": true ...`), **funciona y el 429
desapareció** porque ahora sale desde tu IP de casa. Frená con `Ctrl+C`.

> Si tenés 2FA (verificación en dos pasos) en Garmin, el login server-to-server
> puede fallar. Lo más simple es desactivar 2FA en esa cuenta, o usar la
> Opción 2 (token) para esa cuenta puntual.

---

## Parte D — Exponer el proxy a internet (URL estable y gratis)

Para que tus amigos lo usen desde cualquier lado necesitás una URL pública.
Recomiendo **Tailscale Funnel**: gratis, URL estable que no cambia, HTTPS
automático, sin abrir puertos en tu router y sin necesidad de un dominio. Tus
amigos **no** necesitan instalar nada (Funnel es público).

```bash
# 1. Instalar Tailscale en la Pi
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# te da un link: abrilo en el navegador y logueate (Google/GitHub/email)

# 2. En el panel de Tailscale (admin console), una sola vez:
#    - Activá "MagicDNS" y "HTTPS Certificates"
#    - Activá "Funnel" para la Pi
#    (Settings → Features / Funnel)

# 3. Exponer el puerto 8000 públicamente
sudo tailscale funnel --bg 8000

# 4. Ver tu URL pública estable
sudo tailscale funnel status
```

Te va a dar algo como `https://coachpi.tuTailnet.ts.net`. **Esa es la URL del
proxy** que va en la página. No cambia entre reinicios.

### Alternativa rápida solo para probar (URL temporal)

Si querés probar en 2 minutos sin cuenta:

```bash
sudo apt install -y cloudflared   # o bajalo de github.com/cloudflare/cloudflared/releases (ARM)
cloudflared tunnel --url http://localhost:8000
```

Imprime una URL `https://algo-al-azar.trycloudflare.com`. Sirve para probar,
pero **cambia cada vez que la reiniciás**, así que no es para uso permanente.

---

## Parte E — Que arranque solo al prender la Pi

Para que el proxy reviva tras reinicios o cortes de luz, creá un servicio
systemd:

```bash
sudo nano /etc/systemd/system/garmin-proxy.service
```

Pegá esto (ajustá `coachpi` si tu usuario es otro):

```ini
[Unit]
Description=Garmin Proxy Coach
After=network-online.target
Wants=network-online.target

[Service]
User=coachpi
WorkingDirectory=/home/coachpi/coah-sergio
ExecStart=/home/coachpi/coah-sergio/.venv/bin/uvicorn garmin_proxy:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activalo:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now garmin-proxy
sudo systemctl status garmin-proxy   # debería decir "active (running)"
```

Tailscale Funnel también arranca solo (queda configurado con `--bg`).

---

## Parte F — Conectar la app a tu proxy

1. Abrí tu página (Coach Vegabikes).
2. En la pantalla de inicio, elegí el modo **"Proxy Garmin"**.
3. Pegá la URL de tu proxy (la de Tailscale Funnel, ej:
   `https://coachpi.tuTailnet.ts.net`), tu email y tu clave de Garmin.
4. "Comenzar".

Para tus amigos, puedo **precargar esa URL** en la página (un cambio de una
línea) para que ellos solo escriban su email y clave. Pasame la URL final del
Funnel y lo dejo listo.

---

## Notas

- **Seguridad:** las claves de Garmin de tus amigos pasan por tu Pi. El proxy
  no las escribe en disco, pero vos sos el responsable del aparato. Para amigos
  que confían en vos, razonable. Si alguno prefiere no darte la clave, existe la
  Opción 2 (token) — avisame y la habilito.
- **2FA:** si una cuenta tiene verificación en dos pasos, el login automático
  puede fallar; desactivala para esa cuenta o usá la Opción 2.
- **Consumo:** el proxy es liviano; la Pi 3 lo aguanta de sobra.
- **Apagar/mover la Pi:** si la apagás, el proxy deja de responder hasta que la
  prendas de nuevo. La URL de Tailscale no cambia, así que al prenderla vuelve a
  funcionar solo.
