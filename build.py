#!/usr/bin/env python3
"""
Build the local Saved website from Instagram export JSON.

Reads saved_posts.json and saved_collections.json, preserves enrichment from
enrichment.json (and leftover HTML viewers if present), fixes export mojibake,
and writes assets/data.js.

USAGE
-----

    python3 build.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SHORTCODE_RE = re.compile(r"/(?:p|reel|tv)/([^/]+)")
DATA_RE = re.compile(r"const DATA = (.*?);\n", re.DOTALL)

RECIPE_KEYWORDS = re.compile(
    r"\b(recipe|ingredients|method|serves|tbsp|tsp|cup|cups|oven|bake|marinate)\b",
    re.IGNORECASE,
)

EXCLUDED_COLLECTIONS = set()
MOJIBAKE_MARKERS = ("Ã", "â", "ð", "ï", "Â")


def shortcode_from_url(url: str) -> str:
    m = SHORTCODE_RE.search(url or "")
    return m.group(1) if m else ""


def post_type_from_url(url: str) -> str:
    if "/reel/" in url:
        return "reel"
    if "/tv/" in url:
        return "tv"
    return "post"


def fix_mojibake(s: str) -> str:
    """Repair IG export strings that were UTF-8 decoded as Latin-1."""
    if not s or not any(ch in s for ch in MOJIBAKE_MARKERS):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def parse_hashtags(node) -> list[str]:
    tags = []
    if not isinstance(node, dict):
        return tags
    if node.get("title") == "Hashtags":
        for item in node.get("dict", []):
            for field in item.get("dict", []):
                if field.get("label") == "Name":
                    tags.append(fix_mojibake(field.get("value") or ""))
    return tags


def parse_owner(node) -> tuple[str, str]:
    username = ""
    owner_name = ""
    if not isinstance(node, dict) or node.get("title") != "Owner":
        return username, owner_name
    for item in node.get("dict", []):
        for field in item.get("dict", []):
            if field.get("label") == "Username":
                username = field.get("value", "") or ""
            elif field.get("label") == "Name":
                owner_name = fix_mojibake(field.get("value", "") or "")
    return username, owner_name


def parse_label_values(label_values) -> dict:
    fields = {
        "url": "",
        "caption": "",
        "title": "",
        "hashtags": [],
        "username": "",
        "owner_name": "",
    }
    for lv in label_values:
        label = lv.get("label")
        if label == "URL":
            fields["url"] = lv.get("value", "") or ""
        elif label == "Caption":
            fields["caption"] = fix_mojibake(lv.get("value", "") or "")
        elif label == "Title":
            fields["title"] = fix_mojibake(lv.get("value", "") or "")
        elif lv.get("title") == "Hashtags":
            fields["hashtags"] = parse_hashtags(lv)
        elif lv.get("title") == "Owner":
            fields["username"], fields["owner_name"] = parse_owner(lv)
    return fields


def make_post(fields: dict, saved_ts: int = 0) -> dict:
    url = fields["url"]
    caption = fields["caption"] or ""
    return {
        "url": url,
        "shortcode": shortcode_from_url(url),
        "caption": caption,
        "title": fields["title"] or "",
        "username": fields["username"] or "",
        "owner_name": fields["owner_name"] or "",
        "type": post_type_from_url(url),
        "hashtags": fields["hashtags"] or [],
        "recipe_status": None,
        "caption_clean": caption,
        "saved_ts": saved_ts,
        "category": None,
        "collections": [],
        "enriched": {"reconstructed": None, "restaurant": None},
    }


def parse_saved_posts(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    posts = []
    for entry in entries:
        fields = parse_label_values(entry.get("label_values", []))
        if not fields["url"] or "instagram.com" not in fields["url"]:
            continue
        posts.append(make_post(fields, saved_ts=entry.get("timestamp", 0)))
    return posts


def parse_saved_collections(path: Path) -> list[dict]:
    collections = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for col in collections:
        name = ""
        update_ts = 0
        media_items = []
        for lv in col.get("label_values", []):
            if lv.get("label") == "Name":
                name = lv.get("value", "") or ""
            elif lv.get("label") == "Update time":
                update_ts = lv.get("timestamp_value", 0) or 0
            elif lv.get("title") == "Media":
                media_items = lv.get("dict", []) or []
        posts = []
        for item in media_items:
            fields = parse_label_values(item.get("dict", []))
            if not fields["url"] or "instagram.com" not in fields["url"]:
                continue
            posts.append(make_post(fields))
        result.append({"name": name, "update_ts": update_ts, "posts": posts})
    return result


def load_html_index(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    m = DATA_RE.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    out = {}
    for col in data:
        for post in col.get("posts", []):
            url = post.get("url")
            if url:
                out[url] = post
    return out


def load_js_archive(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    prefix = "window.ARCHIVE = "
    if not text.startswith(prefix):
        return {}
    blob = text[len(prefix) :].rstrip()
    if blob.endswith(";"):
        blob = blob[:-1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return {p["url"]: p for p in data.get("posts", []) if p.get("url")}


def load_enrichment_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def empty_enriched() -> dict:
    return {"reconstructed": None, "restaurant": None}


def merge_preserved(base: dict, old: dict | None) -> dict:
    if not old:
        return base
    for key in ("enriched", "recipe_status", "category", "caption_clean"):
        if key in old and old[key] is not None:
            if key == "enriched" and old[key]:
                base["enriched"] = old[key]
            elif key == "caption_clean":
                base["caption_clean"] = fix_mojibake(old[key])
            elif key != "enriched":
                base[key] = old[key]
    if "enriched" not in base or not base["enriched"]:
        base["enriched"] = empty_enriched()
    return base


def collection_url_sets(collections: list[dict]) -> dict[str, set[str]]:
    return {col["name"]: {p["url"] for p in col["posts"]} for col in collections}


def has_inline_recipe(caption: str) -> bool:
    if not caption:
        return False
    lower = caption.lower()
    if "recipe" in lower and any(
        kw in lower for kw in ("ingredients", "method", "serves", "tbsp", "tsp", "①", "1.")
    ):
        return True
    return bool(RECIPE_KEYWORDS.search(caption))


def categorize_post(post: dict, col_urls: dict[str, set[str]]) -> str:
    url = post["url"]
    if url in col_urls.get("Restaurants", set()):
        return "restaurant"
    if url in col_urls.get("Recipes", set()):
        return "recipe"
    if has_inline_recipe(post.get("caption") or ""):
        return "recipe"
    return "other"


def apply_new_post_defaults(post: dict, col_urls: dict[str, set[str]]) -> dict:
    category = categorize_post(post, col_urls)
    post["category"] = category
    if category == "recipe" and has_inline_recipe(post.get("caption") or ""):
        post["recipe_status"] = "inline"
    if "enriched" not in post or not post["enriched"]:
        post["enriched"] = empty_enriched()
    return post


def pick_covers(posts: list[dict], media_dir: Path, n: int = 4) -> list[str]:
    covers = []
    for p in posts:
        sc = p.get("shortcode")
        if not sc:
            continue
        if not (media_dir / f"{sc}.jpg").exists():
            continue
        covers.append(sc)
        if len(covers) >= n:
            break
    if len(covers) < n:
        for p in posts:
            sc = p.get("shortcode")
            if sc and sc not in covers:
                covers.append(sc)
                if len(covers) >= n:
                    break
    return covers


def to_js(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c")


def write_redirect(path: Path, target: str, title: str) -> None:
    path.write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url={target}">
<title>{title}</title>
<script>location.replace({target!r});</script>
</head>
<body>
<p><a href="{target}">{title}</a></p>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    root = Path(__file__).resolve().parent
    posts_json = root / "saved_posts.json"
    collections_json = root / "saved_collections.json"
    assets = root / "assets"
    data_js = assets / "data.js"
    enrich_path = root / "enrichment.json"

    for path in (posts_json, collections_json):
        if not path.exists():
            print(f"ERROR: missing {path}", file=sys.stderr)
            sys.exit(1)

    assets.mkdir(exist_ok=True)
    media_dir = root / "media"

    posts = parse_saved_posts(posts_json)
    collections = parse_saved_collections(collections_json)
    col_urls = collection_url_sets(collections)

    old_index: dict = {}
    old_index.update(load_html_index(root / "saved_all_posts.html"))
    old_index.update(load_html_index(root / "saved_collections.html"))
    old_index.update(load_js_archive(data_js))
    file_enrich = load_enrichment_file(enrich_path)
    for url, rec in file_enrich.items():
        if url not in old_index:
            old_index[url] = rec
        else:
            old_index[url] = merge_preserved(dict(old_index[url]), rec)

    url_to_collections: dict[str, list[str]] = {}
    for col in collections:
        if col["name"] in EXCLUDED_COLLECTIONS:
            continue
        for post in col["posts"]:
            names = url_to_collections.setdefault(post["url"], [])
            if col["name"] and col["name"] not in names:
                names.append(col["name"])

    enriched_posts = []
    for post in posts:
        old = old_index.get(post["url"])
        merged = merge_preserved(dict(post), old)
        merged["collections"] = url_to_collections.get(post["url"], [])
        if not old or not merged.get("category"):
            apply_new_post_defaults(merged, col_urls)
        if "enriched" not in merged or not merged["enriched"]:
            merged["enriched"] = empty_enriched()
        if merged.get("caption_clean"):
            merged["caption_clean"] = fix_mojibake(merged["caption_clean"])
        enriched_posts.append(merged)

    # Include collection-only posts that somehow aren't in saved_posts.json
    seen = {p["url"] for p in enriched_posts}
    for col in collections:
        if col["name"] in EXCLUDED_COLLECTIONS:
            continue
        for post in col["posts"]:
            if post["url"] in seen:
                continue
            old = old_index.get(post["url"])
            merged = merge_preserved(dict(post), old)
            merged["collections"] = url_to_collections.get(post["url"], [])
            if not merged.get("category"):
                apply_new_post_defaults(merged, col_urls)
            enriched_posts.append(merged)
            seen.add(post["url"])

    public_collections = []
    seen_names: set[str] = set()
    for col in collections:
        if col["name"] in EXCLUDED_COLLECTIONS or not col["name"]:
            continue
        if col["name"] in seen_names:
            continue
        seen_names.add(col["name"])
        col_posts = [p for p in enriched_posts if col["name"] in p["collections"]]
        if not col_posts:
            continue
        public_collections.append(
            {
                "name": col["name"],
                "update_ts": col["update_ts"],
                "count": len(col_posts),
                "covers": pick_covers(col_posts, media_dir),
            }
        )

    archive = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts": enriched_posts,
        "collections": public_collections,
        "covers": {
            "recipe": pick_covers(
                [p for p in enriched_posts if p.get("category") == "recipe"], media_dir
            ),
            "restaurant": pick_covers(
                [p for p in enriched_posts if p.get("category") == "restaurant"],
                media_dir,
            ),
            "other": pick_covers(
                [p for p in enriched_posts if p.get("category") == "other"], media_dir
            ),
        },
    }

    data_js.write_text("window.ARCHIVE = " + to_js(archive) + ";\n", encoding="utf-8")

    persist = {}
    for post in enriched_posts:
        persist[post["url"]] = {
            "enriched": post.get("enriched") or empty_enriched(),
            "recipe_status": post.get("recipe_status"),
            "category": post.get("category"),
            "caption_clean": post.get("caption_clean"),
        }
    enrich_path.write_text(json.dumps(persist, ensure_ascii=False), encoding="utf-8")

    write_redirect(root / "saved_all_posts.html", "index.html#/all", "Saved · All posts")
    write_redirect(
        root / "saved_collections.html", "index.html", "Saved · Collections"
    )

    cats = {k: 0 for k in ("recipe", "restaurant", "other")}
    for p in enriched_posts:
        cats[p.get("category") or "other"] += 1
    preserved = sum(
        1
        for p in enriched_posts
        if (p.get("enriched") or {}).get("restaurant")
        or (p.get("enriched") or {}).get("reconstructed")
    )
    print(f"Wrote {data_js.relative_to(root)}: {len(enriched_posts)} posts")
    print(
        f"Collections: {len(public_collections)} · "
        f"recipes {cats['recipe']}, restaurants {cats['restaurant']}, other {cats['other']}"
    )
    print(f"Preserved enrichment: {preserved}")


if __name__ == "__main__":
    main()
