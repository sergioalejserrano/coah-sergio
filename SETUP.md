# Instalación — Coach Vegabikes (todo desde el celular)

Tiempo: ~30–40 min, una sola vez. Después, todo funciona solo y vos solo abrís la app.

> El único paso un poquito más cómodo en una compu es **subir los archivos al repo** (paso 2). Si conseguís una prestada 5 minutos, ese paso vuela. Si no, igual se puede 100% del cel. Todo lo demás es del teléfono.

---

## Lo que vas a usar (todo gratis)
- **GitHub** — guarda la app, corre el robot de datos y hospeda la página.
- **Puter** — le da el cerebro (Claude) al coach. Login una vez.
- **Google Calendar** — una URL privada para que el plan respete tu agenda.

---

## Paso 1 · Cuenta de GitHub
1. Entrá a **github.com** desde el navegador del cel y creá una cuenta (o iniciá sesión).
2. **Activá 2FA** en Settings → Password and authentication. (Tu seguridad depende de esto.)

## Paso 2 · Crear el repositorio y subir los archivos
1. Tocá **+ → New repository**.
2. Nombre: `coach` · visibilidad **Public** · tocá **Create repository**.
3. Subí el contenido del ZIP que te pasé:
   - **Add file → Upload files** y soltá los archivos de la raíz (`index.html`, `manifest.webmanifest`, `sw.js`, `icon-192.png`, `icon-512.png`).
   - Para las carpetas, **Add file → Create new file** y en el nombre escribí la ruta completa; GitHub crea la carpeta sola. Pegá el contenido de cada uno:
     - `.github/workflows/sync.yml`
     - `scripts/fetch_garmin.py`
     - `scripts/requirements.txt`
     - `scripts/upload_workouts.py`
     - `data/garmin.enc`  (poné cualquier texto; el robot lo reemplaza)
     - `data/meta.json`  (contenido: `{"fetchedAt":""}`)

## Paso 3 · Tu frase secreta (cifra tus datos)
Inventá una **frase larga** (ej. `cartago-cerro-caribe-2026-vegabikes`). La vas a usar en dos lugares: en GitHub (para cifrar) y en la app (para descifrar). **Anotala bien**; sin ella no se leen los datos (esa es la idea).

## Paso 4 · Conectar Garmin (elegí UNA opción)

**Opción A — más segura (tu clave nunca sale del cel):**
1. Andá a **colab.research.google.com**, **+ New notebook**.
2. Pegá y corré (botón ▶):
   ```python
   !pip -q install garth
   import garth
   garth.login(input("email Garmin: "), input("clave Garmin: "))
   print("TOKEN:\n" + garth.client.dumps())
   ```
3. Copiá el texto largo que imprime (el token).

**Opción B — más simple (clave cifrada en GitHub):** no hacés nada acá; usás email + clave en el paso 5.

## Paso 5 · Guardar los secretos en GitHub
En tu repo: **Settings → Secrets and variables → Actions → New repository secret**. Creá:
- `DATA_PASSPHRASE` → tu frase del paso 3.
- Si elegiste **A**: `GARMIN_TOKENSTORE_B64` → el token del paso 4.
- Si elegiste **B**: `GARMIN_EMAIL` y `GARMIN_PASSWORD`.
- `CALENDAR_ICAL_URL` → tu URL iCal privada (paso 6). *(opcional pero recomendado)*

## Paso 6 · URL privada de tu calendario
1. En el cel, abrí **calendar.google.com** y pedí **"Ver versión para computadora"** en el menú del navegador.
2. Settings → tu calendario → **"Dirección secreta en formato iCal"** → copiá ese link.
3. Pegalo en el secret `CALENDAR_ICAL_URL`.

## Paso 7 · Encender el robot
1. En el repo, pestaña **Actions** → activala si te lo pide.
2. Entrá a **Sync Garmin → Run workflow → Run**.
3. Esperá ~1 min. Si sale verde ✓, ya bajó y cifró tus datos. (Si sale rojo, abrí el run y mandame el error.)
4. De ahí en adelante corre solo cada 4 horas.

## Paso 8 · Publicar la página
1. **Settings → Pages → Source: Deploy from a branch → main → /(root) → Save**.
2. En 1–2 min te da una URL tipo `https://TUUSUARIO.github.io/coach/`.

## Paso 9 · Abrir la app en el cel
1. Abrí esa URL en el cel.
2. Tocá el engranaje **⚙ (arriba a la derecha) → poné tu frase del paso 3 → Guardar y actualizar.** Ya ves tus datos reales.
3. **Coach:** entrá a la pestaña Coach y, la primera vez, conectá tu cuenta Puter (gratis).
4. **Agregar a inicio:** en iPhone, Compartir → *Agregar a inicio*. En Android, menú ⋮ → *Instalar app*. Te queda con ícono, como una app.

---

## Subir entrenamientos al reloj y al Edge (opcional, lo validamos juntos)
`scripts/upload_workouts.py` arranca en modo prueba (no sube nada). Correlo una vez en Colab con `DRY_RUN=1`, mandame lo que imprime, y cuando confirmemos el formato en tu cuenta lo activamos en automático.

## Mantenimiento
- Si cambiás la clave de Garmin (opción B) o el token se vence, actualizá el secret y volvé a correr el workflow.
- Para forzar datos frescos: en la app, tocá el botón ↻.
