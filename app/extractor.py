import io
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import List, Optional

import pypandoc
import pypdf
from youtube_transcript_api import YouTubeTranscriptApi


def extract_youtube_id(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    if "youtube.com" in parsed.netloc:
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            return query["v"][0]
    elif "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/")
    return None


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "txt":
        return file_bytes.decode("utf-8", errors="ignore")

    if ext == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
        return "\n".join(pages_text)

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        output = pypandoc.convert_file(tmp_path, "plain", format=ext)
        return output
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def extract_youtube_transcript(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL provided.")

    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            snippets = YouTubeTranscriptApi.get_transcript(video_id)
        else:
            ytt = YouTubeTranscriptApi()
            snippets = ytt.fetch(video_id)
    except Exception as exc:
        raise ValueError(f"Failed to fetch YouTube transcript: {str(exc)}") from exc

    texts = []
    for entry in snippets:
        if hasattr(entry, "text"):
            texts.append(entry.text)
        elif isinstance(entry, dict) and "text" in entry:
            texts.append(entry["text"])

    return " ".join(texts)


def split_into_sentences(text: str) -> List[str]:
    clean_text = re.sub(r"\s+", " ", text).strip()
    if not clean_text:
        return []
    pattern = r"(?<=[.!?])\s+"
    raw_sentences = re.split(pattern, clean_text)
    return [s.strip() for s in raw_sentences if s.strip()]
