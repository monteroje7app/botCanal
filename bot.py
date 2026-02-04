from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from io import BytesIO
from html.parser import HTMLParser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import urljoin

import pdfplumber
import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class Match:
    team_code: str
    date: Optional[str]
    time: Optional[str]
    jersey_color: str  # "azul" | "blanco"
    campo: Optional[str]
    raw_line: str
    page: int


_DATE_RE = re.compile(r"\b(?P<d>\d{1,2})[\/\.-](?P<m>\d{1,2})(?:[\/\.-](?P<y>\d{2,4}))?\b")
_TIME_RE = re.compile(r"\b(?P<h>\d{1,2})[:\.](?P<min>\d{2})\b")
_SCORE_RE = re.compile(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b")
_TEAM_TOKEN_RE = re.compile(r"\b[A-Z]{1,3}\d{1,3}\b")
_TIME_RANGE_RE = re.compile(r"^(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})")
_CAMPO_HEADER_RE = re.compile(r"CAMPO\s+\d+\s*(?:\(([^)]+)\))?", re.IGNORECASE)

_SPANISH_DATE_HEADING_RE = re.compile(r"^\s*(?P<d>\d{1,2})\s+DE\s+(?P<mon>[A-ZÁÉÍÓÚÜÑ]+)\s*$", re.IGNORECASE)

_SPANISH_MONTHS = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def _iso_date(d: int, m: int, y: Optional[int]) -> Optional[str]:
    if y is None:
        return None
    if 0 <= y <= 99:
        y = 2000 + y
    try:
        return datetime(y, m, d).date().isoformat()
    except ValueError:
        return None


def _normalize_upper_noaccents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.upper().strip()


def _parse_spanish_date_heading(line: str, default_year: Optional[int]) -> Optional[str]:
    if default_year is None:
        return None

    m = _SPANISH_DATE_HEADING_RE.match(line)
    if not m:
        return None

    day = int(m.group("d"))
    month_name = _normalize_upper_noaccents(m.group("mon"))
    month = _SPANISH_MONTHS.get(month_name)
    if not month:
        return None

    return _iso_date(day, month, default_year)


def download_pdf(pdf_url: str, dest_path: Path, timeout_s: int = 45) -> None:
    raise NotImplementedError("download_pdf is no longer used; use download_pdf_bytes instead")


def download_pdf_bytes(pdf_url: str, timeout_s: int = 45) -> bytes:
    headers = {
        "User-Agent": "botCanal/1.0 (+https://github.com/)"
    }
    with requests.get(pdf_url, stream=True, timeout=timeout_s, headers=headers) as r:
        r.raise_for_status()
        return r.content


class _AnchorTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_anchor = False
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []
        self.links: list[tuple[str, str, dict[str, str]]] = []
        self._current_attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        self._in_anchor = True
        self._current_text_parts = []
        self._current_href = None
        self._current_attrs = {}
        for key, value in attrs:
            if key.lower() == "href" and value:
                self._current_href = value
            if value is not None:
                self._current_attrs[key.lower()] = value

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a":
            return
        if self._in_anchor:
            text = "".join(self._current_text_parts).strip()
            href = self._current_href
            if href:
                self.links.append((text, href, dict(self._current_attrs)))
        self._in_anchor = False
        self._current_href = None
        self._current_text_parts = []
        self._current_attrs = {}


def _normalize_anchor_text(text: str) -> str:
    return _normalize_upper_noaccents(text)


def _extract_links_from_html(html: str) -> list[tuple[str, str, dict[str, str]]]:
    parser = _AnchorTextParser()
    parser.feed(html)
    return parser.links


def _pdf_looks_like_calendar(pdf_bytes: bytes) -> bool:
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                return False
            text = (pdf.pages[0].extract_text() or "")
    except Exception:
        return False

    norm = _normalize_upper_noaccents(text)
    return ("CALENDARIO" in norm) and ("LIGA INTERNA" in norm)


def resolve_pdf_url_from_page(page_url: str, timeout_s: int = 30) -> str:
    headers = {
        "User-Agent": "botCanal/1.0 (+https://github.com/)"
    }
    with requests.get(page_url, timeout=timeout_s, headers=headers) as r:
        r.raise_for_status()
        html = r.text

    links = _extract_links_from_html(html)

    def is_pdf_link(href: str) -> bool:
        return href.lower().split("?")[0].endswith(".pdf")

    def is_aqui_text(text: str, attrs: dict[str, str]) -> bool:
        if "AQUI" in _normalize_anchor_text(text):
            return True
        for key in ("title", "aria-label", "data-label"):
            value = attrs.get(key, "")
            if "AQUI" in _normalize_anchor_text(value):
                return True
        return False

    aqui_links: list[str] = []
    pdf_candidates: list[str] = []

    for text, href, attrs in links:
        if is_aqui_text(text, attrs):
            aqui_links.append(href)
        if is_pdf_link(href):
            pdf_candidates.append(urljoin(page_url, href))

    for href in aqui_links:
        abs_href = urljoin(page_url, href)
        if is_pdf_link(abs_href):
            pdf_candidates.insert(0, abs_href)
            continue

        try:
            with requests.get(abs_href, timeout=timeout_s, headers=headers) as r2:
                r2.raise_for_status()
                inner_html = r2.text
            for _, inner_href, _ in _extract_links_from_html(inner_html):
                if is_pdf_link(inner_href):
                    pdf_candidates.append(urljoin(abs_href, inner_href))
        except Exception:
            continue

    seen: set[str] = set()
    for pdf_url in pdf_candidates:
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        try:
            pdf_bytes = download_pdf_bytes(pdf_url)
        except Exception:
            continue
        if _pdf_looks_like_calendar(pdf_bytes):
            return pdf_url

    raise ValueError("No se encontró un PDF de calendario válido en la página.")


def extract_pdf_lines(pdf_bytes: bytes) -> Iterator[tuple[int, str]]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.splitlines():
                cleaned = " ".join(line.split())
                if cleaned:
                    yield page_index, cleaned




def _extract_team_tokens(line: str) -> list[str]:
    return _TEAM_TOKEN_RE.findall(line)


def _parse_campo_names(line: str) -> Optional[list[str]]:
    if "CAMPO" not in line.upper():
        return None
    matches = _CAMPO_HEADER_RE.findall(line)
    if not matches:
        return None

    names: list[str] = []
    for idx, label in enumerate(matches, start=1):
        label_clean = (label or "").strip()
        if label_clean:
            names.append(f"Campo {idx} ({label_clean})")
        else:
            names.append(f"Campo {idx}")
    return names


def _get_campo_for_index(campo_names: list[str], pair_index: int) -> str:
    if pair_index < len(campo_names):
        return campo_names[pair_index]
    return f"Campo {pair_index + 1}"


def _strip_datetime_prefix(text: str) -> str:
    text = _DATE_RE.sub(" ", text, count=1)
    text = _TIME_RE.sub(" ", text, count=1)
    return " ".join(text.split())


def _parse_date_time(raw_line: str, default_year: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
    date_iso: Optional[str] = None
    time_hhmm: Optional[str] = None

    m = _DATE_RE.search(raw_line)
    if m:
        d = int(m.group("d"))
        mo = int(m.group("m"))
        y_raw = m.group("y")
        y = int(y_raw) if y_raw is not None else default_year
        date_iso = _iso_date(d, mo, y)

    t = _TIME_RE.search(raw_line)
    if t:
        hh = int(t.group("h"))
        mm = int(t.group("min"))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            time_hhmm = f"{hh:02d}:{mm:02d}"

    return date_iso, time_hhmm


def _infer_default_year(lines: Iterable[tuple[int, str]]) -> Optional[int]:
    for _, line in lines:
        m = _DATE_RE.search(line)
        if not m:
            continue
        y_raw = m.group("y")
        if not y_raw:
            continue
        try:
            y = int(y_raw)
        except ValueError:
            continue
        if 0 <= y <= 99:
            y = 2000 + y
        if 1900 <= y <= 2100:
            return y
    return None


def _cleanup_team_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\|•·]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip("-–— ")
    s = re.sub(r"\(.*?\)", "", s).strip()
    return s


def _split_teams(line_wo_datetime: str) -> tuple[Optional[str], Optional[str]]:
    # Try common separators between teams.
    # We keep this permissive because PDFs often collapse spacing.
    separators = [
        r"\s+-\s+",
        r"\s+–\s+",
        r"\s+—\s+",
        r"\s+vs\.?\s+",
        r"\s+v\.?\s+",
    ]

    # Remove trailing scores to reduce false splits on score separators.
    candidate = re.sub(r"\s+" + _SCORE_RE.pattern + r"\s*$", "", line_wo_datetime)
    candidate = candidate.strip()

    for sep in separators:
        parts = re.split(sep, candidate, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            home = _cleanup_team_name(parts[0])
            away = _cleanup_team_name(parts[1])
            if home and away:
                # Trim any lingering score/extra info at the end of away
                away = re.split(_SCORE_RE, away, maxsplit=1)[0].strip()
                away = re.split(r"\s{2,}|\s+\|\s+|\s+@\s+", away, maxsplit=1)[0].strip()
                away = _cleanup_team_name(away)
                return (home or None), (away or None)

    return None, None


def parse_matches(
    lines: Iterable[tuple[int, str]],
    team_code: Optional[str],
) -> list[Match]:
    team_code_norm = team_code.strip() if team_code else ""

    # Many calendar rows only contain the time + teams; the date is often a page heading
    # like "31 DE ENERO". We infer a default year from any explicit dd/mm/yy we find.
    lines_list = list(lines)
    default_year = _infer_default_year(lines_list)
    if default_year is None:
        default_year = datetime.now(timezone.utc).year

    matches: list[Match] = []
    seen: set[tuple] = set()

    current_context_date: Optional[str] = None
    current_campo_names: list[str] = []
    in_liga_interna = False
    # Color is derived from LOCAL/VISITANTE column position.

    for page, line in lines_list:
        heading_date = _parse_spanish_date_heading(line, default_year)
        if heading_date:
            current_context_date = heading_date
            in_liga_interna = False

        upper_line = line.upper()
        if "LIGA INTERNA" in upper_line:
            in_liga_interna = True
            continue
        if upper_line.startswith("FEDERADOS"):
            in_liga_interna = False
            continue

        if not in_liga_interna:
            continue

        campo_names = _parse_campo_names(line)
        if campo_names:
            current_campo_names = campo_names
            continue

        if not _TIME_RANGE_RE.search(line):
            continue

        date_iso, time_hhmm = _parse_date_time(line, default_year=default_year)
        if date_iso is None:
            date_iso = current_context_date
        team_tokens = _extract_team_tokens(line)
        if not team_tokens:
            continue

        for idx_token, token in enumerate(team_tokens):
            if team_code_norm and token.lower() != team_code_norm.lower():
                continue

            pair_index = idx_token // 2
            campo = _get_campo_for_index(current_campo_names, pair_index) if current_campo_names else None

            # In each pair, LOCAL is first (blanco) and VISITANTE is second (azul).
            jersey_color = "blanco" if (idx_token % 2 == 0) else "azul"

            match = Match(
                team_code=token,
                date=date_iso,
                time=time_hhmm,
                jersey_color=jersey_color,
                campo=campo,
                raw_line=line,
                page=page,
            )

            key = (
                match.team_code,
                match.date,
                match.time,
                match.jersey_color,
                match.campo,
                match.raw_line,
                match.page,
            )
            if key in seen:
                continue
            seen.add(key)
            matches.append(match)

    return matches


def write_outputs(matches: list[Match], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "matches.json"
    txt_path = output_dir / "matches.txt"

    sorted_matches = sorted(
        matches,
        key=lambda m: (
            (m.team_code or "").upper(),
            m.date or "",
            m.time or "",
        ),
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(sorted_matches),
        "matches": [
            {
                "team": m.team_code,
                "date": m.date,
                "time": m.time,
                "color": m.jersey_color,
                "campo": m.campo,
            }
            for m in sorted_matches
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append("Teams: all" if not matches else "Teams: all")
    lines.append(f"Generated (UTC): {payload['generated_at']}")
    lines.append(f"Matches: {len(sorted_matches)}")
    lines.append("")

    for m in sorted_matches:
        dt = " ".join([p for p in [m.date or "", m.time or ""] if p]).strip() or "(no date/time)"
        campo_text = f" {m.campo}" if m.campo else ""
        lines.append(f"- {dt} {m.team_code} {m.jersey_color}{campo_text}")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def filter_next_match_day(matches: list[Match]) -> list[Match]:
    today = datetime.now(timezone.utc).date()
    today_dates = []
    future_dates = []
    for m in matches:
        if not m.date:
            continue
        try:
            d = datetime.fromisoformat(m.date).date()
        except ValueError:
            continue
        if d == today:
            today_dates.append(d)
        elif d > today:
            future_dates.append(d)

    if today_dates:
        start_date = today
    else:
        if not future_dates:
            return []
        start_date = min(future_dates)

    # Include matches until Sunday of the same week.
    days_to_sunday = 6 - start_date.weekday()
    end_date = start_date + timedelta(days=days_to_sunday)

    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()
    return [m for m in matches if m.date and start_iso <= m.date <= end_iso]


def send_telegram_notification(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    r.raise_for_status()


def build_telegram_messages(matches: list[Match]) -> list[str]:
    header = "Calendario actualizado"
    max_len = 4096

    def sort_key(m: Match):
        return (m.team_code or "", m.date or "", m.time or "")

    matches_sorted = sorted(matches, key=sort_key)

    messages: list[str] = []
    lines: list[str] = [header]

    for m in matches_sorted:
        team = m.team_code or "(sin equipo)"
        date = m.date or "(sin fecha)"
        time = m.time or "(sin hora)"
        campo = m.campo or "(sin campo)"
        color = m.jersey_color or "(sin color)"
        line = f"- {team} | {date} {time} | {campo} | {color}"

        candidate = "\n".join(lines + [line])
        if len(candidate) > max_len:
            messages.append("\n".join(lines))
            lines = [header, line]
        else:
            lines.append(line)

    if lines:
        messages.append("\n".join(lines))

    return messages


def main(argv: list[str]) -> int:
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(description="Download calendar PDF, extract I12 matches, write outputs, optional Telegram notify.")
    parser.add_argument("--pdf-url", default=os.getenv("PDF_URL"), help="Calendar PDF URL (or set PDF_URL env var)")
    parser.add_argument(
        "--page-url",
        default=os.getenv("PAGE_URL", "https://www.ocioydeportecanal.es/es/page/view/escuela_futbol"),
        help="Página donde buscar el enlace 'AQUI' (o set PAGE_URL env var)",
    )
    parser.add_argument("--team", default=os.getenv("TEAM_CODE", ""), help="(Ignored) Team code filter is disabled; all teams are always included")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output)")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram notification even if secrets are present")

    args = parser.parse_args(argv)

    pdf_url = args.pdf_url
    if not pdf_url:
        try:
            pdf_url = resolve_pdf_url_from_page(args.page_url)
        except Exception as e:
            print(f"ERROR: No se pudo resolver el PDF desde la página: {e}", file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir)

    try:
        pdf_bytes = download_pdf_bytes(pdf_url)
        lines = list(extract_pdf_lines(pdf_bytes))
        matches = parse_matches(lines, None)
        matches = filter_next_match_day(matches)
        json_path, txt_path = write_outputs(matches, output_dir)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {json_path} and {txt_path} ({len(matches)} matches)")

    if args.no_telegram:
        return 0

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if tg_token and tg_chat_id:
        try:
            msgs = build_telegram_messages(matches)
            for msg in msgs:
                send_telegram_notification(tg_token, tg_chat_id, msg)
            print("Telegram notification sent")
        except Exception as e:
            print(f"WARNING: Telegram notification failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
