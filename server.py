"""Tiny yt-dlp HTTP service for toolsed.com downloaders.

Endpoints:
  POST /extract  {"url": "https://ok.ru/video/..."} -> video metadata + direct format URLs
  GET  /stream?u=<direct url>&n=<filename>         -> proxied download (Range supported)

Deploy on Render as a Docker Web Service (same as cobalt). Free tier is fine.
"""
import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import requests
import yt_dlp
from flask import Flask, Response, jsonify, request, stream_with_context

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY")  # optional shared secret
ALLOWED_STREAM_HOSTS = re.compile(r"(^|\.)(okcdn\.ru|mycdn\.me|ok\.ru|odnoklassniki\.ru|vkuser\.net|vkuserlive\.net|vkuseraudio\.net)$", re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def public_host(host):
    """Blocks localhost/private networks so /stream can't be abused (SSRF)."""
    try:
        for info in socket.getaddrinfo(host, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


def authorized():
    # Browser downloads can't send headers, so /stream also accepts ?k=
    return not API_KEY or request.headers.get("X-Api-Key") == API_KEY or request.args.get("k") == API_KEY


@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "yt-dlp", "version": yt_dlp.version.__version__})


@app.post("/extract")
def extract():
    if not authorized():
        return jsonify({"ok": False, "message": "unauthorized"}), 401
    url = (request.get_json(silent=True) or {}).get("url", "")
    if not re.match(r"^https?://\S+$", url):
        return jsonify({"ok": False, "message": "invalid url"}), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp raises many types
        msg = str(e)
        if "Unsupported URL" in msg:
            return jsonify({"ok": False, "message": "unsupported"}), 400
        if "Private" in msg or "login" in msg.lower():
            return jsonify({"ok": False, "message": "private"}), 400
        return jsonify({"ok": False, "message": "fetch_failed", "detail": msg[:300]}), 502

    formats, audio = [], []
    for f in info.get("formats") or []:
        if not f.get("url") or str(f.get("protocol", "")).startswith(("m3u8", "http_dash", "f4m")):
            continue
        if f.get("vcodec") in (None, "none"):
            if f.get("acodec") not in (None, "none"):
                audio.append({"url": f["url"], "ext": f.get("ext"), "abr": f.get("abr"), "filesize": f.get("filesize") or f.get("filesize_approx")})
            continue
        if f.get("acodec") == "none":
            continue  # video-only stream without sound
        formats.append({
            "url": f.get("url"),
            "ext": f.get("ext"),
            "height": f.get("height"),
            "note": f.get("format_note"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
        })
    if not formats and info.get("url"):
        formats.append({"url": info["url"], "ext": info.get("ext"), "height": info.get("height"), "note": None, "filesize": None})
    return jsonify({
        "ok": True,
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "formats": formats,
        "audio": sorted(audio, key=lambda a: a.get("abr") or 0, reverse=True)[:3],
        "thumbnails": [t["url"] for t in (info.get("thumbnails") or []) if t.get("url")][-4:],
        "extractor": info.get("extractor_key"),
        "page": info.get("webpage_url"),
    })


@app.get("/stream")
def stream():
    if not authorized():
        return "unauthorized", 401
    u = request.args.get("u", "")
    name = re.sub(r'[\r\n"\\]', "", request.args.get("n", "video.mp4"))[:120]
    m = re.match(r"^https://([^/]+)", u)
    if not m or not public_host(m.group(1).split(":")[0]):
        return "forbidden", 403
    ref = request.args.get("r") or ("https://ok.ru/" if ALLOWED_STREAM_HOSTS.search(m.group(1)) else "")
    headers = {"User-Agent": UA}
    if ref.startswith("http"):
        headers["Referer"] = ref
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    up = requests.get(u, headers=headers, stream=True, timeout=30)
    if up.status_code not in (200, 206):
        return "video link expired, please try again", 502
    out = {
        "Content-Type": up.headers.get("content-type", "video/mp4"),
        "Content-Disposition": f"attachment; filename*=UTF-8''{requests.utils.quote(name)}",
        "Accept-Ranges": "bytes",
    }
    for h in ("content-length", "content-range"):
        if up.headers.get(h):
            out[h] = up.headers[h]
    return Response(stream_with_context(up.iter_content(1024 * 256)), status=up.status_code, headers=out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 9000)))
