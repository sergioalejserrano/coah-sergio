# Seguridad — Coach Vegabikes

Resumen honesto de cómo quedan protegidos tus datos, qué riesgos quedan, y la revisión del código.

## Garantías

1. **Tus datos de salud nunca están en claro en internet.**
   El robot los cifra con **AES-256-GCM**, con una llave derivada de tu frase mediante **PBKDF2-SHA256 (200.000 iteraciones)**. En el repo solo vive `garmin.enc`, un bloque ilegible. La app lo descifra **en tu teléfono** con tu frase. Sin la frase, el archivo no sirve (lo probamos: con la clave equivocada, falla el descifrado).

2. **Tus credenciales no viajan a la app ni quedan en el código.**
   La clave/token de Garmin, la frase y la URL del calendario viven solo en **GitHub Actions Secrets** (cifrados por GitHub, enmascarados en los logs). La página web no contiene ningún secreto.

3. **La app no expone nada hasta que vos pongás tu frase.**
   La frase se guarda solo en el `localStorage` de tu teléfono. No se envía a ningún servidor.

4. **El robot no puede ser secuestrado por terceros.**
   El workflow corre solo por horario o cuando vos lo disparás (`workflow_dispatch`). **Nunca** corre en pull requests, así los Secrets no quedan expuestos a forks. Permiso mínimo: solo escribir el archivo de datos.

5. **El coach.** Tus conversaciones (con tus datos en el prompt) viajan a Anthropic vía Puter, igual que con cualquier IA en la nube. Los datos *en reposo* siguen cifrados; esto aplica solo a lo que le escribís al coach.

## Riesgos que quedan (sin maquillar)
- **Tu cuenta de GitHub y tu cuenta de Puter.** Si te las roban, ahí está el riesgo real. **2FA en ambas, obligatorio.**
- **Opción B (clave de Garmin en Secrets).** Es cómoda pero tu clave (cifrada) vive en GitHub. Si te preocupa, usá la **Opción A (token)**: la clave nunca sale de tu cel y el token lo invalidás cambiando la clave de Garmin. Garmin no permite limitar el alcance de la sesión, así que el token da acceso de lectura a tu cuenta hasta que lo rotés.
- **Repo público.** El *código* es visible (no tiene secretos), y `garmin.enc` es público pero cifrado. Si querés que ni el código se vea, se puede mover el hosting a Cloudflare Pages con repo privado (un paso extra, sigue gratis).
- **Frase débil.** La seguridad del cifrado depende de tu frase. Usá algo largo y único; no la reутилices de otra cuenta.

## Revisión de vulnerabilidades del código
- **Sin secretos en el frontend.** Verificado: `index.html` no contiene claves ni tokens.
- **XSS:** todo dato dinámico se inserta con `textContent` o pasa por `esc()` antes de ir al HTML. Las respuestas del coach se renderizan como texto (`textContent`), no como HTML.
- **Cripto estándar:** AES-GCM (autenticado: detecta manipulación) + PBKDF2 200k. Sin algoritmos caseros. Salt e IV aleatorios por archivo.
- **Sin almacenamiento inseguro:** el Service Worker **nunca** cachea `garmin.enc` ni llamadas externas; solo la cáscara de la app.
- **Dependencias:** versiones fijadas, librerías oficiales (`garminconnect`, `cryptography`, `icalendar`).
- **Permisos del workflow:** `contents: write` y nada más; disparadores restringidos.
- **`.gitignore`** evita subir por error `.env`, tokens o credenciales locales.

## Si algo se compromete
1. Cambiá tu clave de Garmin (invalida cualquier token/sesión).
2. Rotá el secret afectado en GitHub y volvé a correr el workflow.
3. Cambiá tu frase: actualizá `DATA_PASSPHRASE`, corré el workflow, y poné la nueva frase en la app.
