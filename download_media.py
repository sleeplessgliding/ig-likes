#!/usr/bin/env python3
"""
Download Instagram post thumbnails for offline browsing.

Reads URLs from your Instagram export JSON files and downloads just the cover
image (JPG) for each post — works for both photos and reels. Skips video files
to save space. Files end up at ./media/{shortcode}.jpg.

This script is meant to run on YOUR machine (not the AI sandbox), so it can
use Instagram's own embed APIs / your login session, neither of which the
AI has access to.

USAGE
-----

    # 1. Install dependency
    pip install instaloader

    # 2. (Recommended) log in interactively so IG doesn't rate-limit you
    python3 -m instaloader -l YOUR_USERNAME

    # 3. Download (point at one or both JSON exports)
    python download_media.py saved_posts.json saved_collections.json --login YOUR_USERNAME

    # If your login session is rate-limited (401 / "Please wait"), skip GraphQL
    # and only use anonymous public thumbnail fallbacks:
    python download_media.py saved_posts.json --from-failed --fallback-only

    # Test with just the first 20 first
    python download_media.py saved_posts.json --limit 20

    # If you hit rate limits, slow down
    python download_media.py saved_posts.json --delay 5

    # Resume after interruption: just re-run the same command, it skips
    # what's already in ./media/

NOTES
-----
- Without --login you'll only get public posts and IG rate-limits faster.
- A logged-in session is much more reliable. instaloader saves your session
  after `python3 -m instaloader -l USER` so future runs of this script will use it.
- Expect ~100-300 KB per thumbnail. 500 posts ≈ 50-150 MB of disk.
- If Instagram returns 401 "Please wait a few minutes", stop retrying with
  --login for several hours — hammering makes the cooldown longer.
"""
import argparse
import html as html_lib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

instaloader = None


def get_instaloader():
    global instaloader
    if instaloader is not None:
        return instaloader
    try:
        import instaloader as il
    except ImportError:
        print("ERROR: instaloader is not installed.", file=sys.stderr)
        print("Install it with:  pip install instaloader", file=sys.stderr)
        sys.exit(1)
    instaloader = il
    return instaloader


SHORTCODE_RE = re.compile(r"/(?:p|reel|tv)/([^/]+)")
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:'
    r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|'
    r'content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'
    r')',
    re.IGNORECASE,
)
EMBED_IMG_RE = re.compile(
    r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
DISPLAY_URL_RE = re.compile(r'"display_url"\s*:\s*"([^"]+)"')
THUMBNAIL_SRC_RE = re.compile(r'"thumbnail_src"\s*:\s*"([^"]+)"')
THUMBNAIL_URL_RE = re.compile(r'"thumbnail_url"\s*:\s*"([^"]+)"')
SCONTENT_RE = re.compile(r'https://scontent[^"\'\\\s<>]+')


def walk_for_urls(node, urls):
    """Recursively walk JSON to find every IG URL."""
    if isinstance(node, dict):
        if node.get("label") == "URL":
            v = node.get("value", "")
            if isinstance(v, str) and "instagram.com" in v:
                urls.add(v)
        for v in node.values():
            walk_for_urls(v, urls)
    elif isinstance(node, list):
        for item in node:
            walk_for_urls(item, urls)


def extract_urls(json_path):
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    urls = set()
    walk_for_urls(data, urls)
    return urls


def normalize_carousel_files(out_dir, shortcode):
    """instaloader names carousels '{shortcode}_1.jpg', '_2.jpg', etc.
    Rename the first one to '{shortcode}.jpg' so the HTML can find it."""
    plain = out_dir / f"{shortcode}.jpg"
    if plain.exists():
        return
    first = out_dir / f"{shortcode}_1.jpg"
    if first.exists():
        first.rename(plain)


def cleanup_metadata(out_dir, shortcode):
    """instaloader can leave stray .txt and .json.xz files. Delete them."""
    for ext in (".txt", ".json.xz", ".xz"):
        for f in out_dir.glob(f"{shortcode}*{ext}"):
            try:
                f.unlink()
            except OSError:
                pass


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
CRAWLER_UA = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
)


def _opener(user_agent=UA):
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ("User-Agent", user_agent),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
        ("Accept-Language", "en-US,en;q=0.9"),
    ]
    return opener


def _anon_opener():
    return _opener(UA)


def _crawler_opener():
    """Instagram still emits og:image for Facebook's crawler on many private/gated posts."""
    return _opener(CRAWLER_UA)


def _fetch(opener, url, timeout=30):
    """Return (status, body_bytes, content_type)."""
    req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), (resp.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        ctype = e.headers.get("Content-Type") if e.headers else ""
        return e.code, body, ctype


def _unescape_json_url(u):
    return html_lib.unescape(
        (u or "").encode("utf-8").decode("unicode_escape").replace("\\/", "/")
    )


def _clean_image_url(u):
    if not u:
        return ""
    u = html_lib.unescape(u).replace("\\/", "/")
    if "\\u" in u or "\\x" in u:
        try:
            u = u.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    if "scontent" not in u and "cdninstagram.com/v/" not in u:
        if "rsrc.php" in u or "static.cdninstagram.com" in u:
            return ""
    return u


def _first_image_url(text):
    """Pull a post cover URL out of HTML or JSON."""
    m = OG_IMAGE_RE.search(text or "")
    if m:
        cleaned = _clean_image_url(m.group(1) or m.group(2) or "")
        if cleaned:
            return cleaned
    for rx in (THUMBNAIL_URL_RE, DISPLAY_URL_RE, THUMBNAIL_SRC_RE, EMBED_IMG_RE):
        m = rx.search(text or "")
        if m:
            cleaned = _clean_image_url(_unescape_json_url(m.group(1)))
            if cleaned:
                return cleaned
    for raw in SCONTENT_RE.findall(text or ""):
        cleaned = _clean_image_url(raw)
        if cleaned and "/v/" in cleaned:
            return cleaned
    return ""


def _save_image_url(opener, image_url, dest):
    """Download an image URL into dest. Returns True on success."""
    image_url = _clean_image_url(image_url)
    if not image_url:
        return False
    status, content, ctype = _fetch(opener, image_url)
    if status != 200 or not content or len(content) < 500:
        return False
    ctype = (ctype or "").lower()
    is_jpeg = content[:3] == b"\xff\xd8\xff"
    is_png = content[:8] == b"\x89PNG\r\n\x1a\n"
    is_webp = content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if "image" not in ctype and "octet-stream" not in ctype and not (is_jpeg or is_png or is_webp):
        return False
    dest.write_bytes(content)
    return dest.exists() and dest.stat().st_size > 500


def download_thumbnail_fallback(shortcode, url, dest, verbose=False):
    """Try public endpoints (anonymous) for a cover image. Returns True on success."""
    chrome = _anon_opener()
    crawler = _crawler_opener()
    kind = "reel" if "/reel/" in url else "p"
    notes = []

    # 1) Facebook crawler UA — still gets og:image on many private / gated posts
    try:
        status, body, _ = _fetch(crawler, url)
        text = body.decode("utf-8", "replace")
        if status != 200:
            notes.append(f"crawler HTTP {status}")
        else:
            image_url = _first_image_url(text)
            if image_url and _save_image_url(chrome, image_url, dest):
                return True
            notes.append("crawler: no image found")
    except Exception as e:
        notes.append(f"crawler: {e}")

    # 2) oEmbed JSON (public posts)
    try:
        oembed = "https://www.instagram.com/api/v1/oembed/?" + urllib.parse.urlencode(
            {"url": url}
        )
        status, body, _ = _fetch(chrome, oembed)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace") or "{}")
            thumb = data.get("thumbnail_url") or _first_image_url(data.get("html") or "")
            if _save_image_url(chrome, thumb, dest):
                return True
            notes.append("oembed: no thumbnail_url")
        else:
            msg = body.decode("utf-8", "replace")[:80].replace("\n", " ")
            notes.append(f"oembed HTTP {status} {msg}".strip())
    except Exception as e:
        notes.append(f"oembed: {e}")

    # 3) Embed page (Chrome)
    try:
        embed_url = f"https://www.instagram.com/{kind}/{shortcode}/embed/"
        status, body, _ = _fetch(chrome, embed_url)
        text = body.decode("utf-8", "replace")
        if status != 200:
            notes.append(f"embed HTTP {status}")
        else:
            image_url = _first_image_url(text)
            if image_url and _save_image_url(chrome, image_url, dest):
                return True
            notes.append("embed: no image found in HTML")
    except Exception as e:
        notes.append(f"embed: {e}")

    # 4) Public post page (Chrome)
    try:
        status, body, _ = _fetch(chrome, url)
        text = body.decode("utf-8", "replace")
        if status != 200:
            notes.append(f"page HTTP {status}")
        else:
            image_url = _first_image_url(text)
            if image_url and _save_image_url(chrome, image_url, dest):
                return True
            notes.append("page: no image found in HTML")
    except Exception as e:
        notes.append(f"page: {e}")

    # 5) Legacy /media/?size=l redirect
    try:
        media_url = f"https://www.instagram.com/{kind}/{shortcode}/media/?size=l"
        status, content, ctype = _fetch(chrome, media_url)
        if status == 200 and content and len(content) > 500:
            ctype = (ctype or "").lower()
            if "image" in ctype or content[:3] == b"\xff\xd8\xff":
                dest.write_bytes(content)
                return True
        notes.append(f"media/?size=l HTTP {status}")
    except Exception as e:
        notes.append(f"media: {e}")

    if verbose and notes:
        print(f"         fallback notes: {'; '.join(notes)}")
    return False


def prune_media(out_dir, keep_shortcodes):
    """Remove carousel extras and thumbnails not referenced by the export."""
    carousel_re = re.compile(r"^(.+)_\d+$")
    removed = []
    for path in sorted(out_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".txt", ".xz"} or path.name.endswith(".json.xz"):
            path.unlink()
            removed.append(path.name)
            continue
        if path.suffix.lower() != ".jpg":
            continue
        stem = path.stem
        extra = carousel_re.match(stem)
        if extra and extra.group(1) in keep_shortcodes:
            path.unlink()
            removed.append(path.name)
            continue
        if stem not in keep_shortcodes:
            path.unlink()
            removed.append(path.name)
    print(f"Pruned {len(removed)} leftover files from {out_dir}/")
    return removed


def is_rate_limit_msg(msg):
    m = (msg or "").lower()
    return (
        "429" in m
        or "401" in m
        or "rate" in m
        or "wait a few minutes" in m
        or "please wait" in m
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("inputs", nargs="+", help="IG export JSON file(s)")
    ap.add_argument("--out", default="media",
                    help="Output directory (default: media)")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="Seconds between posts (default 5.0; raise if rate-limited)")
    ap.add_argument("--login", default=None,
                    help="IG username to load session for "
                         "(pre-login with: python3 -m instaloader -l USER)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N posts (handy for testing)")
    ap.add_argument("--no-thumbnails", action="store_true",
                    help="Skip the video-cover thumbnails (you'll lose reel previews)")
    ap.add_argument("--fallback-only", action="store_true",
                    help="Skip GraphQL entirely; only use anonymous public thumbnail "
                         "fallbacks (best when your login session is rate-limited)")
    ap.add_argument("--from-failed", action="store_true",
                    help="Only retry shortcodes listed in download_failed.txt")
    ap.add_argument("--prune", action="store_true",
                    help="Delete leftover carousel frames (*_2.jpg) and thumbnails "
                         "whose shortcode is not in the input JSON")
    args = ap.parse_args()

    # 1. Collect every URL from every input file
    all_urls = set()
    for inp in args.inputs:
        path = Path(inp)
        if not path.exists():
            print(f"  ! skipping {inp}: not found")
            continue
        urls = extract_urls(path)
        print(f"  {inp}: {len(urls)} URLs")
        all_urls.update(urls)
    print(f"\nUnique URLs across all inputs: {len(all_urls)}")

    # 2. Pair each with its shortcode
    items = []
    for url in sorted(all_urls):
        m = SHORTCODE_RE.search(url)
        if m:
            items.append((m.group(1), url))
    keep_shortcodes = {sc for sc, _ in items}

    if args.from_failed:
        failed_log = Path("download_failed.txt")
        if not failed_log.exists():
            print("ERROR: --from-failed set but download_failed.txt not found",
                  file=sys.stderr)
            sys.exit(1)
        wanted = set()
        for line in failed_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                wanted.add(line.split("\t")[0])
        items = [(sc, url) for sc, url in items if sc in wanted]
        print(f"Filtered to {len(items)} shortcodes from download_failed.txt")

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    if args.prune:
        prune_media(out_dir, keep_shortcodes)

    # 3. Skip anything we already have
    existing = {p.stem for p in out_dir.glob("*.jpg")}
    todo = [(sc, url) for sc, url in items if sc not in existing]
    print(f"Already on disk: {len(existing)}")
    print(f"To download:    {len(todo)}")

    if not todo:
        print("Nothing to do.")
        return

    if args.limit:
        todo = todo[:args.limit]
        print(f"(Limited to first {len(todo)} for this run)")

    L = None
    il = None
    if not args.fallback_only:
        il = get_instaloader()
        L = il.Instaloader(
            download_pictures=True,
            download_videos=False,
            download_video_thumbnails=not args.no_thumbnails,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            dirname_pattern=str(out_dir),
            filename_pattern="{shortcode}",
            quiet=True,
            iphone_support=False,
            max_connection_attempts=1,
        )

        if args.login:
            try:
                L.load_session_from_file(args.login)
                print(f"Loaded session for @{args.login}")
            except FileNotFoundError:
                print(f"No saved session for @{args.login}. Run first:")
                print(f"  python3 -m instaloader -l {args.login}")
                return
            except Exception as e:
                print(f"Could not load session: {e}")
                return
    else:
        print("Fallback-only mode: skipping GraphQL / login session")

    # 4. Download loop
    print()
    ok = 0
    failed = []
    rate_limited = False

    for i, (shortcode, url) in enumerate(todo, 1):
        prefix = f"[{i:4d}/{len(todo)}] {shortcode:<12s}"
        dest = out_dir / f"{shortcode}.jpg"

        if args.fallback_only or rate_limited:
            if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                print(f"{prefix}  ✓ fallback thumbnail")
                ok += 1
            else:
                print(f"{prefix}  ✗ fallback failed")
                failed.append((shortcode, url, "fallback failed"))
            time.sleep(args.delay)
            continue

        try:
            post = il.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target="")
            normalize_carousel_files(out_dir, shortcode)
            cleanup_metadata(out_dir, shortcode)
            if dest.exists():
                print(f"{prefix}  ✓")
                ok += 1
            else:
                print(f"{prefix}  ? downloaded but no .jpg found — trying fallback")
                if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                    print(f"{prefix}  ✓ fallback thumbnail")
                    ok += 1
                else:
                    failed.append((shortcode, url, "no jpg produced"))
        except il.exceptions.LoginRequiredException:
            print(f"{prefix}  ✗ login required — run: "
                  f"python3 -m instaloader -l YOUR_NAME, then re-run with --login YOUR_NAME")
            failed.append((shortcode, url, "login required"))
        except il.exceptions.QueryReturnedNotFoundException:
            print(f"{prefix}  ✗ post deleted or private — trying fallback")
            if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                print(f"{prefix}  ✓ fallback thumbnail")
                ok += 1
            else:
                failed.append((shortcode, url, "404"))
        except il.exceptions.ConnectionException as e:
            msg = str(e)
            if is_rate_limit_msg(msg):
                print(f"{prefix}  ✗ rate-limited — switching remaining to anonymous fallback")
                rate_limited = True
                if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                    print(f"{prefix}  ✓ fallback thumbnail")
                    ok += 1
                else:
                    failed.append((shortcode, url, msg))
            else:
                print(f"{prefix}  ✗ connection: {e} — trying fallback")
                if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                    print(f"{prefix}  ✓ fallback thumbnail")
                    ok += 1
                else:
                    failed.append((shortcode, url, msg))
        except Exception as e:
            print(f"{prefix}  ✗ {type(e).__name__}: {e} — trying fallback")
            if download_thumbnail_fallback(shortcode, url, dest, verbose=True):
                print(f"{prefix}  ✓ fallback thumbnail")
                ok += 1
            else:
                failed.append((shortcode, url, str(e)))

        time.sleep(args.delay)

    print(f"\nDone. {ok}/{len(todo)} succeeded, {len(failed)} failed.")

    if failed:
        log = Path("download_failed.txt")
        with log.open("w", encoding="utf-8") as f:
            for sc, url, err in failed:
                f.write(f"{sc}\t{url}\t{err}\n")
        print(f"Failures logged to {log}")
        print("Tip: if your login is rate-limited, wait several hours, then try:")
        print("  python download_media.py saved_posts.json --from-failed --fallback-only")
        print("Or leave these as placeholders — the HTML viewer still works without them.")


if __name__ == "__main__":
    main()
