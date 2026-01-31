from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import pdfplumber
import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class Match:
    team_code: str
    date: Optional[str]
    time: Optional[str]
    home_team: Optional[str]
    away_team: Optional[str]
    opponent: Optional[str]
    is_home: Optional[bool]
    raw_line: str
    page: int


_DATE_RE = re.compile(r"\b(?P<d>\d{1,2})[\/\.-](?P<m>\d{1,2})(?:[\/\.-](?P<y>\d{2,4}))?\b")
_TIME_RE = re.compile(r"\b(?P<h>\d{1,2})[:\.](?P<min>\d{2})\b")
_SCORE_RE = re.compile(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b")

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
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "botCanal/1.0 (+https://github.com/)"
    }
    with requests.get(pdf_url, stream=True, timeout=timeout_s, headers=headers) as r:
        r.raise_for_status()
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        with tmp_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
        tmp_path.replace(dest_path)


def extract_pdf_lines(pdf_path: Path) -> Iterator[tuple[int, str]]:
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.splitlines():
                cleaned = " ".join(line.split())
                if cleaned:
                    yield page_index, cleaned


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


def parse_matches(lines: Iterable[tuple[int, str]], team_code: str) -> list[Match]:
    team_code_norm = team_code.strip()
    if not team_code_norm:
        raise ValueError("team_code must not be empty")

    # Many calendar rows only contain the time + teams; the date is often a page heading
    # like "31 DE ENERO". We infer a default year from any explicit dd/mm/yy we find.
    lines_list = list(lines)
    default_year = _infer_default_year(lines_list)
    if default_year is None:
        default_year = datetime.now(timezone.utc).year

    matches: list[Match] = []
    seen: set[tuple] = set()

    current_context_date: Optional[str] = None

    for page, line in lines_list:
        heading_date = _parse_spanish_date_heading(line, default_year)
        if heading_date:
            current_context_date = heading_date

        if team_code_norm.lower() not in line.lower():
            continue

        date_iso, time_hhmm = _parse_date_time(line, default_year=default_year)
        if date_iso is None:
            date_iso = current_context_date
        line_wo_datetime = _strip_datetime_prefix(line)
        home, away = _split_teams(line_wo_datetime)

        opponent: Optional[str] = None
        is_home: Optional[bool] = None

        if home and away:
            if team_code_norm.lower() in home.lower():
                opponent = away
                is_home = True
            elif team_code_norm.lower() in away.lower():
                opponent = home
                is_home = False

        match = Match(
            team_code=team_code_norm,
            date=date_iso,
            time=time_hhmm,
            home_team=home,
            away_team=away,
            opponent=opponent,
            is_home=is_home,
            raw_line=line,
            page=page,
        )

        key = (
            match.team_code,
            match.date,
            match.time,
            match.home_team,
            match.away_team,
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

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(matches),
        "matches": [asdict(m) for m in matches],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append(f"Team: {matches[0].team_code if matches else ''}".strip())
    lines.append(f"Generated (UTC): {payload['generated_at']}")
    lines.append(f"Matches: {len(matches)}")
    lines.append("")

    for m in matches:
        dt = " ".join([p for p in [m.date or "", m.time or ""] if p]).strip() or "(no date/time)"
        ha = "HOME" if m.is_home is True else "AWAY" if m.is_home is False else ""
        teams = ""
        if m.home_team and m.away_team:
            teams = f"{m.home_team} vs {m.away_team}"
        else:
            teams = m.raw_line
        extra = f" [{ha}]" if ha else ""
        lines.append(f"- {dt}{extra} :: {teams}")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


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


def build_telegram_message(matches: list[Match], team_code: str) -> str:
    header = f"Calendario actualizado: {team_code}\nPartidos encontrados: {len(matches)}"

    # Show up to 5 upcoming-ish matches (those with a parsed date first)
    def sort_key(m: Match):
        if m.date:
            return (0, m.date, m.time or "00:00")
        return (1, "9999-12-31", m.time or "00:00")

    sample = sorted(matches, key=sort_key)[:5]
    body_lines: list[str] = []
    for m in sample:
        dt = " ".join([p for p in [m.date or "", m.time or ""] if p]).strip() or "(sin fecha/hora)"
        opp = m.opponent or "(rival desconocido)"
        ha = "Local" if m.is_home is True else "Visitante" if m.is_home is False else ""
        suffix = f" - {ha}" if ha else ""
        body_lines.append(f"- {dt}: vs {opp}{suffix}")

    body = "\n".join(body_lines)
    return header + ("\n\n" + body if body else "")


def main(argv: list[str]) -> int:
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(description="Download calendar PDF, extract I12 matches, write outputs, optional Telegram notify.")
    parser.add_argument("--pdf-url", default=os.getenv("PDF_URL"), help="Calendar PDF URL (or set PDF_URL env var)")
    parser.add_argument("--team", default=os.getenv("TEAM_CODE", "I12"), help="Team code to filter (default: I12)")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output)")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram notification even if secrets are present")

    args = parser.parse_args(argv)

    if not args.pdf_url:
        print("ERROR: PDF_URL is not set. Provide --pdf-url or set PDF_URL env var.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    pdf_path = output_dir / "calendar.pdf"

    try:
        download_pdf(args.pdf_url, pdf_path)
        lines = list(extract_pdf_lines(pdf_path))
        matches = parse_matches(lines, args.team)
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
            msg = build_telegram_message(matches, args.team)
            send_telegram_notification(tg_token, tg_chat_id, msg)
            print("Telegram notification sent")
        except Exception as e:
            print(f"WARNING: Telegram notification failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
