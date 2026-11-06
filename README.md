# Instagram Saved Archive

Local website of your Instagram saved posts and collections.

Drop in a fresh Instagram data export, download thumbnails, rebuild, and open
`index.html`. No server required.

## Files

| File | Role |
|------|------|
| `index.html` | The site |
| `assets/` | CSS, JS, and generated `data.js` |
| `saved_posts.json` | Instagram export — all saved posts |
| `saved_collections.json` | Instagram export — saved collections |
| `enrichment.json` | Recipe/restaurant notes preserved across rebuilds |
| `download_media.py` | Downloads cover thumbnails into `media/` |
| `build.py` | Rebuilds `assets/data.js` from the JSON |
| `media/` | Local JPG thumbnails (`{shortcode}.jpg`) |
| `download_failed.txt` | Shortcodes that failed to download |

The site reads `assets/data.js`. Updating the JSON alone does nothing until you rebuild.

## One-time setup

```bash
cd ~/projects/ig_likes
python3 -m venv .venv
source .venv/bin/activate
pip install instaloader

# Log in once so Instagram rate-limits you less
python3 -m instaloader -l YOUR_USERNAME
```

Thumbnail fallbacks use only the Python standard library. `instaloader` is needed
when you pass `--login`.

## Update workflow (every time you refresh your export)

### 1. Request a new Instagram export

Instagram → Settings → Your activity → Download your information.
Ask for JSON. When it arrives, copy these files into this folder, overwriting:

- `saved_posts.json`
- `saved_collections.json`

They usually live under something like `your_instagram_activity/saved/`.

### 2. Download new thumbnails

```bash
python3 download_media.py saved_posts.json saved_collections.json --login YOUR_USERNAME --prune
```

Already-downloaded images are skipped. `--prune` deletes leftover carousel frames
and thumbnails that are no longer in the export.

If Instagram rate-limits you (`401` / "Please wait a few minutes"):

```bash
python3 download_media.py saved_posts.json --fallback-only --delay 3
```

Posts that still fail show a placeholder. Captions and links still work.

### 3. Rebuild the site

```bash
python3 build.py
```

This parses both JSON files, repairs garbled captions from the export, preserves
recipe/restaurant enrichment, and writes `assets/data.js`. The Sports collection
is skipped (by design).

### 4. Open the site

```bash
open index.html
```

Or double-click `index.html`. Keep it in the same folder as `media/` and `assets/`.

If Instagram embeds fail to load from `file://`, serve the folder:

```bash
python3 -m http.server 8765
# then open http://127.0.0.1:8765/
```

## What the site shows

- **Home** — category cards and your IG collections
- **All / Recipes / Restaurants / Other** — filtered post lists
- **Search** — captions, usernames, hashtags, collection names
- **Keep** — marks live in `localStorage` and survive rebuilds

## Common issues

**"I replaced the JSON but don't see new posts"**
You still need `python3 build.py`.

**`zsh: command not found: instaloader`**
Use `python3 -m instaloader -l YOUR_USERNAME`.

**LibreSSL / urllib3 warning**
Harmless on macOS system Python. Ignore it.

**Rate limit (`Please wait a few minutes`)**
Wait several hours. Do not keep retrying with `--login`.

**Missing thumbnails but posts appear**
Normal for failed downloads. Click "Load preview" or "Open on IG".
