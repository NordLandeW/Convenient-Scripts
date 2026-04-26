#!/usr/bin/env python3
"""Pixiv batch downloader via aria2c RPC.

Supports CSV and JSON input formats.
- CSV: Uses 'original' and 'fileName' columns. Ugoira uses default FPS with warning.
- JSON: Uses full metadata including ugoiraInfo for precise frame delays.

Pixiv CDN resources require a valid Pixiv artwork Referer. This script derives a
per-artwork Referer from each resource URL and also supports remote download
proxies for both aria2c tasks and local ugoira ZIP fetching.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile


PIXIV_ROOT_REFERER = "https://www.pixiv.net/"
COMMON_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
SEPARATOR_LIKE_CHARS = {
    "/", "\\", "／", "＼", "⁄", "∕", "╱", "╲", "⧸", "⧹", "⟋", "⟍",
}
INVALID_CHAR_MAP = {
    "<": "＜",
    ">": "＞",
    ":": "：",
    '"': "＂",
    "|": "｜",
    "?": "？",
    "*": "＊",
}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _is_windows():
    return os.name == "nt"


def _local_path(path):
    """Return a local filesystem path, enabling long paths on Windows."""
    path = os.path.abspath(os.fspath(path))
    if not _is_windows():
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _path_exists(path):
    return os.path.exists(_local_path(path))


def _open_local(path, *args, **kwargs):
    return open(_local_path(path), *args, **kwargs)


def _ensure_parent_dir(path):
    parent = Path(path).parent
    os.makedirs(_local_path(parent), exist_ok=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Batch download pixiv images via aria2c RPC."
    )
    p.add_argument("input", nargs="?",
                   help="Path to CSV or JSON file (auto-detected by extension).")
    p.add_argument("--base-dir", default=str(Path.home()),
                   help="Base directory for relative paths [default: ~/]")
    p.add_argument("--rpc", default="http://localhost:6800/jsonrpc",
                   help="aria2c RPC endpoint")
    p.add_argument("--secret", default="",
                   help="aria2c RPC secret token")
    p.add_argument("--proxy", default="",
                   help=("Proxy for Pixiv resource downloads, e.g. "
                         "http://127.0.0.1:7890, https://..., socks5://127.0.0.1:1080"))
    p.add_argument("--stop-aria2", action="store_true",
                   help="Stop and clear all aria2 active/waiting/stopped tasks before exiting or downloading.")
    p.add_argument("--batch-size", type=int, default=50,
                   help="Tasks per multicall batch [default: 50]")
    p.add_argument("--max-fn-bytes", type=int, default=250,
                   help="Max filename byte length on non-Windows systems [default: 250]")
    p.add_argument("--ugoira-fps", type=int, default=20,
                   help="Default FPS when frame delays unavailable [default: 20]")
    p.add_argument("--ugoira-jobs", type=int, default=4,
                   help="Parallel ugoira conversion threads [default: 4]")
    p.add_argument("--ugoira-quality", default="lossless",
                   help="WebM quality: 'lossless' (default) or CRF 0-63")
    p.add_argument("--ugoira-only", action="store_true",
                   help="Only process ugoira (skip normal images).")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse input and report counts without downloading.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _sanitize_component(name):
    """Sanitize one path component for cross-platform filesystem safety."""
    name = (name or "").strip()
    out = []
    for ch in name:
        if ch == "\x00" or ord(ch) < 32:
            out.append("_")
        elif ch in SEPARATOR_LIKE_CHARS:
            out.append("／")
        else:
            out.append(INVALID_CHAR_MAP.get(ch, ch))
    name = "".join(out)

    if name in {".", ".."}:
        name = name.replace(".", "．")

    if _is_windows():
        name = name.rstrip(" .")
        if not name:
            name = "_"
        stem, ext = os.path.splitext(name)
        base = stem if stem else name
        if base.upper() in WINDOWS_RESERVED_NAMES:
            if stem:
                name = f"{stem}_{ext}"
            else:
                name = name + "_"

    return name or "_"


def sanitize_relative_path(fname):
    """Normalize to a safe relative path while preserving directory structure."""
    raw = (fname or "").strip().replace("\\", "/")
    parts = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        parts.append(_sanitize_component(part))
    if not parts:
        return "_"
    return "/".join(parts)


def _sanitize_filename_text(text):
    """Sanitize free-form text intended to stay inside one filename component."""
    return _sanitize_component("" if text is None else str(text))


def truncate_filename(filename, max_bytes=250):
    """Truncate filename to fit within *max_bytes* UTF-8, keeping extension."""
    if max_bytes is None or max_bytes <= 0:
        return filename, False
    if len(filename.encode("utf-8")) <= max_bytes:
        return filename, False
    stem, ext = os.path.splitext(filename)
    target = max_bytes - len(ext.encode("utf-8"))
    while stem and len(stem.encode("utf-8")) > target:
        stem = stem[:-1]
    return stem + ext, True


def _normalize_output_path(fname, max_fn_bytes):
    """Sanitize and, on non-Windows systems only, optionally truncate filename."""
    fname = sanitize_relative_path(fname)
    if _is_windows():
        return fname, False

    if "/" in fname:
        dpart, npart = fname.rsplit("/", 1)
    else:
        dpart, npart = "", fname
    npart, truncated = truncate_filename(npart, max_fn_bytes)
    if dpart:
        return f"{dpart}/{npart}", truncated
    return npart, truncated


def _replace_suffix(path_text, new_suffix):
    """Replace the filename suffix while preserving directories."""
    path_obj = Path(path_text)
    return str(path_obj.with_suffix(new_suffix)).replace("\\", "/")


def _make_fname_from_json(item):
    """Build a save path from JSON metadata (mirrors CSV fileName format)."""
    xr = item.get("xRestrict", 0)
    rdir = _sanitize_component({0: "All ages", 1: "R-18", 2: "R-18G"}.get(xr, "Other"))
    iid = _sanitize_filename_text(item.get("id", item.get("idNum", "unknown")))
    user = _sanitize_filename_text(item.get("user", "unknown"))
    tags = ",".join(_sanitize_filename_text(tag) for tag in item.get("tags", []))
    stem_parts = [iid, user]
    if tags:
        stem_parts.append(tags)
    stem = "_".join(stem_parts)
    ext = _sanitize_filename_text(item.get("ext", "jpg")).lstrip(".") or "jpg"
    return f"pixiv/{rdir}/{stem}.{ext}"


def _infer_artwork_id(url):
    """Infer Pixiv artwork ID from a resource URL."""
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    for pat in (
        r"^(\d+)-.+_p\d+_master\d+\.",
        r"^(\d+)_p\d+\.",
        r"^(\d+)_ugoira",
    ):
        m = re.match(pat, name)
        if m:
            return m.group(1)
    m = re.search(r"/(\d+)(?:_p\d+|_ugoira|-)", urllib.parse.urlsplit(url).path)
    return m.group(1) if m else None


def _pixiv_referer(url, artwork_id=None):
    """Return the most specific Pixiv Referer for a resource URL."""
    aid = str(artwork_id).strip() if artwork_id else _infer_artwork_id(url)
    if aid:
        return f"https://www.pixiv.net/artworks/{aid}"
    return PIXIV_ROOT_REFERER


def _normalize_proxy(proxy):
    """Validate and normalize proxy URI."""
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    parsed = urllib.parse.urlsplit(proxy)
    scheme = parsed.scheme.lower()
    if scheme not in COMMON_PROXY_SCHEMES or not parsed.netloc:
        raise ValueError(
            "--proxy must be a full URI using one of: "
            + ", ".join(sorted(COMMON_PROXY_SCHEMES))
        )
    return proxy


# ---------------------------------------------------------------------------
# aria2c RPC
# ---------------------------------------------------------------------------

def _rpc(endpoint, method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": "pixdl", "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _rpc_multi(endpoint, calls):
    mc = [{"methodName": m, "params": p} for m, p in calls]
    return _rpc(endpoint, "system.multicall", [mc])


def _tok(secret):
    return ["token:" + secret] if secret else []


def _aria2_extract_gids(items):
    gids = []
    for item in items or []:
        gid = item.get("gid") if isinstance(item, dict) else None
        if gid:
            gids.append(gid)
    return gids


def _aria2_tell_active(endpoint, secret=""):
    resp = _rpc(endpoint, "aria2.tellActive", _tok(secret))
    return _aria2_extract_gids(resp.get("result", []))


def _aria2_list_paginated(endpoint, method, secret="", page_size=1000):
    tok = _tok(secret)
    gids = []
    offset = 0
    while True:
        resp = _rpc(endpoint, method, tok + [offset, page_size])
        batch = _aria2_extract_gids(resp.get("result", []))
        if not batch:
            break
        gids.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return gids


def _build_aria2_force_remove_calls(gids, secret=""):
    tok = _tok(secret)
    return [("aria2.forceRemove", tok + [gid]) for gid in gids if gid]


def _chunked(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(0, len(items), size)]


def _stop_all_aria2(endpoint, secret="", page_size=1000, multicall_size=200):
    active = _aria2_tell_active(endpoint, secret)
    waiting = _aria2_list_paginated(endpoint, "aria2.tellWaiting", secret, page_size)
    remove_calls = _build_aria2_force_remove_calls(active + waiting, secret)

    for chunk in _chunked(remove_calls, multicall_size):
        _rpc_multi(endpoint, chunk)

    _rpc(endpoint, "aria2.purgeDownloadResult", _tok(secret))

    return {
        "removed_active": len(active),
        "removed_waiting": len(waiting),
        "remaining_active": len(_aria2_tell_active(endpoint, secret)),
        "remaining_waiting": len(_aria2_list_paginated(endpoint, "aria2.tellWaiting", secret, page_size)),
        "remaining_stopped": len(_aria2_list_paginated(endpoint, "aria2.tellStopped", secret, page_size)),
    }


def _aria2_options(save_path, referer, proxy=""):
    opts = {
        "dir": _local_path(save_path.parent),
        "out": save_path.name,
        "header": [f"Referer: {referer}"],
        "auto-file-renaming": "false",
        "continue": "true",
        "split": "4",
        "max-connection-per-server": "4",
    }
    if proxy:
        opts["all-proxy"] = proxy
    return opts


# ---------------------------------------------------------------------------
# Ugoira processing
# ---------------------------------------------------------------------------

def _curl_binary():
    return "curl.exe" if _is_windows() else "curl"


def _download_to_file(url, out_path, referer, proxy=""):
    """Download one file to *out_path* with Pixiv Referer and optional proxy."""
    if proxy:
        cmd = [
            _curl_binary(),
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--output",
            _local_path(out_path),
            "--referer",
            referer,
            "--proxy",
            proxy,
            url,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as e:
            raise RuntimeError("curl not found; required for --proxy with ugoira downloads") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("curl timeout") from e
        if r.returncode != 0:
            msg = (r.stderr or "").strip() or f"curl exited with {r.returncode}"
            raise RuntimeError(msg)
        return

    req = urllib.request.Request(url, headers={"Referer": referer})
    with urllib.request.urlopen(req, timeout=120) as resp:
        with _open_local(out_path, "wb") as zf:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                zf.write(chunk)


def _ffmpeg_quote_concat_name(name):
    return name.replace("\\", "/").replace("'", r"'\''")


def _build_concat_manifest(frames, frame_delays=None, default_fps=20):
    """Build an ffmpeg concat demuxer manifest using relative frame names only."""
    lines = []
    for i, frame in enumerate(frames):
        if frame_delays and i < len(frame_delays):
            dur = frame_delays[i] / 1000.0
        else:
            dur = 1.0 / default_fps
        lines.append(f"file '{_ffmpeg_quote_concat_name(frame)}'")
        lines.append(f"duration {dur:.6f}")
    lines.append(f"file '{_ffmpeg_quote_concat_name(frames[-1])}'")
    return "\n".join(lines) + "\n"


def _process_ugoira(url, save_path, referer, frame_delays=None,
                    default_fps=20, quality="lossless", proxy=""):
    """Download ugoira ZIP -> extract frames -> ffmpeg concat -> WebM."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "ugoira.zip")
        try:
            _download_to_file(url, zip_path, referer, proxy)
        except Exception as e:
            print(f"\n  [ERROR] download: {e}", file=sys.stderr)
            return False

        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(_local_path(frames_dir), exist_ok=True)
        try:
            with zipfile.ZipFile(_local_path(zip_path)) as z:
                z.extractall(_local_path(frames_dir))
        except zipfile.BadZipFile as e:
            print(f"\n  [ERROR] bad zip: {e}", file=sys.stderr)
            return False

        frames = sorted(
            f for f in os.listdir(_local_path(frames_dir))
            if not f.startswith(".")
            and os.path.isfile(_local_path(os.path.join(frames_dir, f)))
        )
        if not frames:
            print("\n  [ERROR] empty zip", file=sys.stderr)
            return False

        concat_path = os.path.join(frames_dir, "concat.txt")
        with _open_local(concat_path, "w", encoding="utf-8", newline="\n") as cf:
            cf.write(_build_concat_manifest(frames, frame_delays, default_fps))

        ext = os.path.splitext(frames[0])[1].lower()
        pix_fmt = "yuva420p" if ext == ".png" else "yuv420p"

        temp_output_name = "ugoira.webm"
        temp_output_path = os.path.join(frames_dir, temp_output_name)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", "concat.txt",
            "-vsync", "vfr",
            "-c:v", "libvpx-vp9",
        ]
        if quality == "lossless":
            cmd += ["-lossless", "1"]
        else:
            cmd += ["-crf", str(quality), "-b:v", "0"]
        cmd += ["-pix_fmt", pix_fmt, "-an", temp_output_name]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=_local_path(frames_dir),
            )
            if r.returncode != 0:
                print(f"\n  [ERROR] ffmpeg: {(r.stderr or '')[-400:]}", file=sys.stderr)
                return False
        except subprocess.TimeoutExpired:
            print("\n  [ERROR] ffmpeg timeout", file=sys.stderr)
            return False

        _ensure_parent_dir(save_path)
        shutil.move(_local_path(temp_output_path), _local_path(save_path))

    return True


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_csv(path, max_fn):
    """Return list of entry dicts from CSV."""
    enc = "utf-8"
    for te in ("utf-8-sig", "utf-8", "gbk", "shift_jis"):
        try:
            with _open_local(path, "r", encoding=te) as f:
                f.read(256)
            enc = te
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    entries, trunc = [], 0
    warned = False
    with _open_local(path, "r", encoding=enc, newline="") as f:
        rd = csv.DictReader(f)
        flds = rd.fieldnames or []
        if "original" not in flds or "fileName" not in flds:
            print("[ERROR] CSV needs 'original' and 'fileName' columns.", file=sys.stderr)
            sys.exit(1)
        has_type = "type" in flds

        for row in rd:
            url = row["original"].strip()
            fname = row["fileName"].strip()
            if not url or not fname:
                continue

            fname, wt = _normalize_output_path(fname, max_fn)
            if wt:
                trunc += 1
                print(f"[WARN] Truncated: ...{fname[-50:]}")

            is_ugo = (has_type and row.get("type", "").lower() == "ugoira") or "ugoira" in url.lower()
            if is_ugo:
                fname = _replace_suffix(fname, ".webm")
                if not warned:
                    print("[WARN] CSV mode: ugoira frame delays unavailable — "
                          "using default FPS. Use JSON input for precise timing.")
                    warned = True

            entries.append({
                "url": url,
                "fname": fname,
                "is_ugoira": is_ugo,
                "frame_delays": None,
                "artwork_id": row.get("artworkId") or _infer_artwork_id(url),
            })

    if trunc:
        print(f"[INFO] {trunc} filename(s) truncated.")
    return entries


def _parse_json(path, max_fn):
    """Return list of entry dicts from JSON."""
    with _open_local(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries, trunc = [], 0
    for item in data:
        url = (item.get("original") or "").strip()
        if not url:
            continue

        fname, wt = _normalize_output_path(_make_fname_from_json(item), max_fn)
        if wt:
            trunc += 1
            print(f"[WARN] Truncated: ...{fname[-50:]}")

        uinfo = item.get("ugoiraInfo")
        is_ugo = uinfo is not None or "ugoira" in url.lower()
        if is_ugo:
            fname = _replace_suffix(fname, ".webm")
        delays = None
        if uinfo and "frames" in uinfo:
            delays = [fr["delay"] for fr in uinfo["frames"]]

        entries.append({
            "url": url,
            "fname": fname,
            "is_ugoira": is_ugo,
            "frame_delays": delays,
            "artwork_id": item.get("id") or item.get("idNum") or _infer_artwork_id(url),
        })

    if trunc:
        print(f"[INFO] {trunc} filename(s) truncated.")
    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    base = Path(args.base_dir).expanduser().resolve()

    try:
        proxy = _normalize_proxy(args.proxy)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if args.stop_aria2:
        try:
            stop_stats = _stop_all_aria2(args.rpc, args.secret)
        except Exception as e:
            print(f"[ERROR] Failed to stop aria2 tasks: {e}", file=sys.stderr)
            sys.exit(1)

        print("[DONE] aria2 stop requested: "
              f"removed {stop_stats['removed_active']} active + "
              f"{stop_stats['removed_waiting']} waiting task(s).")
        print("[INFO] aria2 remaining: "
              f"active={stop_stats['remaining_active']}, "
              f"waiting={stop_stats['remaining_waiting']}, "
              f"stopped={stop_stats['remaining_stopped']}")
        if not args.input:
            return

    if not args.input:
        print("[ERROR] input is required unless --stop-aria2 is used.", file=sys.stderr)
        sys.exit(2)

    inp = Path(args.input).expanduser().resolve()
    if not _path_exists(inp):
        print(f"[ERROR] Not found: {inp}", file=sys.stderr)
        sys.exit(1)

    ext = inp.suffix.lower()
    if ext == ".json":
        entries = _parse_json(str(inp), args.max_fn_bytes)
    elif ext == ".csv":
        entries = _parse_csv(str(inp), args.max_fn_bytes)
    else:
        print(f"[ERROR] Unsupported format: {ext}", file=sys.stderr)
        sys.exit(1)

    # Filter existing
    norm_dl, ugo_dl = [], []
    skipped = 0
    for e in entries:
        if _path_exists(base / e["fname"]):
            skipped += 1
        elif e["is_ugoira"]:
            ugo_dl.append(e)
        elif not args.ugoira_only:
            norm_dl.append(e)

    ugo_total = sum(1 for e in entries if e["is_ugoira"])
    print(f"[INFO] {len(entries)} total ({ugo_total} ugoira), "
          f"{skipped} exist, {len(norm_dl)} normal + {len(ugo_dl)} ugoira to download.")
    if proxy:
        print(f"[INFO] Using proxy: {proxy}")
    if _is_windows():
        print("[INFO] Windows mode: filename byte truncation disabled; long paths enabled for local file operations.")

    if not norm_dl and not ugo_dl:
        print("[INFO] Nothing to download.")
        return
    if args.dry_run:
        print(f"[DRY-RUN] {len(norm_dl)} aria2c + {len(ugo_dl)} ugoira.")
        return

    # ---- aria2c ----
    if norm_dl:
        try:
            ver = _rpc(args.rpc, "aria2.getVersion", _tok(args.secret))
            print("[INFO] aria2c " + ver.get("result", {}).get("version", "?"))
        except Exception as e:
            print(f"[ERROR] aria2c unreachable: {e}", file=sys.stderr)
            sys.exit(1)

        tok = _tok(args.secret)
        sent, batch = 0, []
        for e in norm_dl:
            sp = base / e["fname"]
            referer = _pixiv_referer(e["url"], e.get("artwork_id"))
            opts = _aria2_options(sp, referer, proxy)
            batch.append(("aria2.addUri", tok + [[e["url"]], opts]))
            if len(batch) >= args.batch_size:
                _rpc_multi(args.rpc, batch)
                sent += len(batch)
                print(f"\r[PROGRESS] aria2c: {sent}/{len(norm_dl)}", end="", flush=True)
                batch = []
        if batch:
            _rpc_multi(args.rpc, batch)
            sent += len(batch)
        print(f"\n[DONE] {sent} tasks sent to aria2c.")

    # ---- ugoira ----
    if ugo_dl:
        jobs = args.ugoira_jobs
        quality = args.ugoira_quality
        print(f"[INFO] Processing {len(ugo_dl)} ugoira "
              f"({jobs} threads, quality={quality}) ...")
        ok = fail = 0

        def _do_one(idx, entry):
            sp = base / entry["fname"]
            referer = _pixiv_referer(entry["url"], entry.get("artwork_id"))
            return idx, entry, sp, _process_ugoira(
                entry["url"], str(sp), referer, entry["frame_delays"],
                args.ugoira_fps, quality, proxy,
            )

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_do_one, i, e): i
                    for i, e in enumerate(ugo_dl, 1)}
            for fut in as_completed(futs):
                idx, entry, sp, success = fut.result()
                label = sp.name[:50] + ("..." if len(sp.name) > 50 else "")
                precise = entry["frame_delays"] is not None
                tag = "precise" if precise else f"{args.ugoira_fps}fps"
                status = "ok" if success else "FAIL"
                print(f"  [{idx}/{len(ugo_dl)}] {label} ({tag}) {status}")
                if success:
                    ok += 1
                else:
                    fail += 1
        print(f"[DONE] Ugoira: {ok} ok, {fail} failed.")


if __name__ == "__main__":
    main()
