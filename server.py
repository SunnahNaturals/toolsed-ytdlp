import os
import re
import requests
import yt_dlp
from flask import Flask, Response, jsonify, request, stream_with_context

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY")
ALLOWED_STREAM_HOSTS = re.compile(r"(^|\.)(okcdn\.ru|mycdn\.me|ok\.ru|odnoklassniki\.ru)$", re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def authorized():
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
    except Exception as e:
        msg = str(e)
        if "Unsupported URL" in msg:
            return jsonify({"ok": False, "message": "unsupported"}), 400
        if "Private" in msg or "login" in msg.lower():
            return jsonify({"ok": False, "message": "private"}), 400
        return jsonify({"ok": False, "message": "fetch_failed", "detail": msg[:300]}), 502

    formats = []
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue
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
    })

@app.get("/stream")
def stream():
    if not authorized():
        return "unauthorized", 401
    u = request.args.get("u", "")
    name = re.sub(r'[\r\n"\\]', "", request.args.get("n", "video.mp4"))[:120]
    m = re.match(r"^https://([^/]+)", u)
    if not m or not ALLOWED_STREAM_HOSTS.search(m.group(1)):
        return "forbidden", 403
    headers = {"User-Agent": UA, "Referer": "https://ok.ru/"}
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
