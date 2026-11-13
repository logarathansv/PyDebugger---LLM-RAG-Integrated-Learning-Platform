"""YouTube transcript boundary and strict video-reference parsing."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Protocol, Sequence
from urllib.parse import parse_qs, urlparse


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


class TranscriptError(ValueError):
    """Raised when a transcript reference or remote transcript is unavailable."""


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class YouTubeTranscript:
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: tuple[TranscriptSegment, ...]


class TranscriptProvider(Protocol):
    def fetch(
        self, video_id: str, languages: Sequence[str] = ("en",)
    ) -> YouTubeTranscript: ...


class YouTubeTranscriptProvider:
    """Adapter around youtube-transcript-api's current object API."""

    def fetch(
        self, video_id: str, languages: Sequence[str] = ("en",)
    ) -> YouTubeTranscript:
        try:
            from requests import Session
            from youtube_transcript_api import YouTubeTranscriptApi

            class TranscriptSession(Session):
                def request(self, method, url, **kwargs):
                    kwargs.setdefault("timeout", (5, 20))
                    return super().request(method, url, **kwargs)

            fetched = YouTubeTranscriptApi(http_client=TranscriptSession()).fetch(
                video_id, languages=list(languages)
            )
        except Exception as error:
            reason = _friendly_transcript_error(type(error).__name__)
            raise TranscriptError(reason) from error

        segments_list: list[TranscriptSegment] = []
        for snippet in fetched:
            text = str(snippet.text).strip()
            start = float(snippet.start)
            duration = float(snippet.duration)
            if not math.isfinite(start) or not math.isfinite(duration):
                raise TranscriptError("The video transcript contains invalid timestamps")
            if start < 0 or duration < 0:
                raise TranscriptError("The video transcript contains invalid timestamps")
            if text:
                segments_list.append(TranscriptSegment(text, start, duration))
        segments = tuple(segments_list)
        if not segments:
            raise TranscriptError("The video transcript is empty")
        if fetched.video_id != video_id:
            raise TranscriptError("YouTube returned a transcript for an unexpected video")
        return YouTubeTranscript(
            video_id=fetched.video_id,
            language=fetched.language,
            language_code=fetched.language_code,
            is_generated=bool(fetched.is_generated),
            segments=segments,
        )


def parse_youtube_video_id(reference: str) -> str:
    """Accept a bare ID or a supported HTTPS YouTube URL; reject other hosts."""

    value = reference.strip()
    if len(value) > 2_048:
        raise TranscriptError("The YouTube reference is too long")
    if VIDEO_ID_PATTERN.fullmatch(value):
        return value

    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise TranscriptError("Use an HTTPS YouTube URL or an 11-character video ID")
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif host in YOUTUBE_HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            candidate = parts[1] if len(parts) == 2 and parts[0] in {
                "embed",
                "live",
                "shorts",
            } else ""
    else:
        raise TranscriptError("Only youtube.com and youtu.be URLs are supported")

    if not VIDEO_ID_PATTERN.fullmatch(candidate):
        raise TranscriptError("The YouTube URL does not contain a valid video ID")
    return candidate


def canonical_youtube_url(video_id: str) -> str:
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise TranscriptError("Invalid YouTube video ID")
    return f"https://www.youtube.com/watch?v={video_id}"


def _friendly_transcript_error(error_name: str) -> str:
    messages = {
        "TranscriptsDisabled": "Transcripts are disabled for this video",
        "NoTranscriptFound": "No transcript was found in the requested languages",
        "VideoUnavailable": "The YouTube video is unavailable",
        "RequestBlocked": "YouTube blocked transcript access from this server",
        "IpBlocked": "YouTube blocked transcript access from this server",
        "InvalidVideoId": "The YouTube video ID is invalid",
    }
    return messages.get(error_name, "The YouTube transcript could not be retrieved")
