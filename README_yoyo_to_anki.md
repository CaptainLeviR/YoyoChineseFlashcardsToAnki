YoYoChinese → Anki Exporter

What it does
- Fetches your YoYoChinese flashcards via the site API (requires your browser Cookie).
- Exports an Anki‑friendly TSV file.
- Optionally downloads audio and references it in the TSV as `[sound:...]`.

Prereqs
- Python 3.8+ (no external packages needed).
- Your logged‑in `Cookie` for yoyochinese.com (copied from your browser DevTools → Network → any request to yoyochinese.com → Request Headers → Cookie). Keep this private.

Usage
1) From the repo root:

   python3 yoyo_to_anki.py \
     --cookie "<paste your Cookie header value>" \
     --output ./export \
     --deck-name YoYoChinese \
     --format simple \
     --include-audio \
     --audio-speed normal \
     --per-page 100

   Notes:
   - You can also set `YOYO_COOKIE` env var instead of `--cookie`.
   - If you omit `--course-id`, the script prompts you to select a course and automatically:
     - Sets the deck name to `YoyoChinese <Course Name>` (e.g., `YoyoChinese Beginner Course`).
     - Uses Level subdecks (`Level 1..N`) and writes per-level TSVs.
   - `--format simple` → 2 columns (Front, Back). Front includes Simplified + optional audio; Back includes Pinyin — English.
   - `--format rich` → 7 columns: Simplified, Pinyin, English, Traditional, Audio, Code, WordType.
   - `--audio-workers` → max concurrent audio downloads (default: 8). Increase if your network is fast; reduce if you see rate limits.
   - `--split-by-wordtype` → writes two TSVs instead of one: `<deck-name>.word.<format>.tsv` and `<deck-name>.sentence.<format>.tsv`, based on the card's `WordType`.
   - `--levels-subdecks` → groups by course levels and produces Level subdecks (`Deck::Level 1`, `Deck::Level 2`, …). Also writes per‑level TSVs (`<deck-name>.level1.<format>.tsv`, etc.). This overrides `--split-by-wordtype` (Word/Sentence are combined per level). This is the default behavior when you select a course interactively.
   - `--make-apkg` → also builds an `.apkg` using your HTML/CSS templates placed alongside the script (repo root). With default settings uses `Deck::Word` and `Deck::Sentence`. With `--levels-subdecks`, uses `Deck::Level N` subdecks.
   - `--apkg-path` → optional path for the output `.apkg` (defaults to `export/<deck-name>.apkg`).
   - Use filters if needed: `--course-id`, `--level-id`, `--unit-id`, `--lesson-id`, `--mastery-type`.

Output
- TSV at `export/<deck-name>.<format>.tsv` (or two files when using `--split-by-wordtype`, or one per level when using `--levels-subdecks`)
 - If `--make-apkg`, an Anki package at `export/<deck-name>.apkg` containing subdecks, fields, styling, and audio (when downloaded).
- Media (if `--include-audio`) in `export/media/` with filenames matching `[sound:...]` in TSV

Import into Anki
- File → Import → select the TSV.
- For `simple` format: map Field 1 → Front, Field 2 → Back.
- For `rich` format: create/match a note type with 7 fields or map the fields you want; extra TSV columns can be ignored.
- For audio: if Anki doesn’t auto‑detect the `export/media/` folder, copy those files into Anki’s media folder after importing (Anki → Tools → Check Media to verify).

Tips
- Use `--max 50` for a quick test run.
- Use `--audio-speed slow` to use the slow recording when available.
- Some entries may be missing audio; they will import fine without sound.

Notes
- The `WordType` column now uses labels: `Word` (single word/phrase) or `Sentence` (full sentence). The Mastery field was removed as Anki will manage its own SRS.
 - Level subdecks use a manual mapping of course → ordered level IDs defined in `LEVEL_IDS_BY_COURSE` inside `yoyo_to_anki.py`. Add entries there for future courses. Course display names are defined in `COURSE_NAMES`.
 - When building an `.apkg`, notes include a first field `index` (not shown on the card) containing the lesson/code. This makes the first field unique to prevent Anki duplicate warnings while keeping the visible front unchanged.

Troubleshooting
- 401/403 errors: your Cookie is likely missing/expired. Grab a fresh value from the browser.
- Empty results: adjust filters, or verify your account has flashcards on the site.
- Missing `.apkg`: install `genanki` → `pip install genanki`, then rerun with `--make-apkg`.
 - Audio download errors: the script automatically retries transient failures (e.g., SSL EOF) with exponential backoff and cleans up partial files. If specific files keep failing, you can rerun and it will skip any audio already downloaded.
