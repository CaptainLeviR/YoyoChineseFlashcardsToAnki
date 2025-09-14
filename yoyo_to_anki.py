#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    # Use urllib to avoid external deps
    import urllib.request
    import urllib.error
except Exception as e:
    print(f"Failed to import urllib: {e}", file=sys.stderr)
    sys.exit(1)


API_URL = "https://yoyochinese.com/api/v1/flashcards/manage/cards"
CDN_AUDIO_BASE = "https://cdn.yoyochinese.com/audio/practice/"

# Mapping of course IDs to ordered Level IDs used for Level subdecks.
# Extend this dict manually for future courses as needed.
LEVEL_IDS_BY_COURSE = {
    # Course: 5f9c5382c32d410f1447bee9 → Levels 1..6
    "5f9c5382c32d410f1447bee9": [
        "5f9c5382c32d410f1447bef5",
        "5f9c5382c32d410f1447bef6",
        "5f9c5382c32d410f1447bef7",
        "5f9c5382c32d410f1447bef8",
        "5f9c5382c32d410f1447bef9",
        "5f9c5382c32d410f1447befa",
    ],
    # Chinese Characters → Levels 1..6
    "5f9c5382c32d410f1447beeb": [
        "5f9c5382c32d410f1447bf01",
        "5f9c5382c32d410f1447bf02",
        "5f9c5382c32d410f1447bf03",
        "5f9c5382c32d410f1447bf04",
        "5f9c5382c32d410f1447bf05",
        "5f9c5382c32d410f1447bf06",
    ],
    # Intermediate Conversational → Levels 1..6
    "5f9c5382c32d410f1447beea": [
        "5f9c5382c32d410f1447befb",
        "5f9c5382c32d410f1447befc",
        "5f9c5382c32d410f1447befd",
        "5f9c5382c32d410f1447befe",
        "5f9c5382c32d410f1447beff",
        "5f9c5382c32d410f1447bf00",
    ],
    # Chinese Characters II → Levels 1..6
    "5f9c5382c32d410f1447beed": [
        "5f9c5382c32d410f1447bf0d",
        "5f9c5382c32d410f1447bf0e",
        "5f9c5382c32d410f1447bf0f",
        "5f9c5382c32d410f1447bf10",
        "5f9c5382c32d410f1447bf11",
        "5f9c5382c32d410f1447bf12",
    ],
    # Upper Intermediate Conversational → Levels 1..6
    "5f9c5382c32d410f1447beec": [
        "5f9c5382c32d410f1447bf07",
        "5f9c5382c32d410f1447bf08",
        "5f9c5382c32d410f1447bf09",
        "5f9c5382c32d410f1447bf0a",
        "5f9c5382c32d410f1447bf0b",
        "5f9c5382c32d410f1447bf0c",
    ],
    # Chinese Character Reader → Levels 1..6
    "5f9c5382c32d410f1447beee": [
        "5f9c5382c32d410f1447bf13",
        "5f9c5382c32d410f1447bf14",
        "5f9c5382c32d410f1447bf15",
        "5f9c5382c32d410f1447bf16",
        "5f9c5382c32d410f1447bf17",
        "5f9c5382c32d410f1447bf18",
    ],
}

# Human-friendly course names for selection and deck naming
COURSE_NAMES: Dict[str, str] = {
    "5f9c5382c32d410f1447bee9": "Beginner Conversational",
    "5f9c5382c32d410f1447beeb": "Chinese Characters",
    "5f9c5382c32d410f1447beea": "Intermediate Conversational",
    "5f9c5382c32d410f1447beed": "Chinese Characters II",
    "5f9c5382c32d410f1447beec": "Upper Intermediate Conversational",
    "5f9c5382c32d410f1447beee": "Chinese Character Reader",
}


@dataclass
class Flashcard:
    id: str
    code: str
    masteryLevel: Optional[int]
    wordType: Optional[int]
    simplified: str
    traditional: str
    pinyin: str
    english1: str
    english2: str
    audio_code_normal: Optional[str]
    audio_code_slow: Optional[str]

    @staticmethod
    def from_api(obj: Dict) -> "Flashcard":
        c = obj.get("content", {})
        return Flashcard(
            id=str(obj.get("id") or obj.get("_id") or ""),
            code=obj.get("code") or "",
            masteryLevel=obj.get("masteryLevel"),
            wordType=obj.get("wordType"),
            simplified=(c.get("simplified") or "").strip(),
            traditional=(c.get("traditional") or "").strip(),
            pinyin=(c.get("pinyin") or "").strip(),
            english1=(c.get("english1") or "").strip(),
            english2=(c.get("english2") or "").strip(),
            audio_code_normal=c.get("normal"),
            audio_code_slow=c.get("slow"),
        )

    def audio_filename(self, speed: str) -> Optional[str]:
        code = None
        if speed == "normal":
            code = self.audio_code_normal
        elif speed == "slow":
            code = self.audio_code_slow
        if not code:
            return None
        return f"{code}.mp3"


def build_headers(cookie: Optional[str]) -> Dict[str, str]:
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        # This header appears in the browser sample; likely optional.
        "is-native": "false",
    }
    if cookie:
        # Allow passing either just the cookie value or full header
        if cookie.lower().startswith("cookie:"):
            # Strip leading 'cookie:'
            cookie_val = cookie.split(":", 1)[1].strip()
        else:
            cookie_val = cookie.strip()
        headers["Cookie"] = cookie_val
    return headers


def http_post_json(url: str, body: Dict, headers: Dict[str, str], timeout: int = 30) -> Dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read().decode(charset, errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if hasattr(e, 'read') else str(e)
        raise RuntimeError(f"HTTP {e.code} error: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e}")


def http_download(
    url: str,
    dest_path: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    retries: int = 4,
    backoff: float = 0.75,
) -> None:
    """Download a URL to dest_path with basic retry + backoff.

    Writes to a temporary .part file and atomically replaces dest on success.
    Cleans up partial files on failure. Retries common transient network/SSL
    errors (e.g., unexpected EOF) with exponential backoff.
    """
    # Prepare request
    req = urllib.request.Request(url, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    # Add range header to be browser-like and resumable, but it's optional
    if "range" not in (headers or {}):
        req.add_header("range", "bytes=0-")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + ".part"

    last_err: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            # Remove any previous partial file before retrying
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)

            # Success: atomically move into place
            os.replace(tmp_path, dest_path)
            return
        except Exception as e:
            last_err = e
            # Best-effort cleanup
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            if attempt >= retries:
                break
            # Exponential backoff with tiny jitter
            sleep_s = backoff * (2 ** (attempt - 1)) + min(0.25, 0.05 * attempt)
            time.sleep(sleep_s)

    # If we reach here, all retries failed
    raise RuntimeError(f"download failed after {retries} attempts: {last_err}")


def fetch_all_flashcards(
    cookie: Optional[str],
    filters: Dict,
    per_page: int,
    max_cards: Optional[int],
    delay: float,
) -> List[Flashcard]:
    headers = build_headers(cookie)
    page = 1
    cards: List[Flashcard] = []
    total = None

    while True:
        body = {
            "filters": filters,
            "page": page,
            "cardsPerPage": per_page,
        }
        data = http_post_json(API_URL, body, headers)
        batch = [Flashcard.from_api(x) for x in data.get("flashcards", [])]
        cards.extend(batch)
        total = data.get("totalFlashcards") if total is None else total
        print(f"Fetched page {page}, +{len(batch)} cards (total so far: {len(cards)} / {total or '?'})")
        if max_cards is not None and len(cards) >= max_cards:
            cards = cards[:max_cards]
            break
        if not batch:
            break
        if total is not None and len(cards) >= total:
            break
        page += 1
        if delay > 0:
            time.sleep(delay)

    return cards


def to_simple_fields(card: Flashcard, include_audio: bool, speed: str) -> Tuple[str, str, Optional[str]]:
    # Front: Chinese (Simplified) + optional audio
    front = card.simplified
    audio_fn = card.audio_filename(speed)
    if include_audio and audio_fn:
        front = f"{front} [sound:{audio_fn}]"

    # Back: Pinyin — English
    english = card.english1
    if card.english2:
        english = f"{english} | {card.english2}"
    back = f"{card.pinyin} — {english}" if card.pinyin else english

    return front, back, audio_fn


def _word_type_label(word_type: Optional[int]) -> str:
    if word_type == 2:
        return "Word"
    if word_type == 3:
        return "Sentence"
    return ""


def to_rich_fields(card: Flashcard, include_audio: bool, speed: str) -> Tuple[List[str], Optional[str]]:
    # Fields: Simplified, Pinyin, English, Traditional, Audio, Code, WordTypeLabel
    english = card.english1
    if card.english2:
        english = f"{english} | {card.english2}"
    audio_fn = card.audio_filename(speed) if include_audio else None
    audio_field = f"[sound:{audio_fn}]" if audio_fn else ""
    fields = [
        card.simplified,
        card.pinyin,
        english,
        card.traditional,
        audio_field,
        card.code,
        _word_type_label(card.wordType),
    ]
    return fields, audio_fn


def write_tsv_simple(output_file: str, rows: List[Tuple[str, str]]):
    with open(output_file, "w", encoding="utf-8") as f:
        for front, back in rows:
            f.write(front.replace("\t", " ") + "\t" + back.replace("\t", " ") + "\n")


def write_tsv_rich(output_file: str, rows: List[List[str]]):
    with open(output_file, "w", encoding="utf-8") as f:
        for fields in rows:
            safe = [x.replace("\t", " ") for x in fields]
            f.write("\t".join(safe) + "\n")


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Export YoYoChinese flashcards to an Anki-friendly TSV and optional audio.")
    parser.add_argument("--cookie", help="Cookie header value used to authenticate to yoyochinese.com (copy from browser). You can pass with or without 'Cookie:' prefix.")
    parser.add_argument("--deck-name", default="YoyoChinese", help="Deck name (used for file names only).")
    parser.add_argument("--output", default="export", help="Output directory for TSV and media.")
    parser.add_argument("--per-page", type=int, default=50, help="Cards per page to request from API.")
    parser.add_argument("--max", dest="max_cards", type=int, default=None, help="Maximum number of cards to fetch (for testing).")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between page requests in seconds.")

    # Filters
    parser.add_argument("--mastery-type", default="all", help="Mastery type filter (e.g., all, learning, mastered). Exact values per site.")
    parser.add_argument("--course-id", default="", help="Course ID filter. If omitted, you will be prompted to select a course.")
    parser.add_argument("--level-id", default="", help="Level ID filter.")
    parser.add_argument("--unit-id", default="", help="Unit ID filter.")
    parser.add_argument("--lesson-id", default="", help="Lesson ID filter.")

    # Output format
    parser.add_argument("--format", choices=["simple", "rich"], default="simple", help="TSV format: simple=2 fields (Front/Back) with optional audio; rich=7 fields (Simplified, Pinyin, English, Traditional, Audio, Code, WordType).")
    parser.add_argument("--include-audio", action="store_true", help="Download audio files and reference them in TSV.")
    parser.add_argument("--audio-workers", type=int, default=8, help="Max concurrent audio downloads (default: 8).")
    parser.add_argument("--audio-speed", choices=["normal", "slow"], default="normal", help="Which audio to reference.")
    parser.add_argument("--make-apkg", action="store_true", help="Create an .apkg using genanki (if available). Uses Word/Sentence subdecks by default, or Level subdecks when --levels-subdecks is set.")
    parser.add_argument("--apkg-path", default=None, help="Optional explicit output path for the .apkg file (defaults to export/<deck-name>.apkg).")
    parser.add_argument("--split-by-wordtype", action="store_true", help="Write separate TSVs for Word and Sentence based on card.wordType.")
    parser.add_argument("--levels-subdecks", action="store_true", help="Group by course levels (Level 1..N) into subdecks; overrides --split-by-wordtype.")

    args = parser.parse_args()

    cookie = args.cookie or os.getenv("YOYO_COOKIE")
    if not cookie:
        print("Error: --cookie (or env YOYO_COOKIE) is required to authenticate to yoyochinese.com", file=sys.stderr)
        sys.exit(2)

    # Interactive course selection when no course is provided
    selected_via_prompt = False
    if not args.course_id:
        # Build selectable list from known courses (those with level mappings)
        available = [(cid, COURSE_NAMES.get(cid, cid)) for cid in LEVEL_IDS_BY_COURSE.keys()]
        if not available:
            print("Error: no courses are configured. Add entries to LEVEL_IDS_BY_COURSE.", file=sys.stderr)
            sys.exit(2)
        print("Select a course to export:")
        for i, (cid, name) in enumerate(available, start=1):
            print(f"  {i}) {name} [{cid}]")
        choice = None
        if sys.stdin.isatty():
            try:
                raw = input("Enter number [1]: ").strip()
                choice = int(raw) if raw else 1
            except Exception:
                choice = 1
        else:
            # Non-interactive; default to first
            choice = 1
            print("No TTY detected; defaulting to option 1.")
        if choice < 1 or choice > len(available):
            choice = 1
        selected_id, selected_name = available[choice - 1]
        args.course_id = selected_id
        # If deck name was left as default, set to 'YoyoChinese <Course>'
        if (args.deck_name or "").strip() == "YoYoChinese":
            args.deck_name = f"YoyoChinese {selected_name}"
        selected_via_prompt = True

    # Build filters after potential interactive selection
    filters = {
        "masteryType": {"value": args.mastery_type, "label": args.mastery_type.capitalize()},
        "courseId": args.course_id,
        "levelId": args.level_id,
        "unitId": args.unit_id,
        "lessonId": args.lesson_id,
    }

    out_dir = os.path.abspath(args.output)
    media_dir = os.path.join(out_dir, "media")
    ensure_dir(out_dir)
    if args.include_audio:
        ensure_dir(media_dir)

    print("Fetching flashcards from YoYoChinese ...")
    cards: List[Flashcard] = []
    cards_by_level: Dict[int, List[Flashcard]] = {}
    # Use Level subdecks if requested or when a course was selected interactively
    using_levels = bool(args.levels_subdecks) or selected_via_prompt

    if using_levels:
        if not args.course_id:
            print("Error: --levels-subdecks requires --course-id.", file=sys.stderr)
            sys.exit(2)
        if args.course_id not in LEVEL_IDS_BY_COURSE:
            print(
                "Error: no level mapping found for this --course-id. Add it to LEVEL_IDS_BY_COURSE.",
                file=sys.stderr,
            )
            sys.exit(2)
        if args.split_by_wordtype:
            print("Note: --levels-subdecks ignores --split-by-wordtype (combines Word+Sentence).")

        level_ids = LEVEL_IDS_BY_COURSE[args.course_id]
        for idx, lvl_id in enumerate(level_ids, start=1):
            lvl_filters = dict(filters)
            lvl_filters["levelId"] = lvl_id
            lvl_filters["unitId"] = ""
            lvl_filters["lessonId"] = ""
            try:
                lvl_cards = fetch_all_flashcards(
                    cookie=cookie,
                    filters=lvl_filters,
                    per_page=args.per_page,
                    max_cards=args.max_cards,
                    delay=args.delay,
                )
            except Exception as e:
                print(f"Failed to fetch flashcards for Level {idx}: {e}", file=sys.stderr)
                sys.exit(3)
            cards_by_level[idx] = lvl_cards
            cards.extend(lvl_cards)
        print(f"Fetched {sum(len(v) for v in cards_by_level.values())} cards across {len(cards_by_level)} levels. Transforming → TSV ...")
    else:
        try:
            cards = fetch_all_flashcards(
                cookie=cookie,
                filters=filters,
                per_page=args.per_page,
                max_cards=args.max_cards,
                delay=args.delay,
            )
        except Exception as e:
            print(f"Failed to fetch flashcards: {e}", file=sys.stderr)
            sys.exit(3)
        print(f"Fetched {len(cards)} cards. Transforming → TSV ...")

    # Accumulators
    tsv_rows_simple_all: List[Tuple[str, str]] = []
    tsv_rows_rich_all: List[List[str]] = []
    # When splitting, bucket by label: 'Word' or 'Sentence'
    tsv_simple_by_type: Dict[str, List[Tuple[str, str]]] = {"Word": [], "Sentence": []}
    tsv_rich_by_type: Dict[str, List[List[str]]] = {"Word": [], "Sentence": []}
    # When grouping by levels
    tsv_simple_by_level: Dict[int, List[Tuple[str, str]]] = {}
    tsv_rich_by_level: Dict[int, List[List[str]]] = {}
    audio_to_download: List[Tuple[str, str]] = []  # (url, dest_path)

    def _accumulate_card(card: Flashcard, bucket_simple: List[Tuple[str, str]], bucket_rich: List[List[str]]):
        if args.format == "simple":
            front, back, audio_fn = to_simple_fields(card, args.include_audio, args.audio_speed)
            bucket_simple.append((front, back))
            if args.include_audio and audio_fn:
                url = CDN_AUDIO_BASE + audio_fn
                audio_to_download.append((url, os.path.join(media_dir, audio_fn)))
        else:
            fields, audio_fn = to_rich_fields(card, args.include_audio, args.audio_speed)
            bucket_rich.append(fields)
            if args.include_audio and audio_fn:
                url = CDN_AUDIO_BASE + audio_fn
                audio_to_download.append((url, os.path.join(media_dir, audio_fn)))

    if using_levels:
        for lvl_idx, lvl_cards in cards_by_level.items():
            simple_bucket: List[Tuple[str, str]] = []
            rich_bucket: List[List[str]] = []
            for card in lvl_cards:
                _accumulate_card(card, simple_bucket, rich_bucket)
            if args.format == "simple":
                tsv_simple_by_level[lvl_idx] = simple_bucket
            else:
                tsv_rich_by_level[lvl_idx] = rich_bucket
    else:
        for card in cards:
            label = _word_type_label(card.wordType)
            if args.split_by_wordtype and label in tsv_rich_by_type:
                if args.format == "simple":
                    front, back, audio_fn = to_simple_fields(card, args.include_audio, args.audio_speed)
                    tsv_simple_by_type[label].append((front, back))
                    if args.include_audio and audio_fn:
                        url = CDN_AUDIO_BASE + audio_fn
                        audio_to_download.append((url, os.path.join(media_dir, audio_fn)))
                else:
                    fields, audio_fn = to_rich_fields(card, args.include_audio, args.audio_speed)
                    tsv_rich_by_type[label].append(fields)
                    if args.include_audio and audio_fn:
                        url = CDN_AUDIO_BASE + audio_fn
                        audio_to_download.append((url, os.path.join(media_dir, audio_fn)))
            else:
                _accumulate_card(card, tsv_rows_simple_all, tsv_rows_rich_all)

    # Write TSV(s)
    if using_levels:
        written_files: List[str] = []
        if args.format == "simple":
            for lvl_idx, rows in sorted(tsv_simple_by_level.items()):
                if not rows:
                    continue
                out_tsv = os.path.join(out_dir, f"{args.deck_name}.level{lvl_idx}.{args.format}.tsv")
                write_tsv_simple(out_tsv, rows)
                written_files.append(out_tsv)
        else:
            for lvl_idx, rows in sorted(tsv_rich_by_level.items()):
                if not rows:
                    continue
                out_tsv = os.path.join(out_dir, f"{args.deck_name}.level{lvl_idx}.{args.format}.tsv")
                write_tsv_rich(out_tsv, rows)
                written_files.append(out_tsv)
        if written_files:
            print("Wrote Level TSVs:")
            for p in written_files:
                print(f"  - {p}")
        else:
            print("No TSV written: no cards returned for any level.")
    else:
        if args.split_by_wordtype:
            written_files: List[str] = []
            if args.format == "simple":
                for key, rows in tsv_simple_by_type.items():
                    if not rows:
                        continue
                    out_tsv = os.path.join(out_dir, f"{args.deck_name}.{key.lower()}.{args.format}.tsv")
                    write_tsv_simple(out_tsv, rows)
                    written_files.append(out_tsv)
            else:
                for key, rows in tsv_rich_by_type.items():
                    if not rows:
                        continue
                    out_tsv = os.path.join(out_dir, f"{args.deck_name}.{key.lower()}.{args.format}.tsv")
                    write_tsv_rich(out_tsv, rows)
                    written_files.append(out_tsv)
            if written_files:
                print("Wrote split TSVs:")
                for p in written_files:
                    print(f"  - {p}")
            else:
                print("No TSV written: no cards matched Word/Sentence buckets.")
        else:
            out_tsv = os.path.join(out_dir, f"{args.deck_name}.{args.format}.tsv")
            if args.format == "simple":
                write_tsv_simple(out_tsv, tsv_rows_simple_all)
            else:
                write_tsv_rich(out_tsv, tsv_rows_rich_all)
            print(f"Wrote TSV → {out_tsv}")

    if args.include_audio and audio_to_download:
        # Deduplicate downloads by (url, dest) while preserving order
        unique_pairs = list(dict.fromkeys(audio_to_download))
        total = len(unique_pairs)
        print(f"Downloading {total} audio files to {media_dir} with {args.audio_workers} workers ...")

        # Worker function
        def _download_pair(pair: Tuple[str, str]) -> Tuple[str, Tuple[str, str], Optional[str]]:
            url, dest = pair
            try:
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    return ("cached", pair, None)
                http_download(url, dest)
                return ("downloaded", pair, None)
            except Exception as e:
                return ("failed", pair, str(e))

        ok = 0
        cached = 0
        failed = 0
        processed = 0
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=max(1, args.audio_workers)) as ex:
            futures = [ex.submit(_download_pair, p) for p in unique_pairs]
            for i, fut in enumerate(as_completed(futures), start=1):
                status, pair, err = fut.result()
                with lock:
                    processed += 1
                    if status == "downloaded":
                        ok += 1
                    elif status == "cached":
                        ok += 1
                        cached += 1
                    else:
                        failed += 1
                        url, _ = pair
                        print(f"  WARN: failed {url} → {err}")
                    if processed % 25 == 0 or processed == total:
                        print(f"  [{processed}/{total}] ok={ok} cached={cached} failed={failed}")

        print(f"Audio downloads completed: {ok}/{total} ok ({cached} cached, {failed} failed)")

    # Optionally build .apkg with subdecks (Word/Sentence) using genanki
    if args.make_apkg:
        try:
            import genanki  # type: ignore
        except Exception as e:
            print("Cannot build .apkg: genanki not installed. Install with: pip install genanki", file=sys.stderr)
            return

        # Load templates and style from files colocated with this script (preferred),
        # falling back to a legacy 'tools/' subfolder if present.
        script_dir = os.path.abspath(os.path.dirname(__file__))
        def _read_file(p: str, default: str) -> str:
            try:
                with open(p, 'r', encoding='utf-8') as fh:
                    return fh.read()
            except Exception:
                return default

        def _read_first(paths: List[str], default: str) -> str:
            for p in paths:
                try:
                    with open(p, 'r', encoding='utf-8') as fh:
                        return fh.read()
                except Exception:
                    continue
            return default

        legacy_tools_dir = os.path.join(script_dir, 'tools')
        front_html = _read_first([
            os.path.join(script_dir, 'anki_front_template.html'),
            os.path.join(legacy_tools_dir, 'anki_front_template.html'),
        ], "{{simplified}}")
        back_html = _read_first([
            os.path.join(script_dir, 'anki_back_template.html'),
            os.path.join(legacy_tools_dir, 'anki_back_template.html'),
        ], "{{simplified}}<br>{{pinyin}}<br>{{english}}")
        css_text = _read_first([
            os.path.join(script_dir, 'anki_style.css'),
            os.path.join(legacy_tools_dir, 'anki_style.css'),
        ], ".card { font-family: Georgia; font-size: 14px; }")

        # Stable IDs from names
        def _stable_id(name: str) -> int:
            h = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
            return int(h, 16)

        model = genanki.Model(
            model_id=_stable_id('YoYoChinese-Model-v1'),
            name='YoYoChinese Model',
            fields=[
                { 'name': 'index' },
                { 'name': 'simplified' },
                { 'name': 'traditional' },
                { 'name': 'pinyin' },
                { 'name': 'english' },
                { 'name': 'audio' },
            ],
            templates=[
                {
                    'name': 'Card 1',
                    'qfmt': front_html,
                    'afmt': back_html,
                }
            ],
            css=css_text,
        )

        # Determine base deck name for APKG: prefer course mapping name
        course_base = COURSE_NAMES.get(args.course_id, None)
        apkg_base_name = f"YoyoChinese {course_base}" if course_base else args.deck_name

        # Build notes from fetched cards
        media_files: List[str] = []

        if 'using_levels' in locals() and using_levels:
            # Create Level N subdecks
            decks_by_level: Dict[int, Any] = {}
            for lvl_idx in sorted(cards_by_level.keys()):
                deck_name = f"{apkg_base_name}::Level {lvl_idx}"
                decks_by_level[lvl_idx] = genanki.Deck(_stable_id(deck_name), deck_name)

            for lvl_idx, lvl_cards in cards_by_level.items():
                deck = decks_by_level[lvl_idx]
                for card in lvl_cards:
                    english = card.english1
                    if card.english2:
                        english = f"{english} | {card.english2}"
                    audio_fn = card.audio_filename(args.audio_speed) if args.include_audio else None
                    audio_field = f"[sound:{audio_fn}]" if audio_fn else ""
                    if audio_fn:
                        media_path = os.path.join(media_dir, audio_fn)
                        if os.path.exists(media_path):
                            media_files.append(media_path)

                    note = genanki.Note(
                        model=model,
                        fields=[
                            (card.code or card.id or ''),
                            card.simplified,
                            card.traditional,
                            card.pinyin,
                            english,
                            audio_field,
                        ]
                    )
                    deck.add_note(note)

            decks = [decks_by_level[i] for i in sorted(decks_by_level.keys())]
        else:
            # Default: Word / Sentence subdecks
            deck_word = genanki.Deck(_stable_id(f"{apkg_base_name}::Word"), f"{apkg_base_name}::Word")
            deck_sentence = genanki.Deck(_stable_id(f"{apkg_base_name}::Sentence"), f"{apkg_base_name}::Sentence")

            for card in cards:
                label = _word_type_label(card.wordType) or 'Word'
                english = card.english1
                if card.english2:
                    english = f"{english} | {card.english2}"
                audio_fn = card.audio_filename(args.audio_speed) if args.include_audio else None
                audio_field = f"[sound:{audio_fn}]" if audio_fn else ""
                if audio_fn:
                    media_path = os.path.join(media_dir, audio_fn)
                    if os.path.exists(media_path):
                        media_files.append(media_path)

                note = genanki.Note(
                    model=model,
                    fields=[
                        (card.code or card.id or ''),
                        card.simplified,
                        card.traditional,
                        card.pinyin,
                        english,
                        audio_field,
                    ]
                )
                if label == 'Sentence':
                    deck_sentence.add_note(note)
                else:
                    deck_word.add_note(note)

            decks = [deck_word, deck_sentence]

        # Package and write
        apkg_out = args.apkg_path or os.path.join(out_dir, f"{apkg_base_name}.apkg")
        pkg = genanki.Package(decks)
        if media_files:
            pkg.media_files = sorted(set(media_files))
        pkg.write_to_file(apkg_out)
        print(f"Wrote Anki package → {apkg_out}")

    print("Done. Import into Anki: File → Import → select TSV.\n"
          "- For 'simple' format: map Front=Field 1, Back=Field 2.\n"
          "- Place media from 'media/' folder into Anki media if not auto-imported.")


if __name__ == "__main__":
    main()
