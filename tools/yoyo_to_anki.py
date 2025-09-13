#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import hashlib

try:
    # Use urllib to avoid external deps
    import urllib.request
    import urllib.error
except Exception as e:
    print(f"Failed to import urllib: {e}", file=sys.stderr)
    sys.exit(1)


API_URL = "https://yoyochinese.com/api/v1/flashcards/manage/cards"
CDN_AUDIO_BASE = "https://cdn.yoyochinese.com/audio/practice/"


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
    parser.add_argument("--deck-name", default="YoYoChinese", help="Deck name (used for file names only).")
    parser.add_argument("--output", default="export", help="Output directory for TSV and media.")
    parser.add_argument("--per-page", type=int, default=50, help="Cards per page to request from API.")
    parser.add_argument("--max", dest="max_cards", type=int, default=None, help="Maximum number of cards to fetch (for testing).")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between page requests in seconds.")

    # Filters
    parser.add_argument("--mastery-type", default="all", help="Mastery type filter (e.g., all, learning, mastered). Exact values per site.")
    parser.add_argument("--course-id", default="", help="Course ID filter.")
    parser.add_argument("--level-id", default="", help="Level ID filter.")
    parser.add_argument("--unit-id", default="", help="Unit ID filter.")
    parser.add_argument("--lesson-id", default="", help="Lesson ID filter.")

    # Output format
    parser.add_argument("--format", choices=["simple", "rich"], default="simple", help="TSV format: simple=2 fields (Front/Back) with optional audio; rich=7 fields (Simplified, Pinyin, English, Traditional, Audio, Code, WordType).")
    parser.add_argument("--include-audio", action="store_true", help="Download audio files and reference them in TSV.")
    parser.add_argument("--audio-speed", choices=["normal", "slow"], default="normal", help="Which audio to reference.")
    parser.add_argument("--make-apkg", action="store_true", help="Create an .apkg with subdecks Word/Sentence using genanki (if available).")
    parser.add_argument("--apkg-path", default=None, help="Optional explicit output path for the .apkg file (defaults to export/<deck-name>.apkg).")
    parser.add_argument("--split-by-wordtype", action="store_true", help="Write separate TSVs for Word and Sentence based on card.wordType.")

    args = parser.parse_args()

    cookie = args.cookie or os.getenv("YOYO_COOKIE")
    if not cookie:
        print("Error: --cookie (or env YOYO_COOKIE) is required to authenticate to yoyochinese.com", file=sys.stderr)
        sys.exit(2)

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
    audio_to_download: List[Tuple[str, str]] = []  # (url, dest_path)

    for card in cards:
        label = _word_type_label(card.wordType)
        if args.format == "simple":
            front, back, audio_fn = to_simple_fields(card, args.include_audio, args.audio_speed)
            if args.split_by_wordtype and label in tsv_simple_by_type:
                tsv_simple_by_type[label].append((front, back))
            else:
                tsv_rows_simple_all.append((front, back))
            if args.include_audio and audio_fn:
                url = CDN_AUDIO_BASE + audio_fn
                audio_to_download.append((url, os.path.join(media_dir, audio_fn)))
        else:
            fields, audio_fn = to_rich_fields(card, args.include_audio, args.audio_speed)
            if args.split_by_wordtype and label in tsv_rich_by_type:
                tsv_rich_by_type[label].append(fields)
            else:
                tsv_rows_rich_all.append(fields)
            if args.include_audio and audio_fn:
                url = CDN_AUDIO_BASE + audio_fn
                audio_to_download.append((url, os.path.join(media_dir, audio_fn)))

    # Write TSV(s)
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
        print(f"Downloading {len(audio_to_download)} audio files to {media_dir} ...")
        ok = 0
        for i, (url, dest) in enumerate(audio_to_download, start=1):
            # Skip if already exists
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                ok += 1
                if i % 50 == 0:
                    print(f"  [{i}/{len(audio_to_download)}] cached")
                continue
            try:
                http_download(url, dest)
                ok += 1
                if i % 25 == 0:
                    print(f"  [{i}/{len(audio_to_download)}] downloaded")
            except Exception as e:
                print(f"  WARN: failed {url} → {e}")
                # continue; keep TSV usable
        print(f"Audio downloads completed: {ok}/{len(audio_to_download)} ok")

    # Optionally build .apkg with subdecks (Word/Sentence) using genanki
    if args.make_apkg:
        try:
            import genanki  # type: ignore
        except Exception as e:
            print("Cannot build .apkg: genanki not installed. Install with: pip install genanki", file=sys.stderr)
            return

        # Load templates and style from tools; fall back to minimal defaults
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        def _read_file(p: str, default: str) -> str:
            try:
                with open(p, 'r', encoding='utf-8') as fh:
                    return fh.read()
            except Exception:
                return default

        tools_dir = os.path.join(repo_root, 'tools')
        front_html = _read_file(os.path.join(tools_dir, 'anki_front_template.html'), "{{simplified}}")
        back_html = _read_file(os.path.join(tools_dir, 'anki_back_template.html'), "{{simplified}}<br>{{pinyin}}<br>{{english}}")
        css_text = _read_file(os.path.join(tools_dir, 'anki_style.css'), ".card { font-family: Georgia; font-size: 14px; }")

        # Stable IDs from names
        def _stable_id(name: str) -> int:
            h = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
            return int(h, 16)

        model = genanki.Model(
            model_id=_stable_id('YoYoChinese-Model-v1'),
            name='YoYoChinese Model',
            fields=[
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

        deck_word = genanki.Deck(_stable_id(f"{args.deck_name}::Word"), f"{args.deck_name}::Word")
        deck_sentence = genanki.Deck(_stable_id(f"{args.deck_name}::Sentence"), f"{args.deck_name}::Sentence")

        # Build notes from fetched cards
        media_files: List[str] = []
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
                    card.simplified,
                    card.traditional,
                    card.pinyin,
                    english,
                    audio_field,
                ],
                tags=[label.lower(), card.code] if card.code else [label.lower()],
            )
            if label == 'Sentence':
                deck_sentence.add_note(note)
            else:
                deck_word.add_note(note)

        # Package and write
        apkg_out = args.apkg_path or os.path.join(out_dir, f"{args.deck_name}.apkg")
        pkg = genanki.Package([deck_word, deck_sentence])
        if media_files:
            pkg.media_files = sorted(set(media_files))
        pkg.write_to_file(apkg_out)
        print(f"Wrote Anki package → {apkg_out}")

    print("Done. Import into Anki: File → Import → select TSV.\n"
          "- For 'simple' format: map Front=Field 1, Back=Field 2.\n"
          "- Place media from 'media/' folder into Anki media if not auto-imported.")


if __name__ == "__main__":
    main()
