import os
import re
import tempfile

DOWNLOAD_EXTENSIONS = {
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v",
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
}


def extract_urls(text: str):
    return re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE).findall(text)


def download_url_sync(url: str, max_bytes: int):
    import requests
    url_lower = url.lower()
    if any(x in url_lower for x in ["youtube.com", "youtu.be", "twitch.tv", "vimeo.com", "twitter.com", "x.com"]):
        try:
            import yt_dlp
            with tempfile.TemporaryDirectory() as tmpdir:
                opts = {"outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"), "format": "best[ext=mp4]/best[ext=webm]/best", "quiet": True, "no_warnings": True}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        return None, "Could not get video info"
                    files = [f for f in os.listdir(tmpdir) if os.path.isfile(os.path.join(tmpdir, f))]
                    if not files:
                        return None, "No file produced"
                    path = os.path.join(tmpdir, files[0])
                    if os.path.getsize(path) > max_bytes:
                        return None, f"File too large. Max: {max_bytes // (1024*1024)} MB."
                    with open(path, "rb") as f:
                        return f.read(), files[0]
        except ImportError:
            return None, "yt-dlp not installed. pip install yt-dlp"
        except Exception as e:
            return None, str(e)[:200]
    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        disposition = r.headers.get("content-disposition", "")
        filename = None
        if "filename=" in disposition:
            import urllib.parse
            for part in disposition.split(";"):
                if "filename=" in part:
                    filename = urllib.parse.unquote(part.split("filename=", 1)[1].strip(" \"").strip("'"))
                    break
        if not filename and "/" in content_type:
            ext = "." + content_type.split("/")[-1].split(";")[0].strip()
            if ext in DOWNLOAD_EXTENSIONS:
                filename = "download" + ext
        if not filename:
            from urllib.parse import urlparse
            name = os.path.basename(urlparse(url).path) or "download"
            filename = name if "." in name else name + ".bin"
        data = b""
        for chunk in r.iter_content(chunk_size=8192):
            data += chunk
            if len(data) > max_bytes:
                return None, f"File too large. Max: {max_bytes // (1024*1024)} MB."
        return data, filename
    except Exception as e:
        return None, str(e)[:200]
