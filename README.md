# botCanal

Bot en Python que descarga el PDF del calendario de la escuela de fútbol de Ocio y Deporte Canal y extrae los partidos de **LIGA INTERNA** de un equipo: **fecha**, **hora**, **campo** y **color de camiseta**.

- **Equipo:** se filtra por `TEAM_CODE` (p. ej. `CJ11`). Si está vacío, incluye todos los equipos.
- **Jornada:** devuelve los partidos de hoy hasta el domingo; si hoy no hay, los de la próxima fecha disponible hasta el domingo de esa semana.
- **Fecha:** se toma de los encabezados del PDF (`17 DE OCTUBRE`, `VIERNES 23 DE OCTUBRE`). El año se deduce por mes a partir de las fechas explícitas `dd/mm/yyyy` del propio PDF, porque la temporada cruza dos años.
- **Campo y color:** se asignan por la posición de cada equipo bajo las columnas `LOCAL` / `VISITANTE` de cada campo. `LOCAL` = ⚪ `blanco`, `VISITANTE` = 🔵 `azul`.

Genera:
- `output/matches.json`
- `output/matches.txt`

Opcionalmente envía una notificación a Telegram si están presentes `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

---

## Requisitos

- Python 3.10+ (recomendado 3.11)
- Dependencias en `requirements.txt`

---

## Instalación (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Crea tu `.env` a partir del ejemplo:

```bash
cp .env.example .env
```

Variables:

- `PAGE_URL`: página donde buscar el enlace al PDF (por defecto `https://www.ocioydeportecanal.es/es/page/view/escuela_futbol`). El bot busca el enlace `AQUÍ` que sigue al texto «Calendario de competiciones» y comprueba que el PDF sea un calendario (`CALENDARIO` + `LIGA INTERNA` en sus primeras páginas).
- `PDF_URL`: URL de respaldo del PDF. Solo se usa si no se puede resolver el PDF desde `PAGE_URL`; en ese caso el bot avisa con un `WARNING` porque puede estar desactualizada.
- `TEAM_CODE`: código del equipo a filtrar (p. ej. `CJ11`). Vacío = todos.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: opcionales.

> El club publica un PDF nuevo (con otra URL) cada vez que cambia el calendario, así que una `PDF_URL` fija se queda obsoleta sin avisar. Por eso el bot resuelve siempre el PDF desde la página y solo recurre a `PDF_URL` como respaldo.

---

## Ejecución

### Usando variables de entorno

```bash
python bot.py
```

### Pasando página, URL de respaldo y equipo por argumento

```bash
python bot.py --page-url "https://www.ocioydeportecanal.es/es/page/view/escuela_futbol" --pdf-url "https://.../calendario.pdf" --team CJ11
```

Los argumentos tienen prioridad sobre las variables de entorno. `--pdf-url` es solo el respaldo si la página no enlaza un calendario válido.

### Desactivar Telegram aunque existan secrets

```bash
python bot.py --no-telegram
```

---

## Formato de salida

### `output/matches.json`

Estructura:

- `generated_at`: timestamp UTC
- `count`: número de partidos
- `matches`: lista de objetos con:
  - `team`: código de equipo
  - `date`: `YYYY-MM-DD`
  - `time`: `HH:MM`
  - `color`: `azul` | `blanco`
  - `campo`: p. ej. `Campo 1 (PRINCIPAL)`

### `output/matches.txt`

Cabecera con el equipo filtrado (`Teams: CJ11`, o `Teams: all` sin filtro), fecha de generación y número de partidos; después, una línea por partido:

```
- 2026-10-17 09:00 CJ11 🔵 azul Campo 1 (PRINCIPAL)
```

---

## Telegram (opcional)

Si defines:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

…el bot enviará un mensaje con los partidos de la jornada:

```
Calendario actualizado
- CJ11 | 2026-10-17 09:00 | Campo 1 (PRINCIPAL) | 🔵 azul
```

Canal de Telegram donde se publican los resultados:
- https://t.me/OcioCanalFutbol

---

## GitHub Actions (ejecución periódica)

El workflow está en `.github/workflows/calendar-bot.yml` y soporta:

- Ejecución programada: **miércoles y viernes 07:00 UTC**
- Ejecución manual: `workflow_dispatch` (`gh workflow run calendar-bot.yml`)

> Para pausar la ejecución programada, elimina o comenta el bloque `schedule:` **entero**. Si solo comentas las líneas `cron`, `schedule:` queda vacío y el workflow deja de ser válido (tampoco se podrá lanzar a mano).

### Configurar `PDF_URL` y `TEAM_CODE`

El workflow lee `PDF_URL` y `TEAM_CODE` de las **Repository Variables** y, si no existen, de los **Repository Secrets** con el mismo nombre.

- `Settings → Secrets and variables → Actions → Variables → New repository variable`
- O desde la terminal:

```bash
gh variable set TEAM_CODE --body "CJ11"
gh variable set PDF_URL --body "https://.../calendario.pdf"
```

### Configurar Telegram en GitHub

En `Settings → Secrets and variables → Actions → Secrets` crea:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Si no existen, el workflow correrá igual (solo que no notificará).

### Ver resultados del workflow

En cada ejecución, el workflow:

- hace commit de `output/matches.json` y `output/matches.txt` en `main` si cambiaron (`chore: update matches output [skip ci]`);
- sube el contenido de `output/` como artifact (`matches-output`).

---

## Notas sobre `output/`

`output/*` está en `.gitignore` (salvo `output/.gitkeep`), así que lo que generes en local no se commitea por accidente. Los `matches.*` que hay en el repo los añade el workflow de GitHub Actions explícitamente.

---

## Troubleshooting

- Si localmente falla por imports (`requests`, `pdfplumber`, etc.), asegúrate de estar usando el entorno virtual y tener instaladas las dependencias:
  - `source .venv/bin/activate`
  - `pip install -r requirements.txt`

- Si `date` sale vacío, suele indicar que el PDF no contiene encabezados de fecha tipo `17 DE OCTUBRE` / `VIERNES 23 DE OCTUBRE` o que el texto no se puede extraer correctamente (depende de cómo esté generado el PDF).

- Si sale `Matches: 0` en plena temporada, revisa que `PDF_URL` apunte al calendario vigente y que `TEAM_CODE` exista en él.
