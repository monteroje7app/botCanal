# botCanal

Bot en Python que descarga un PDF de calendario, infiere la **fecha** desde encabezados tipo `31 DE ENERO` y detecta el **color de camiseta** (según el color de fondo de cada celda: `azul` o `blanco`).

Por defecto extrae **todos los equipos** del día de ejecución. Si no hay partidos hoy, devuelve la **próxima fecha disponible**.

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

Edita `.env` y define al menos:

- `PDF_URL=...`

---

## Ejecución

### Usando variables de entorno

```bash
python bot.py
```

### Pasando la URL por argumento

```bash
python bot.py --pdf-url "https://.../calendario.pdf"
```

### Filtrar por equipo (opcional)

```bash
python bot.py --team I12
```

### Desactivar Telegram aunque existan secrets

```bash
python bot.py --no-telegram
```

---

## Formato de salida

### `output/matches.json`

Estructura:

- `generated_at`: timestamp UTC
- `count`: número de coincidencias
- `matches`: lista de objetos con:
  - `team`: código de equipo
  - `date`: `YYYY-MM-DD`
  - `time`: `HH:MM`
  - `color`: `azul` | `blanco`

### `output/matches.txt`

Lista legible, una línea por partido:

- `YYYY-MM-DD HH:MM TEAM azul|blanco`

---

## Telegram (opcional)

Si defines:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

…el bot enviará un mensaje con un resumen de resultados.

---

## GitHub Actions (ejecución periódica)

El workflow está en `.github/workflows/calendar-bot.yml` y soporta:

- Ejecución programada: **viernes 07:00 UTC**
- Ejecución manual: `workflow_dispatch`

### Configurar `PDF_URL`

El workflow acepta `PDF_URL` de dos maneras:

1) **Repository Variable** (recomendado si no es sensible)
- `Settings → Secrets and variables → Actions → Variables → New repository variable`
- Name: `PDF_URL`
- Value: la URL del PDF

2) **Repository Secret** (si prefieres tratarlo como sensible)
- `Settings → Secrets and variables → Actions → Secrets → New repository secret`
- Name: `PDF_URL`
- Value: la URL del PDF

### Configurar Telegram en GitHub

En `Settings → Secrets and variables → Actions → Secrets` crea:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Si no existen, el workflow correrá igual (solo que no notificará).

### Ver resultados del workflow

En cada ejecución, el workflow sube el contenido de `output/` como artifact (`matches-output`).

---

## Notas sobre `output/`

La carpeta `output/` se mantiene en el repo solo con `output/.gitkeep`.
Los archivos generados (`matches.json`, `matches.txt`) **no se commitean** y están ignorados en `.gitignore`.

---

## Troubleshooting

- Si localmente falla por imports (`requests`, `pdfplumber`, etc.), asegúrate de estar usando el entorno virtual y tener instaladas las dependencias:
  - `source .venv/bin/activate`
  - `pip install -r requirements.txt`

- Si `date` sale vacío, suele indicar que el PDF no contiene encabezados de fecha en formato tipo `31 DE ENERO` o que el texto no se puede extraer correctamente (depende de cómo esté generado el PDF).
