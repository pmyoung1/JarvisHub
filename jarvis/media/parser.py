"""Parse movie title + year from common release / library filename patterns."""

from __future__ import annotations

import re
from pathlib import PurePath

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}

# Tokens after which the rest of a release name is junk.
_JUNK_TOKENS = {
    "2160p", "1080p", "1080i", "720p", "576p", "480p", "4k", "uhd", "8k",
    "bluray", "blu-ray", "bdrip", "brrip", "bdremux", "remux", "webrip", "web-dl",
    "webdl", "web", "hdtv", "dvdrip", "dvdscr", "dvd", "hdrip", "hdr", "hdr10",
    "dv", "dolbyvision", "sdr", "x264", "x265", "h264", "h265", "hevc", "avc",
    "xvid", "divx", "aac", "ac3", "dts", "dts-hd", "truehd", "atmos", "ddp5",
    "dd5", "flac", "10bit", "8bit", "proper", "repack", "extended", "unrated",
    "directors", "director's", "remastered", "imax", "limited", "internal",
    "multi", "dubbed", "subbed", "yify", "yts", "rarbg", "amzn", "nf", "dsnp",
    "hmax", "atvp", "criterion",
}

_YEAR_RE = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)")
_PAREN_YEAR_RE = re.compile(r"[\(\[]\s*(19[0-9]{2}|20[0-9]{2})\s*[\)\]]")
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_SEP_RE = re.compile(r"[._]+")
_SPACE_RE = re.compile(r"\s+")

# Folder names that are containers, not a movie title.
_GENERIC_DIRS = {
    "movies", "movie", "films", "film", "video", "videos", "media", "library",
    "extras", "featurettes", "sample", "samples", "subs", "subtitles",
    "bdmv", "stream", "video_ts", "downloads", "new folder",
}


def is_video_file(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() in VIDEO_EXTENSIONS


def parse_media_name(name: str) -> tuple[str, int | None]:
    """Return (title, year) parsed from a filename stem or folder name.

    Handles e.g.:
      Movie.Name.2010.1080p.BluRay.x264 -> ("Movie Name", 2010)
      Movie Name (2010)                 -> ("Movie Name", 2010)
      Movie_Name_[2010]_720p            -> ("Movie Name", 2010)
      2001.A.Space.Odyssey.1968.mkv     -> ("2001 A Space Odyssey", 1968)
    """
    stem = PurePath(name).name
    if PurePath(stem).suffix.lower() in VIDEO_EXTENSIONS:
        stem = PurePath(stem).stem

    # 1. Explicit "(2010)" / "[2010]" year — the clearest signal.
    paren = _PAREN_YEAR_RE.search(stem)
    if paren:
        year = int(paren.group(1))
        title_part = stem[: paren.start()]
        title = _tidy(_BRACKET_RE.sub(" ", title_part))
        if title:
            return title, year

    cleaned = _BRACKET_RE.sub(" ", stem)
    spaced = _SEP_RE.sub(" ", cleaned)
    spaced = spaced.replace(" - ", " ")
    tokens = [t for t in _SPACE_RE.split(spaced) if t]

    # 2. Dotted / scene-style: title tokens, then a year, then junk.
    #    Use the LAST plausible year that is not the first token, so
    #    "2001 A Space Odyssey 1968" keeps 2001 in the title.
    year_idx = None
    for i, tok in enumerate(tokens):
        clean_tok = tok.strip("()[]-")
        if _YEAR_RE.fullmatch(clean_tok) and i > 0:
            year_idx = i
    junk_idx = None
    for i, tok in enumerate(tokens):
        if tok.lower().strip("()[]-") in _JUNK_TOKENS and i > 0:
            junk_idx = i
            break

    if year_idx is not None and (junk_idx is None or year_idx < junk_idx):
        title = _tidy(" ".join(tokens[:year_idx]))
        year = int(tokens[year_idx].strip("()[]-"))
        if title:
            return title, year

    end = junk_idx if junk_idx is not None else len(tokens)
    if year_idx is not None and junk_idx is not None and year_idx > junk_idx:
        end = junk_idx
    title = _tidy(" ".join(tokens[:end]))
    return (title or _tidy(stem) or stem), None


def title_from_path(path: str | PurePath) -> tuple[str, int | None]:
    """Prefer the parent folder when it looks like 'Title (Year)'."""
    pure = PurePath(path)
    file_title, file_year = parse_media_name(pure.name)
    parent = pure.parent.name
    if parent and parent.lower() not in _GENERIC_DIRS:
        dir_title, dir_year = parse_media_name(parent)
        if dir_year and (not file_year or _looks_generic(file_title)):
            return dir_title, dir_year
        if dir_year and file_year == dir_year and len(dir_title) >= len(file_title):
            return dir_title, dir_year
        if not file_year and dir_year:
            return dir_title, dir_year
    return file_title, file_year


def _looks_generic(title: str) -> bool:
    low = title.lower().strip()
    return low in {"movie", "film", "video", "main", "feature", "title"} or low.isdigit()


def _tidy(text: str) -> str:
    text = _SPACE_RE.sub(" ", text).strip(" -_.,")
    # Drop a dangling "-" release-group style suffix.
    text = re.sub(r"\s+-\s*$", "", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    """Lowercase, drop punctuation and leading articles for fuzzy matching."""
    low = text.lower()
    low = re.sub(r"[^a-z0-9\s]", " ", low)
    low = _SPACE_RE.sub(" ", low).strip()
    low = re.sub(r"^(the|a|an)\s+", "", low)
    return low
