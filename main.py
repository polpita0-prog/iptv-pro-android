# -*- coding: utf-8 -*-
import base64
import datetime
import html
import json
import os
import re
import threading
import time
import traceback
from urllib.parse import parse_qs, quote, urlparse
import uuid

import flet as ft
import requests
from requests.adapters import HTTPAdapter

try:
    import flet_video as ftv
    VideoControlClass = ftv.Video
    VideoMediaClass = ftv.VideoMedia
    HAS_FLET_VIDEO_PKG = True
except Exception:
    VideoControlClass = getattr(ft, "Video", None)
    VideoMediaClass = getattr(ft, "VideoMedia", None)
    HAS_FLET_VIDEO_PKG = False

TMDB_API_KEY = "6e2d327ee657c8671b3eda952cd260a0"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


# =====================================================================
# GLOBALNA SESJA HTTP (CONNECTION POOLING)
# =====================================================================
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


GLOBAL_HTTP = make_session()


# =====================================================================
# PREKOMPILOWANE WYRAŻENIA REGULARNE (OPTYMALIZACJA CPU)
# =====================================================================
PL_REGEX = re.compile(
    r"(?:^|[\s\|\[\(\-_:•/])(PL|POL|POLAND|POLSKA|POLSKIE|POLISH|LEKTOR|DUBBING|NAPISY[\s_]*PL|PL[\s_]*SUB|VIP[\s_]*PL)(?:$|[\s\|\]\)\-_:•/])"
    r"|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"
    r"|\b(tvp|polsat|tvn|canal\+|c\+|ale\s*kino|kino\s*polska|planete\+|domo\+|kuchnia\+|teletoon\+|minimini\+|ttv|puls|fokus\s*tv|eska|polo\s*tv|trwam|republika|wpolsce|wydarzenia\s*24|stopklatka|zoom\s*tv|wp\s*tv|nowa\s*tv|metro\s*tv|eleven\s*sports|polsat\s*sport|motowizja|stars\s*tv|kino\s*tv|gametoon|active\s*family|romance\s*tv|13\s*ulica|sci\s*fi|paramount|comedy\s*central|national\s*geographic|discovery|animal\s*planet|tlc|id\s*investigation|hbo|cinemax|filmbox|axn|fox|epic\s*drama|bbc\s*brit|bbc\s*earth|bbc\s*first|bbc\s*lifestyle|viasat|history|crime\s*investigation|sundance|red\s*carpet|antena\s*hd|tv\s*okazje|super\s*polsat|tv4|tv6|polsat\s*news|tvn24|tvp\s*info|biznes24|news24|pogoda24|4fun|vox\s*music|disco\s*polo|power\s*tv|nuta\s*tv|mixtape|hip\s*hop\s*tv)\b",
    re.IGNORECASE,
)

RE_HTML_TAGS = re.compile(r"<[^>]+>")
RE_SYMBOLS = re.compile(r"[★☆●•▪▫◆◇■□►▶◀◁⚡🔥]+")
RE_MULTI_SPACE = re.compile(r"\s{2,}")

RE_PREFIX_BRACKETS = re.compile(r"^(\s*[\|\[\(\{]\s*[A-Za-z0-9\+\-\s_]{1,12}\s*[\|\]\)\}]\s*[:\-_\|]*\s*)+")
RE_PREFIX_WORDS = re.compile(r"^(PL|POL|VIP|TV|VOD|SER|FILMY|SERIALE)\s*[:\|\-]+\s*", re.IGNORECASE)
RE_LEADING_JUNK = re.compile(r"^[#\-_:\|\•\*\+]+\s*")
RE_TRAILING_JUNK = re.compile(r"\s*[#\-_:\|]+$")

RE_MEDIA_SHORT_PREFIX = re.compile(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+")
RE_MEDIA_BRACKETS = re.compile(r"\[.*?\]|\{.*?\}")
RE_MEDIA_SE_EP = re.compile(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$")
RE_MEDIA_SEASON_PL = re.compile(r"\s*-\s*Sezon\s*\d+.*$", re.IGNORECASE)
RE_MEDIA_YEAR_PAREN = re.compile(r"\s*\(\d{4}\).*$")
RE_MEDIA_YEAR_STANDALONE = re.compile(r"\s+\b(19\d{2}|20\d{2})\b.*$")
RE_MEDIA_QUALITY_TAGS = re.compile(
    r"\b(FHD|UHD|4K|8K|HD|SD|1080p|720p|2160p|HEVC|H265|H264|x264|x265|WEB-DL|WEBRip|BluRay|BRRip|DVDRip|LEKTOR|DUBBING|NAPISY|PL|POL|EN|ENG|MULTI|RAW|VIP)\b.*$",
    re.IGNORECASE,
)
RE_MEDIA_SEPARATORS = re.compile(r"[_•\|]+")
RE_EXTRACT_YEAR = re.compile(r"(?:[\(\[\s])(19\d{2}|20\d{2})(?:[\)\]\s]|$)")
RE_PL_DIACRITICS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
RE_PL_COMMON_WORDS = re.compile(
    r"\b(jest|oraz|który|która|które|jego|jej|przez|kiedy|gdy|życie|historia|bohater|świat|film|serial|opowiada|losy|młody|czasie|jednak|tylko|więcej|między|kobieta|mężczyzna|rodzina|miasto|polski|polsce|razem|po|dla|przed|nad|pod)\b"
)
RE_EN_STOPWORDS = re.compile(
    r"\b(the|and|with|from|that|this|their|when|where|which|after|before|about|into|through|between|under|while|during|without|however|family|world|story|young|life|woman|man|years|find|finds|must|becomes|follows|friends)\b"
)
RE_TRAILING_DIGITS = re.compile(r"(\d+)$")
RE_M3U_ATTRS = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
RE_SE_EP_CLASSIFY = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})")


def strip_unwanted_chars(text: str) -> str:
    """Usuwa niepotrzebne znaki sterujące, znaczniki HTML i dziwne symbole z tekstu."""
    if not text:
        return ""
    s = html.unescape(str(text))
    s = s.replace("\xa0", " ").replace("\u200b", "").replace("\r", " ")
    s = RE_HTML_TAGS.sub(" ", s)
    s = RE_SYMBOLS.sub(" ", s)
    return RE_MULTI_SPACE.sub(" ", s).strip()


def clean_display_name(name: str) -> str:
    """Czyści nazwę wyświetlaną na liście z niepotrzebnych prefiksów typu |PL|, [VIP] itp."""
    if not name:
        return ""
    s = strip_unwanted_chars(name)
    s = RE_PREFIX_BRACKETS.sub("", s)
    s = RE_PREFIX_WORDS.sub("", s)
    s = RE_LEADING_JUNK.sub("", s)
    s = RE_TRAILING_JUNK.sub("", s)
    return s.strip() or strip_unwanted_chars(name)


def clean_media_title(title: str) -> str:
    """Agresywne czyszczenie tytułu filmu/serialu pod kątem wyszukiwarek opisów (TMDB, Wiki, TVMaze)."""
    if not title:
        return ""
    clean = clean_display_name(title)
    clean = RE_MEDIA_SHORT_PREFIX.sub("", clean)
    clean = RE_MEDIA_BRACKETS.sub(" ", clean)
    clean = RE_MEDIA_SE_EP.sub("", clean)
    clean = RE_MEDIA_SEASON_PL.sub("", clean)
    clean = RE_MEDIA_YEAR_PAREN.sub("", clean)
    clean = RE_MEDIA_YEAR_STANDALONE.sub("", clean)
    clean = RE_MEDIA_QUALITY_TAGS.sub("", clean)
    clean = RE_MEDIA_SEPARATORS.sub(" ", clean)
    clean = RE_MULTI_SPACE.sub(" ", clean)
    return clean.strip(" -|_:/•.")


def extract_year_from_title(title: str) -> str:
    if not title:
        return ""
    m = RE_EXTRACT_YEAR.search(str(title))
    return m.group(1) if m else ""


def is_polish_text(text: str) -> bool:
    if not text:
        return False
    return bool(PL_REGEX.search(str(text)))


def has_polish_readability(text: str) -> bool:
    """
    Sprawdza, czy opis jest faktycznie w języku polskim.
    Odrzuca opisy angielskie nawet jeśli zawierają pojedyncze polskie nazwisko.
    """
    if not text or len(text.strip()) < 10:
        return False
    lower_t = text.lower()
    en_hits = len(RE_EN_STOPWORDS.findall(lower_t))
    pl_hits = len(RE_PL_COMMON_WORDS.findall(lower_t))
    pl_chars = len(RE_PL_DIACRITICS.findall(text))

    if en_hits >= 2 and en_hits > pl_hits:
        return False
    if pl_chars >= 2:
        return True
    return pl_hits >= 2


def is_polish_item(item: dict, pl_cat_ids: set = None) -> bool:
    if not isinstance(item, dict):
        return False
    if pl_cat_ids and str(item.get("category_id", "")) in pl_cat_ids:
        return True
    fields = (
        item.get("name"),
        item.get("title"),
        item.get("category_name"),
        item.get("group"),
    )
    return any(is_polish_text(f) for f in fields if f)


def parse_int_safe(val, default=0) -> int:
    if val is None:
        return default
    try:
        return int(float(str(val).strip()))
    except Exception:
        m = RE_TRAILING_DIGITS.search(str(val))
        return int(m.group(1)) if m else default


def decode_b64_safe(val: str) -> str:
    if not val:
        return ""
    val_str = str(val).strip()
    try:
        raw = base64.b64decode(val_str, validate=True).decode("utf-8")
        if sum(1 for c in raw if c.isprintable() or c in "\n\r\t") / max(1, len(raw)) > 0.9:
            return strip_unwanted_chars(raw)
    except Exception:
        pass
    return strip_unwanted_chars(val_str)


def get_added_timestamp(item: dict) -> int:
    if not isinstance(item, dict):
        return 0
    return (
        parse_int_safe(item.get("added"), 0)
        or parse_int_safe(item.get("last_modified"), 0)
        or parse_int_safe(item.get("releaseDate"), 0)
    )


def get_numeric_stream_id(item: dict) -> int:
    if not isinstance(item, dict):
        return 0
    return (
        parse_int_safe(item.get("stream_id"), 0)
        or parse_int_safe(item.get("series_id"), 0)
        or parse_int_safe(item.get("id"), 0)
    )


def sort_categories_pl_first(categories: list) -> list:
    pl_cats = []
    other_cats = []
    for c in categories:
        if is_polish_text(c.get("category_name", "")):
            pl_cats.append(c)
        else:
            other_cats.append(c)
    return pl_cats + other_cats


def sort_streams_smart(
    items: list,
    kind: str,
    force_newest_only: bool = False,
    pl_cat_ids: set = None,
) -> list:
    if not items:
        return []

    if kind == "live" and not force_newest_only:
        pl_list = []
        other_list = []
        for it in items:
            if is_polish_item(it, pl_cat_ids):
                pl_list.append(it)
            else:
                other_list.append(it)
        return pl_list + other_list

    def _vod_series_key(it):
        pl_flag = 1 if is_polish_item(it, pl_cat_ids) else 0
        added_ts = get_added_timestamp(it)
        num_id = get_numeric_stream_id(it)
        return (pl_flag, added_ts, num_id)

    return sorted(items, key=_vod_series_key, reverse=True)


def extract_xtream_from_m3u_url(m3u_url):
    try:
        parsed = urlparse(m3u_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return None
        qs = parse_qs(parsed.query)
        username = (qs.get("username") or qs.get("user") or [None])[0]
        password = (qs.get("password") or qs.get("pass") or [None])[0]
        if username and password:
            server = f"{parsed.scheme}://{parsed.netloc}"
            return server, username, password
    except Exception:
        pass
    return None


# =====================================================================
# WIELOŹRÓDŁOWY SILNIK OPISÓW PL + NIEZAWODNE TŁUMACZENIE EN -> PL
# =====================================================================
MAX_MEM_CACHE = 600
tmdb_memory_cache = {}
epg_memory_cache = {}
translate_memory_cache = {}


def set_bounded_cache(cache_dict: dict, key: str, val):
    if len(cache_dict) >= MAX_MEM_CACHE:
        cache_dict.pop(next(iter(cache_dict)), None)
    cache_dict[key] = val


def translate_to_polish_free(text: str) -> str:
    """
    Automatycznie i niezawodnie tłumaczy opis obcojęzyczny (np. z angielskiego) na język polski.
    Wykorzystuje dwa niezależne endpointy Google Translate (gtx + dict-chrome-ex).
    """
    clean_t = strip_unwanted_chars(text)
    if not clean_t or len(clean_t) < 4:
        return ""
    if has_polish_readability(clean_t):
        return clean_t

    cache_k = clean_t[:160].lower()
    if cache_k in translate_memory_cache:
        return translate_memory_cache[cache_k]

    # Endpoint 1: Google Translate GTX (obsługuje długie teksty wielozdaniowe)
    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=pl&dt=t&q={quote(clean_t[:2200])}"
        )
        r = GLOBAL_HTTP.get(url, timeout=4.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(part[0] for part in data[0] if isinstance(part, list) and part and part[0])
                translated = strip_unwanted_chars(translated)
                if translated and translated.lower() != clean_t.lower():
                    set_bounded_cache(translate_memory_cache, cache_k, translated)
                    return translated
    except Exception:
        pass

    # Endpoint 2 (Fallback): Klient dict-chrome-ex JSON
    try:
        url2 = (
            "https://clients5.google.com/translate_a/t"
            f"?client=dict-chrome-ex&sl=auto&tl=pl&q={quote(clean_t[:1800])}"
        )
        r2 = GLOBAL_HTTP.get(url2, timeout=4.0)
        if r2.status_code == 200:
            data2 = r2.json()
            translated2 = ""
            if isinstance(data2, list) and data2:
                first_el = data2[0]
                if isinstance(first_el, list) and first_el:
                    translated2 = str(first_el[0])
                elif isinstance(first_el, str):
                    translated2 = first_el
            elif isinstance(data2, dict) and data2.get("sentences"):
                translated2 = "".join(s.get("trans", "") for s in data2["sentences"] if isinstance(s, dict))
            translated2 = strip_unwanted_chars(translated2)
            if translated2:
                set_bounded_cache(translate_memory_cache, cache_k, translated2)
                return translated2
    except Exception:
        pass

    return clean_t


def ensure_polish_text(text: str) -> str:
    """Gwarantuje, że tekst zostanie przetłumaczony na język polski, jeśli jest po angielsku."""
    clean = strip_unwanted_chars(text)
    if not clean:
        return ""
    if has_polish_readability(clean):
        return clean
    return translate_to_polish_free(clean)


def fetch_polish_wikipedia_plot(title_queries: list, media_hint: str = None) -> str:
    """Pobiera polski opis filmu/serialu bezpośrednio z polskiej Wikipedii."""
    suffixes = (
        [" (serial telewizyjny)", " (serial)", ""]
        if media_hint == "series"
        else [" (film)", ""]
    )
    tried = set()
    for base_q in title_queries:
        q_clean = clean_media_title(base_q)
        if not q_clean or len(q_clean) < 2:
            continue
        for sfx in suffixes:
            full_q = f"{q_clean}{sfx}".strip()
            if full_q.lower() in tried:
                continue
            tried.add(full_q.lower())
            try:
                search_url = (
                    "https://pl.wikipedia.org/w/api.php"
                    f"?action=query&list=search&srsearch={quote(full_q)}"
                    "&utf8=1&format=json&srlimit=3"
                )
                r = GLOBAL_HTTP.get(search_url, timeout=3.0)
                if r.status_code != 200:
                    continue
                s_items = ((r.json() or {}).get("query") or {}).get("search") or []
                for s_it in s_items:
                    page_title = s_it.get("title") or ""
                    if not page_title:
                        continue
                    if q_clean.lower() not in page_title.lower() and page_title.lower() not in q_clean.lower():
                        continue
                    sum_url = f"https://pl.wikipedia.org/api/rest_v1/page/summary/{quote(page_title)}"
                    rs = GLOBAL_HTTP.get(sum_url, timeout=3.0)
                    if rs.status_code == 200:
                        s_data = rs.json() or {}
                        if s_data.get("type") == "disambiguation":
                            continue
                        extract = strip_unwanted_chars(s_data.get("extract") or "")
                        if len(extract) > 45 and has_polish_readability(extract):
                            return extract
            except Exception:
                continue
    return ""


def fetch_tvmaze_details(title_queries: list) -> dict:
    """Alternatywne źródło metadanych z bazy TVMaze z automatycznym tłumaczeniem EN -> PL."""
    for q in title_queries:
        q_clean = clean_media_title(q)
        if not q_clean:
            continue
        try:
            url = f"https://api.tvmaze.com/singlesearch/shows?q={quote(q_clean)}"
            r = GLOBAL_HTTP.get(url, timeout=3.0)
            if r.status_code == 200:
                data = r.json() or {}
                summary_raw = strip_unwanted_chars(data.get("summary") or "")
                summary_pl = ensure_polish_text(summary_raw) if summary_raw else ""
                img_dict = data.get("image") or {}
                rating_val = ((data.get("rating") or {}).get("average")) or ""
                premiered = (data.get("premiered") or "")[:4]
                genres = ", ".join(data.get("genres") or [])
                return {
                    "title": strip_unwanted_chars(data.get("name") or q_clean),
                    "plot": summary_pl,
                    "poster": img_dict.get("original") or img_dict.get("medium"),
                    "rating": str(rating_val) if rating_val else "",
                    "year": premiered,
                    "genres": translate_to_polish_free(genres) if genres else "",
                }
        except Exception:
            continue
    return {}


def fetch_rich_polish_metadata(title: str, media_hint: str = None, xtream_info: dict = None) -> dict:
    """
    Kompleksowe pobieranie polskich opisów z wielu źródeł + GWARANCJA TŁUMACZENIA NA PL:
    1) TMDB pl-PL (szukanie multi + movie/tv + dopasowanie roku)
    2) Serwer Xtream VOD/Series Info (jeśli opis jest po angielsku -> automatyczne tłumaczenie na PL!)
    3) Polska Wikipedia API (pl.wikipedia.org)
    4) TMDB en-US -> automatyczne tłumaczenie EN -> PL
    5) TVMaze API -> automatyczne tłumaczenie EN -> PL
    """
    clean_title = clean_media_title(title)
    year_hint = extract_year_from_title(title)
    if not clean_title:
        return {}

    cache_key = f"{media_hint or 'multi'}:{clean_title.lower()}:{year_hint}"
    if cache_key in tmdb_memory_cache and not xtream_info:
        return tmdb_memory_cache[cache_key]

    res = {
        "title_pl": clean_title,
        "original_title": "",
        "poster": None,
        "backdrop": None,
        "plot": "",
        "plot_source": "",
        "rating": "",
        "votes": "",
        "year": year_hint,
        "genres": "",
        "cast": "",
        "director": "",
        "runtime": "",
        "seasons_info": "",
    }

    candidate_queries = [clean_title]
    for sep in (" / ", " - ", " | "):
        if sep in clean_title:
            parts = [p.strip() for p in clean_title.split(sep) if len(p.strip()) >= 2]
            for p in parts:
                if p not in candidate_queries:
                    candidate_queries.append(p)

    best = None
    m_type = "tv" if media_hint == "series" else "movie"

    for q_try in candidate_queries:
        try:
            endpoints = [
                f"https://api.themoviedb.org/3/search/{m_type}?api_key={TMDB_API_KEY}&query={quote(q_try)}&language=pl-PL"
                + (f"&year={year_hint}" if (year_hint and m_type == "movie") else ""),
                f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(q_try)}&language=pl-PL",
            ]
            for s_url in endpoints:
                r = GLOBAL_HTTP.get(s_url, timeout=3.5)
                if r.status_code == 200:
                    results = [
                        it for it in (r.json().get("results") or [])
                        if it.get("media_type") in (None, "movie", "tv")
                    ]
                    if results:
                        best = results[0]
                        if best.get("media_type") in ("movie", "tv"):
                            m_type = best["media_type"]
                        break
            if best:
                break
        except Exception:
            pass

    foreign_overview_candidates = []

    if best:
        tmdb_id = best.get("id")
        poster_path = best.get("poster_path")
        backdrop_path = best.get("backdrop_path")
        release = best.get("release_date") or best.get("first_air_date") or ""
        overview_pl = strip_unwanted_chars(best.get("overview") or "")

        res["title_pl"] = strip_unwanted_chars(best.get("title") or best.get("name") or clean_title)
        res["original_title"] = strip_unwanted_chars(best.get("original_title") or best.get("original_name") or "")
        if res["original_title"] and res["original_title"] not in candidate_queries:
            candidate_queries.append(res["original_title"])

        if best.get("vote_average"):
            res["rating"] = str(round(float(best.get("vote_average", 0)), 1))
        if best.get("vote_count"):
            res["votes"] = str(best.get("vote_count", ""))
        if release:
            res["year"] = release[:4]

        if tmdb_id:
            try:
                det_url = (
                    f"https://api.themoviedb.org/3/{m_type}/{tmdb_id}"
                    f"?api_key={TMDB_API_KEY}&language=pl-PL&append_to_response=credits"
                )
                rd = GLOBAL_HTTP.get(det_url, timeout=3.5)
                if rd.status_code == 200:
                    det_data = rd.json() or {}
                    if not overview_pl:
                        overview_pl = strip_unwanted_chars(det_data.get("overview") or "")
                    if not poster_path:
                        poster_path = det_data.get("poster_path")
                    if not backdrop_path:
                        backdrop_path = det_data.get("backdrop_path")
                    genres_list = [
                        strip_unwanted_chars(g.get("name"))
                        for g in (det_data.get("genres") or [])
                        if g.get("name")
                    ]
                    res["genres"] = ", ".join(genres_list[:4])

                    if m_type == "movie":
                        rt_m = det_data.get("runtime")
                        if rt_m:
                            res["runtime"] = f"{rt_m} min"
                    else:
                        s_cnt = det_data.get("number_of_seasons")
                        e_cnt = det_data.get("number_of_episodes")
                        if s_cnt:
                            res["seasons_info"] = f"Sezony: {s_cnt}" + (f" ({e_cnt} odc.)" if e_cnt else "")
                        ep_rt = det_data.get("episode_run_time") or []
                        if ep_rt:
                            res["runtime"] = f"~{ep_rt[0]} min/odc."

                    credits = det_data.get("credits") or {}
                    cast_raw = credits.get("cast") or []
                    cast_list = [strip_unwanted_chars(c.get("name")) for c in cast_raw[:6] if c.get("name")]
                    res["cast"] = ", ".join(cast_list)

                    crew_raw = credits.get("crew") or []
                    directors = [
                        strip_unwanted_chars(c.get("name"))
                        for c in crew_raw
                        if c.get("job") in ("Director", "Series Director", "Creator") and c.get("name")
                    ]
                    if not directors and det_data.get("created_by"):
                        directors = [strip_unwanted_chars(c.get("name")) for c in det_data.get("created_by") if c.get("name")]
                    if directors:
                        res["director"] = ", ".join(directors[:2])
            except Exception:
                pass

            if not overview_pl or not has_polish_readability(overview_pl):
                try:
                    en_url = f"https://api.themoviedb.org/3/{m_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language=en-US"
                    ren = GLOBAL_HTTP.get(en_url, timeout=3.0)
                    if ren.status_code == 200:
                        en_ov = strip_unwanted_chars(ren.json().get("overview") or "")
                        if en_ov:
                            foreign_overview_candidates.append(("TMDB EN -> PL", en_ov))
                except Exception:
                    pass

        if poster_path:
            res["poster"] = f"https://image.tmdb.org/t/p/w500{poster_path}"
        if backdrop_path:
            res["backdrop"] = f"https://image.tmdb.org/t/p/w780{backdrop_path}"

        if overview_pl and has_polish_readability(overview_pl):
            res["plot"] = overview_pl
            res["plot_source"] = "TMDB PL"
        elif overview_pl:
            foreign_overview_candidates.insert(0, ("TMDB -> PL", overview_pl))

    # Sprawdzenie danych bezpośrednio z serwera VOD Xtream Codes
    if xtream_info and isinstance(xtream_info, dict):
        xt_plot = strip_unwanted_chars(
            xtream_info.get("plot")
            or xtream_info.get("description")
            or xtream_info.get("overview")
            or ""
        )
        if xt_plot:
            if not res["plot"] and has_polish_readability(xt_plot):
                res["plot"] = xt_plot
                res["plot_source"] = "Serwer VOD PL"
            else:
                foreign_overview_candidates.insert(0, ("Serwer VOD (Tłumaczenie PL)", xt_plot))

        if not res["poster"]:
            res["poster"] = (
                xtream_info.get("movie_image")
                or xtream_info.get("cover_big")
                or xtream_info.get("cover")
            )
        if not res["genres"] and xtream_info.get("genre"):
            res["genres"] = ensure_polish_text(str(xtream_info.get("genre")))
        if not res["cast"] and (xtream_info.get("cast") or xtream_info.get("actors")):
            res["cast"] = strip_unwanted_chars(xtream_info.get("cast") or xtream_info.get("actors"))
        if not res["director"] and xtream_info.get("director"):
            res["director"] = strip_unwanted_chars(xtream_info.get("director"))
        if not res["year"]:
            res["year"] = str(xtream_info.get("releasedate") or xtream_info.get("releaseDate") or "")[:4]
        if not res["rating"] and xtream_info.get("rating"):
            res["rating"] = str(xtream_info.get("rating"))

    # Jeśli wciąż brak polskiego opisu -> sprawdzamy polską Wikipedię
    if not res["plot"]:
        wiki_plot = fetch_polish_wikipedia_plot(candidate_queries, media_hint=media_hint)
        if wiki_plot:
            res["plot"] = wiki_plot
            res["plot_source"] = "Wikipedia PL"

    # Jeśli opis jest w języku obcym (np. angielskim z serwera VOD lub TMDB EN) -> tłumaczymy na PL!
    if not res["plot"] and foreign_overview_candidates:
        for src_label, raw_foreign_txt in foreign_overview_candidates:
            translated_plot = translate_to_polish_free(raw_foreign_txt)
            if translated_plot:
                res["plot"] = translated_plot
                res["plot_source"] = src_label
                break

    # Ostateczny fallback: TVMaze (również z auto-tłumaczeniem na PL)
    if not res["plot"] or not res["poster"]:
        tvm = fetch_tvmaze_details(candidate_queries)
        if tvm:
            if not res["plot"] and tvm.get("plot"):
                res["plot"] = ensure_polish_text(tvm["plot"])
                res["plot_source"] = "TVMaze PL"
            if not res["poster"] and tvm.get("poster"):
                res["poster"] = tvm["poster"]
            if not res["rating"] and tvm.get("rating"):
                res["rating"] = tvm["rating"]
            if not res["year"] and tvm.get("year"):
                res["year"] = tvm["year"]
            if not res["genres"] and tvm.get("genres"):
                res["genres"] = tvm["genres"]

    if res["plot"] and not has_polish_readability(res["plot"]):
        res["plot"] = translate_to_polish_free(res["plot"])

    if res["plot"] or res["poster"]:
        set_bounded_cache(tmdb_memory_cache, cache_key, res)
    return res


class XtreamClient:

    def __init__(self, server, username, password):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.session = GLOBAL_HTTP
        self.user_info = {}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}

    def _api(self, params, timeout=15):
        url = f"{self.server}/player_api.php"
        full_params = {"username": self.username, "password": self.password}
        full_params.update(params)
        r = self.session.get(url, params=full_params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def login(self):
        last_err = None
        for _ in range(2):
            try:
                data = self._api({}, timeout=12)
                self.user_info = data.get("user_info", {})
                if (self.user_info or {}).get("auth") != 1:
                    raise ValueError("Nieprawidłowy login lub hasło.")
                return self.user_info
            except Exception as e:
                last_err = e
                time.sleep(0.3)
        raise last_err

    def get_categories(self, kind):
        action = {
            "live": "get_live_categories",
            "vod": "get_vod_categories",
            "series": "get_series_categories",
        }[kind]
        raw_cats = self._api({"action": action}) or []
        for c in raw_cats:
            if c.get("category_name"):
                c["category_name"] = strip_unwanted_chars(c["category_name"])
        self.pl_cat_ids[kind] = {
            str(c.get("category_id", ""))
            for c in raw_cats
            if is_polish_text(c.get("category_name", ""))
        }
        return sort_categories_pl_first(raw_cats)

    def get_streams(self, kind, category_id=None):
        action = {
            "live": "get_live_streams",
            "vod": "get_vod_streams",
            "series": "get_series",
        }[kind]
        params = {"action": action}
        if category_id and category_id not in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            params["category_id"] = category_id
        raw_streams = self._api(params, timeout=24) or []
        for s in raw_streams:
            if s.get("name"):
                s["name"] = clean_display_name(s["name"])
            if s.get("title"):
                s["title"] = clean_display_name(s["title"])
        if category_id == "__CATCHUP__":
            raw_streams = [
                s for s in raw_streams if parse_int_safe(s.get("tv_archive"), 0) == 1
            ]
        return sort_streams_smart(
            raw_streams,
            kind,
            force_newest_only=(category_id == "__RECENT__"),
            pl_cat_ids=self.pl_cat_ids.get(kind, set()),
        )

    def get_vod_info(self, vod_id):
        try:
            return self._api({"action": "get_vod_info", "vod_id": vod_id}, timeout=8) or {}
        except Exception:
            return {}

    def get_series_info(self, series_id):
        try:
            return self._api({"action": "get_series_info", "series_id": series_id}, timeout=10) or {}
        except Exception:
            return {}

    def get_short_epg(self, stream_id):
        if not stream_id:
            return []
        now_ts = time.time()
        cached = epg_memory_cache.get(str(stream_id))
        if cached and (now_ts - cached[0] < 120):
            return cached[1]
        try:
            data = self._api(
                {"action": "get_short_epg", "stream_id": stream_id, "limit": 4},
                timeout=4,
            )
            listings = data.get("epg_listings", []) if isinstance(data, dict) else []
            parsed = []
            for el in listings[:4]:
                title = decode_b64_safe(el.get("title", ""))
                desc = decode_b64_safe(el.get("description", ""))
                if desc and not has_polish_readability(desc):
                    desc = translate_to_polish_free(desc)
                start_t = (el.get("start") or "")[11:16]
                end_t = (el.get("end") or "")[11:16]
                if title:
                    line_str = f"🕒 {start_t} - {end_t} | {title}"
                    if desc:
                        line_str += f"\n{desc}"
                    parsed.append(line_str.strip())
            set_bounded_cache(epg_memory_cache, str(stream_id), (now_ts, parsed))
            return parsed
        except Exception:
            return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        if kind == "live":
            return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{container_ext}"
        if kind == "vod":
            return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{container_ext}"
        if kind == "series":
            return f"{self.server}/series/{self.username}/{self.password}/{stream_id}.{container_ext}"
        return ""


class M3UClient:

    def __init__(self, source):
        self.source = source.strip()
        self.server = "Lista M3U"
        self.username = "M3U Playlist"
        self.user_info = {"auth": 1, "exp_date": None}
        self.session = GLOBAL_HTTP
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map = {}
        self.direct_urls = {}

    def login(self):
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map = {}
        self.direct_urls = {}
        with self.session.get(self.source, timeout=25, stream=True) as r:
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            self._parse_m3u_lines(r.iter_lines(decode_unicode=True))
        return self.user_info

    def _classify_entry(self, url, group, name):
        lower_url = url.lower()
        lower_group = group.lower()
        ep_match = RE_SE_EP_CLASSIFY.search(name)
        if (
            "/series/" in lower_url
            or ep_match
            or any(k in lower_group for k in ("seriale", "series", "serial"))
        ):
            return "series", ep_match
        if (
            "/movie/" in lower_url
            or lower_url.endswith((".mp4", ".mkv", ".avi"))
            or any(
                k in lower_group
                for k in ("vod", "filmy", "movies", "film", "kino")
            )
        ):
            return "vod", None
        return "live", None

    def _parse_m3u_lines(self, lines_iterable):
        cat_sets = {"live": {}, "vod": {}, "series": {}}
        series_groups = {}
        extinf_line = None
        seq_id = 1

        for raw_line in lines_iterable:
            if not raw_line:
                continue
            line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                extinf_line = line
                continue
            if line.startswith("#"):
                continue

            url = line
            attrs = dict(RE_M3U_ATTRS.findall(extinf_line)) if extinf_line else {}
            raw_name = (
                extinf_line.split(",", 1)[1].strip()
                if (extinf_line and "," in extinf_line)
                else f"Kanał {seq_id}"
            )
            name = clean_display_name(raw_name)
            group = strip_unwanted_chars(attrs.get("group-title") or "Bez kategorii")
            logo = attrs.get("tvg-logo") or ""
            catchup = 1 if (attrs.get("catchup") or attrs.get("tvg-rec")) else 0
            kind, ep_match = self._classify_entry(url, group, name)

            if group not in cat_sets[kind]:
                cat_id = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cat_id
                self.categories[kind].append(
                    {"category_id": cat_id, "category_name": group}
                )
                if is_polish_text(group):
                    self.pl_cat_ids[kind].add(cat_id)
                self.streams_by_cat[kind][cat_id] = []
            else:
                cat_id = cat_sets[kind][group]

            item_id = f"m3u_item_{seq_id}"
            cur_num = seq_id
            seq_id += 1
            self.direct_urls[item_id] = url

            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cat_id].append({
                    "stream_id": item_id,
                    "category_id": cat_id,
                    "added": str(cur_num),
                    "name": name,
                    "group": group,
                    "stream_icon": logo,
                    "direct_url": url,
                    "tv_archive": catchup,
                    "kind": kind,
                })
            else:
                season_num = str(int(ep_match.group(1))) if ep_match else "1"
                ep_num = int(ep_match.group(2)) if ep_match else 1
                base_title = (
                    name[: ep_match.start()].strip(" -|:") if ep_match else name
                )
                series_key = (cat_id, base_title.lower())
                if series_key not in series_groups:
                    series_id = f"m3u_ser_{cur_num}"
                    series_groups[series_key] = series_id
                    self.streams_by_cat["series"][cat_id].append({
                        "series_id": series_id,
                        "category_id": cat_id,
                        "last_modified": str(cur_num),
                        "name": base_title,
                        "group": group,
                        "cover": logo,
                        "kind": "series",
                    })
                    self.series_map[series_id] = {"episodes": {}}
                else:
                    series_id = series_groups[series_key]
                eps = self.series_map[series_id]["episodes"].setdefault(
                    season_num, []
                )
                eps.append({
                    "id": item_id,
                    "episode_num": ep_num,
                    "title": name,
                    "direct_url": url,
                })
            extinf_line = None

        for k in ("live", "vod", "series"):
            self.categories[k] = sort_categories_pl_first(self.categories[k])

    def get_categories(self, kind):
        return self.categories.get(kind, [])

    def get_streams(self, kind, category_id=None):
        cat_map = self.streams_by_cat.get(kind, {})
        pl_ids = self.pl_cat_ids.get(kind, set())
        if not category_id or category_id in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            res = []
            for v in cat_map.values():
                res.extend(v)
            if category_id == "__CATCHUP__":
                res = [s for s in res if parse_int_safe(s.get("tv_archive"), 0) == 1]
            return sort_streams_smart(
                res,
                kind,
                force_newest_only=(category_id == "__RECENT__"),
                pl_cat_ids=pl_ids,
            )
        raw_list = cat_map.get(category_id, [])
        return sort_streams_smart(raw_list, kind, pl_cat_ids=pl_ids)

    def get_vod_info(self, vod_id):
        return {}

    def get_series_info(self, series_id):
        return self.series_map.get(series_id, {"episodes": {}})

    def get_short_epg(self, stream_id):
        return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        return self.direct_urls.get(stream_id, "")


def get_item_unique_id(item):
    if not isinstance(item, dict):
        return ""
    val = (
        item.get("stream_id")
        or item.get("series_id")
        or item.get("id")
        or item.get("category_id")
        or ""
    )
    return str(val)


def build_special_top_categories(kind: str, raw_cats: list) -> list:
    top = [
        {
            "category_id": "__ALL__",
            "category_name": "WSZYSTKIE (Polskie na górze)",
        }
    ]
    if kind == "live":
        top.append(
            {
                "category_id": "__CATCHUP__",
                "category_name": "KANAŁY Z ARCHIWUM (CATCH UP)",
            }
        )
    if kind in ("vod", "series"):
        top.append(
            {
                "category_id": "__RECENT__",
                "category_name": "OSTATNIO DODANE (NOWOŚCI NA GÓRZE)",
            }
        )
    return top + raw_cats


def make_iptv_bp_badge(size_px=42):
    return ft.Container(
        content=ft.Row(
            [
                ft.Text("▣", size=max(12, int(size_px * 0.40)), color="#a1a1aa"),
                ft.Column(
                    [
                        ft.Text(
                            "IPTV",
                            size=max(9, int(size_px * 0.25)),
                            weight=ft.FontWeight.BOLD,
                            color="#f4f4f5",
                            height=max(11, int(size_px * 0.29)),
                        ),
                        ft.Text(
                            "BP",
                            size=max(11, int(size_px * 0.32)),
                            weight=ft.FontWeight.BOLD,
                            color="#a1a1aa",
                            height=max(13, int(size_px * 0.36)),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=5,
        ),
        width=size_px * 1.6,
        height=size_px,
        bgcolor="#18181b",
        border_radius=10,
        border=ft.border.all(1.5, "#52525b"),
        alignment=ft.alignment.center,
    )


def main(page: ft.Page):
    try:
        page.title = "IPTV BP"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = "#101216"
        page.padding = 0

        stop_event = threading.Event()
        app_state = {
            "dialog_open": False,
            "settings_open": False,
            "search_open": False,
            "in_fullscreen_player": False,
            "in_detail_page": False,
            "osd_list_open": False,
            "zap_banner_visible": True,
            "paused_in_bg": False,
            "preview_seq": 0,
            "detail_seq": 0,
            "osd_hide_seq": 0,
            "zap_banner_seq": 0,
            "osd_cat_seq": 0,
            "num_buffer": "",
            "num_seq": 0,
            "last_back_press_ts": 0.0,
            "last_ok_ts": 0.0,
            "video_ctrl": None,
        }
        storage_lock = threading.Lock()
        storage_cache = {}
        pending_writes = {}
        write_timer_active = {"running": False}

        def init_storage_cache():
            keys_defaults = {
                "device_id": "",
                "device_name": "Android TV Box",
                "profiles": [],
                "processed_ids": [],
                "auto_vlc": False,
                "use_native_video_ctrl": bool(HAS_FLET_VIDEO_PKG),
                "auto_launch_vlc_on_zap": False,
                "auto_login": True,
                "only_pl": False,
                "vlc_mode": "vlc_clean",
                "favorites_v2": [],
                "server": "http://",
                "username": "",
                "password": "",
                "m3u_url": "",
                "active_profile_name": "",
                "last_session": {},
            }
            for k, d in keys_defaults.items():
                try:
                    val = page.client_storage.get(k)
                    storage_cache[k] = val if val is not None else d
                except Exception:
                    storage_cache[k] = d

        init_storage_cache()

        def cache_get(key, default=None):
            val = storage_cache.get(key)
            return val if val is not None else default

        def flush_storage_sync():
            with storage_lock:
                items_to_save = list(pending_writes.items())
                pending_writes.clear()
                write_timer_active["running"] = False
            for k_save, v_save in items_to_save:
                try:
                    page.client_storage.set(k_save, v_save)
                except Exception:
                    pass

        def cache_set(key, val):
            storage_cache[key] = val
            with storage_lock:
                pending_writes[key] = val
                if write_timer_active["running"]:
                    return
                write_timer_active["running"] = True

            def _flush_writer():
                time.sleep(0.25)
                if stop_event.is_set():
                    return
                flush_storage_sync()

            threading.Thread(target=_flush_writer, daemon=True).start()

        def safe_ui_update():
            if stop_event.is_set():
                return
            try:
                page.update()
            except Exception:
                pass

        def force_exit_app(_e=None):
            flush_storage_sync()
            stop_event.set()
            try:
                page.window.prevent_close = False
            except Exception:
                pass
            try:
                page.window.close()
            except Exception:
                pass

            def _kill():
                time.sleep(0.15)
                os._exit(0)

            threading.Thread(target=_kill, daemon=True).start()

        def on_lifecycle_change(e: ft.AppLifecycleStateChangeEvent):
            st_str = str(e.data).lower()
            if "detach" in st_str:
                flush_storage_sync()
                stop_event.set()
            elif "pause" in st_str or "inactive" in st_str or "hidden" in st_str:
                app_state["paused_in_bg"] = True
                flush_storage_sync()
            elif "resume" in st_str:
                app_state["paused_in_bg"] = False
                safe_ui_update()

        page.on_app_lifecycle_state_change = on_lifecycle_change
        page.on_disconnect = lambda _: stop_event.set()
        page.on_close = lambda _: stop_event.set()

        device_id = cache_get("device_id")
        if not device_id:
            device_id = f"android-{uuid.uuid4().hex[:6]}"
            cache_set("device_id", device_id)

        device_name = cache_get("device_name", "Android TV Box")
        profiles = list(cache_get("profiles", []))
        auto_vlc_saved = cache_get("auto_vlc", False)
        use_native_vid_saved = cache_get("use_native_video_ctrl", bool(HAS_FLET_VIDEO_PKG))
        auto_vlc_zap_saved = cache_get("auto_launch_vlc_on_zap", False)
        auto_login_saved = cache_get("auto_login", True)
        only_pl_saved = cache_get("only_pl", False)
        saved_vlc_mode = cache_get("vlc_mode", "vlc_clean")
        favorites_items = list(cache_get("favorites_v2", []))

        VLC_MODE_LABELS = {
            "vlc_clean": "Tryb VLC: vlc:// (Czysty)",
            "intent": "Tryb VLC: Android Intent",
            "vlc_full": "Tryb VLC: vlc://http://",
            "http": "Tryb VLC: Standard HTTP",
        }
        if saved_vlc_mode not in VLC_MODE_LABELS:
            saved_vlc_mode = "vlc_clean"

        creds = {
            "server": cache_get("server", "http://"),
            "username": cache_get("username", ""),
            "password": cache_get("password", ""),
            "m3u_url": cache_get("m3u_url", ""),
        }

        def has_saved_credentials():
            m3u_ok = bool((creds.get("m3u_url") or "").strip())
            srv = (creds.get("server") or "").strip()
            usr = (creds.get("username") or "").strip()
            pas = creds.get("password") or ""
            return m3u_ok or bool(srv and srv not in ("http://", "https://") and usr and pas)

        def register_or_update_profile(name_hint="", srv="", usr="", pas="", m3u=""):
            nonlocal profiles
            srv_c = (srv or "").strip()
            usr_c = (usr or "").strip()
            m3u_c = (m3u or "").strip()
            mode = "m3u" if (m3u_c and not usr_c) else "xtream"

            if mode == "xtream" and (not srv_c or srv_c in ("http://", "https://") or not usr_c):
                return ""
            if mode == "m3u" and not m3u_c:
                return ""

            existing_idx = -1
            for idx, p in enumerate(profiles):
                if mode == "xtream" and p.get("server") == srv_c and p.get("username") == usr_c:
                    existing_idx = idx
                    break
                if mode == "m3u" and p.get("m3u_url") == m3u_c:
                    existing_idx = idx
                    break

            if existing_idx >= 0:
                prof_name = name_hint or profiles[existing_idx].get("name") or f"Lista {existing_idx + 1}"
                profiles[existing_idx]["name"] = prof_name
                profiles[existing_idx]["password"] = pas
                profiles[existing_idx]["mode"] = mode
            else:
                host_short = (
                    srv_c.replace("http://", "").replace("https://", "")[:22]
                    if mode == "xtream"
                    else "M3U"
                )
                prof_name = name_hint or (f"{usr_c} @ {host_short}" if mode == "xtream" else f"Lista M3U #{len(profiles) + 1}")
                profiles = [p for p in profiles if p.get("name") != prof_name]
                profiles.insert(0, {
                    "name": prof_name,
                    "mode": mode,
                    "server": srv_c,
                    "username": usr_c,
                    "password": pas,
                    "m3u_url": m3u_c,
                    "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                })

            cache_set("profiles", profiles)
            cache_set("active_profile_name", prof_name)
            return prof_name

        if has_saved_credentials() and not profiles:
            register_or_update_profile(
                cache_get("active_profile_name", ""),
                creds["server"],
                creds["username"],
                creds["password"],
                creds["m3u_url"],
            )

        state = {
            "client": None,
            "kind": "live",
            "in_fav": False,
            "in_profiles": False,
            "in_dashboard": True,
            "view_mode": "dashboard",
            "active_cat": None,
            "active_series_id": "",
            "active_series_title": "",
            "active_profile_name": cache_get("active_profile_name", ""),
            "exp_date_str": "Unlimited",
            "categories": {"live": [], "vod": [], "series": []},
            "categories_cache": {"live": None, "vod": None, "series": None},
            "streams_cache": {},
            "streams": [],
            "episodes": [],
            "filtered": [],
            "osd_channels": [],
            "osd_idx": 0,
            "osd_cat_id": None,
            "osd_cat_name": "Kanały TV",
            "osd_cat_loading": False,
            "osd_epg_text": "Wybierz kanał z listy lub przełącz strzałkami Góra/Dół",
            "search_query": "",
            "only_pl": bool(only_pl_saved),
            "now_playing": "Brak aktywnego strumienia",
            "playing_url": "",
            "current_item": None,
            "previous_live_item": None,
            "is_playing": False,
            "paused": False,
            "volume": 100,
            "muted": False,
            "external_vlc_only": bool(auto_vlc_saved),
            "use_native_video_ctrl": bool(use_native_vid_saved and HAS_FLET_VIDEO_PKG),
            "auto_launch_vlc_on_zap": bool(auto_vlc_zap_saved),
            "auto_login": bool(auto_login_saved),
            "is_logging_in": False,
            "vlc_mode": saved_vlc_mode,
            "zone": "dashboard",
            "dash_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
        }

        VISIBLE_ROWS = 8
        OSD_VISIBLE_ROWS = 8

        def get_active_channel_list() -> list:
            return (
                state.get("osd_channels")
                or state.get("filtered")
                or state.get("streams")
                or favorites_items
                or []
            )

        def find_channel_index_and_number(item) -> tuple:
            ch_list = get_active_channel_list()
            uid = get_item_unique_id(item) if item else ""
            if uid:
                for idx, it in enumerate(ch_list):
                    if get_item_unique_id(it) == uid:
                        return idx, idx + 1, len(ch_list)
            return 0, 1, len(ch_list)

        # =====================================================================
        # OBSŁUGA KATEGORII WEWNĄTRZ BOCZNEJ LISTY OSD (LEWO / PRAWO)
        # =====================================================================
        def get_osd_categories_list() -> list:
            """Zwraca listę kategorii dostępnych do przełączania w bocznym panelu OSD."""
            if state.get("view_mode") == "episodes":
                return [
                    {
                        "category_id": "__EPISODES__",
                        "category_name": f"Odcinki: {state.get('active_series_title') or 'Serial'}",
                    }
                ]

            cur_item = state.get("current_item") or {}
            media_kind = cur_item.get("kind") or (state["kind"] if state["kind"] in ("live", "vod", "series") else "live")
            base_cats = list(state["categories_cache"].get(media_kind) or state["categories"].get(media_kind) or [])
            if not base_cats:
                base_cats = build_special_top_categories(media_kind, [])

            osd_cats = []
            if favorites_items:
                osd_cats.append(
                    {
                        "category_id": "__FAV__",
                        "category_name": f"⭐ ULUBIONE ({len(favorites_items)})",
                    }
                )
            for c in base_cats:
                if c.get("category_id") != "__FAV__":
                    osd_cats.append(c)
            return osd_cats

        def sync_osd_category_context():
            """Synchronizuje nazwę i ID aktualnej kategorii w OSD z bieżącym widokiem."""
            if state.get("view_mode") == "episodes":
                state["osd_cat_id"] = "__EPISODES__"
                state["osd_cat_name"] = f"Odcinki: {state.get('active_series_title') or 'Serial'}"
                state["osd_channels"] = list(state.get("episodes") or state.get("filtered") or [])
                return

            osd_cats = get_osd_categories_list()
            if state.get("in_fav"):
                target_id = "__FAV__"
            else:
                target_id = state.get("osd_cat_id") or state.get("active_cat") or "__ALL__"

            matched = next((c for c in osd_cats if c.get("category_id") == target_id), None)
            if not matched and osd_cats:
                matched = osd_cats[0]

            if matched:
                state["osd_cat_id"] = matched.get("category_id")
                state["osd_cat_name"] = matched.get("category_name") or "Kanały TV"

            if not state.get("osd_channels"):
                state["osd_channels"] = list(state.get("filtered") or state.get("streams") or favorites_items or [])

        def switch_osd_category(delta: int):
            """
            Przełącza kategorię kanałów w bocznej liście OSD (klawisze Lewo/Prawo)
            bez przerywania odtwarzania wideo na pełnym ekranie.
            """
            if not state["client"] or state.get("view_mode") == "episodes":
                return

            cur_item = state.get("current_item") or {}
            media_kind = cur_item.get("kind") or (state["kind"] if state["kind"] in ("live", "vod", "series") else "live")

            def _apply_cat_items(raw_items: list, cat_id: str, cat_name: str):
                only_pl = state.get("only_pl", False)
                pl_ids = state["client"].pl_cat_ids.get(media_kind, set()) if state["client"] else set()
                if only_pl and cat_id != "__FAV__":
                    filtered_items = [it for it in raw_items if is_polish_item(it, pl_ids)]
                else:
                    filtered_items = list(raw_items)

                state["osd_cat_id"] = cat_id
                state["osd_cat_name"] = cat_name
                state["osd_cat_loading"] = False
                state["osd_channels"] = filtered_items

                # Synchronizujemy także główny widok pod spodem
                if cat_id == "__FAV__":
                    state["in_fav"] = True
                    state["streams"] = list(favorites_items)
                    state["filtered"] = filtered_items
                else:
                    state["in_fav"] = False
                    state["active_cat"] = cat_id
                    state["streams"] = raw_items
                    state["filtered"] = filtered_items
                    cats_main = state["categories"].get(media_kind, [])
                    for i_c, c_obj in enumerate(cats_main):
                        if c_obj.get("category_id") == cat_id:
                            state["list_idx"] = i_c
                            break

                # Jeśli aktualnie grany kanał jest w nowej kategorii, ustaw na nim kursor
                cur_uid = get_item_unique_id(state.get("current_item"))
                new_cursor = 0
                if cur_uid:
                    for idx_it, it_obj in enumerate(filtered_items):
                        if get_item_unique_id(it_obj) == cur_uid:
                            new_cursor = idx_it
                            break
                state["osd_idx"] = new_cursor
                state["right_idx"] = new_cursor
                if filtered_items:
                    refresh_osd_epg_for_item(filtered_items[new_cursor])
                rebuild_UI()

            def _ switch_worker():
                app_state["osd_cat_seq"] += 1
                req_seq = app_state["osd_cat_seq"]

                # Upewnij się, że kategorie dla danego działu są pobrane
                if state["categories_cache"].get(media_kind) is None:
                    try:
                        raw_c = state["client"].get_categories(media_kind)
                        full_c = build_special_top_categories(media_kind, raw_c)
                        state["categories_cache"][media_kind] = full_c
                        state["categories"][media_kind] = full_c
                    except Exception:
                        pass

                osd_cats = get_osd_categories_list()
                if not osd_cats:
                    return

                cur_cat_id = state.get("osd_cat_id") or ("__FAV__" if state.get("in_fav") else (state.get("active_cat") or "__ALL__"))
                cur_idx = 0
                for idx, c in enumerate(osd_cats):
                    if c.get("category_id") == cur_cat_id:
                        cur_idx = idx
                        break

                next_idx = (cur_idx + delta) % len(osd_cats)
                target_cat = osd_cats[next_idx]
                target_id = target_cat.get("category_id")
                target_name = target_cat.get("category_name") or "Kategoria"

                if target_id == "__FAV__":
                    _apply_cat_items(list(favorites_items), "__FAV__", target_name)
                    return

                cache_key = f"{media_kind}:{target_id}"
                if cache_key in state["streams_cache"]:
                    _apply_cat_items(state["streams_cache"][cache_key], target_id, target_name)
                    return

                state["osd_cat_id"] = target_id
                state["osd_cat_name"] = f"⏳ {target_name}..."
                state["osd_cat_loading"] = True
                rebuild_UI()

                try:
                    fetched_items = state["client"].get_streams(media_kind, target_id)
                    state["streams_cache"][cache_key] = fetched_items
                    if req_seq != app_state["osd_cat_seq"] or stop_event.is_set():
                        return
                    _apply_cat_items(fetched_items, target_id, target_name)
                except Exception as e:
                    state["osd_cat_loading"] = False
                    show_toast(f"Błąd zmiany kategorii OSD: {e}", "#dc2626")
                    rebuild_UI()

            threading.Thread(target=_switch_worker, daemon=True).start()

        status_debounce = {"running": False}

        def publish_status():
            if stop_event.is_set() or app_state["paused_in_bg"]:
                return
            if status_debounce["running"]:
                return
            status_debounce["running"] = True

            def _worker():
                time.sleep(0.4)
                status_debounce["running"] = False
                if stop_event.is_set() or app_state["paused_in_bg"]:
                    return
                try:
                    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                    status_topic = f"iptv_v4_pro_{clean_id}_status"
                    if state["is_playing"]:
                        st_lbl = "PAUZA" if state["paused"] else "ODTWARZANIE"
                    else:
                        st_lbl = "ZATRZYMANO"

                    srv_txt = (
                        state["active_profile_name"]
                        if state["client"]
                        else "Ekran logowania"
                    )
                    prof_summary = []
                    for p in profiles:
                        prof_summary.append({
                            "name": p.get("name", "Lista"),
                            "mode": p.get("mode", "xtream"),
                            "host": (p.get("server") or p.get("m3u_url") or "")[:30],
                            "added_at": p.get("added_at", ""),
                            "active": (
                                state["client"] is not None
                                and p.get("name") == state["active_profile_name"]
                            ),
                        })

                    snapshot = {
                        "type": "player_status",
                        "device_name": cache_get("device_name", device_name),
                        "device_id": clean_id,
                        "ts": int(time.time() * 1000),
                        "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
                        "state_label": st_lbl,
                        "now_playing": state["now_playing"],
                        "active_server": srv_txt,
                        "only_pl": state["only_pl"],
                        "current_tab": (
                            "menu"
                            if state["in_dashboard"]
                            else ("listy" if state["in_profiles"] else ("ulubione" if state["in_fav"] else state["kind"]))
                        ),
                        "volume": state["volume"],
                        "muted": state["muted"],
                        "profiles": prof_summary,
                    }
                    GLOBAL_HTTP.post(
                        f"https://ntfy.sh/{status_topic}",
                        data=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
                        timeout=4,
                    )
                except Exception:
                    pass

            threading.Thread(target=_worker, daemon=True).start()

        # =====================================================================
        # OBSŁUGA ODTWARZACZA + ZMIANA KANAŁÓW BEZ WYCHODZENIA (OSD LIVE TV)
        # =====================================================================
        def show_toast(msg: str, color: str = "#27272a"):
            try:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(msg, color="#ffffff", weight=ft.FontWeight.BOLD),
                    bgcolor=color,
                    duration=2200,
                )
                page.snack_bar.open = True
                safe_ui_update()
            except Exception:
                pass

        def schedule_zap_banner_autohide(seconds: float = 4.5):
            app_state["zap_banner_visible"] = True
            app_state["zap_banner_seq"] += 1
            cur_seq = app_state["zap_banner_seq"]

            def _hide():
                time.sleep(seconds)
                if stop_event.is_set() or cur_seq != app_state["zap_banner_seq"]:
                    return
                if app_state["in_fullscreen_player"] and not app_state["osd_list_open"]:
                    app_state["zap_banner_visible"] = False
                    rebuild_UI()

            threading.Thread(target=_hide, daemon=True).start()

        def refresh_osd_epg_for_item(item: dict):
            if not item or not state["client"]:
                return
            item_kind = item.get("kind") or state["kind"]
            if item_kind != "live":
                state["osd_epg_text"] = item.get("name") or item.get("title") or ""
                return

            sid = get_item_unique_id(item)

            def _epg_worker():
                lines = state["client"].get_short_epg(sid)
                if lines:
                    state["osd_epg_text"] = "   •   ".join(l.replace("\n", " — ") for l in lines[:2])
                else:
                    state["osd_epg_text"] = "Brak danych EPG dla tego kanału"
                if app_state["in_fullscreen_player"]:
                    rebuild_UI()

            threading.Thread(target=_epg_worker, daemon=True).start()

        def launch_external_vlc(stream_url: str, title: str = "IPTV Stream"):
            if not stream_url:
                return
            mode = state.get("vlc_mode", "vlc_clean")
            clean_no_proto = re.sub(r"^https?://", "", stream_url)
            try:
                if mode == "vlc_clean":
                    target_url = f"vlc://{clean_no_proto}"
                elif mode == "vlc_full":
                    target_url = f"vlc://{stream_url}"
                elif mode == "intent":
                    scheme = "https" if stream_url.startswith("https") else "http"
                    safe_t = quote(title)
                    target_url = (
                        f"intent://{clean_no_proto}#Intent;"
                        f"scheme={scheme};type=video/*;"
                        f"package=org.videolan.vlc;"
                        f"S.title={safe_t};end"
                    )
                else:
                    target_url = stream_url
                page.launch_url(target_url)
            except Exception as e:
                show_toast(f"Błąd uruchamiania VLC: {e}", "#dc2626")

        def is_favorite(item: dict) -> bool:
            uid = get_item_unique_id(item)
            return any(get_item_unique_id(f) == uid for f in favorites_items) if uid else False

        def toggle_favorite(item: dict):
            nonlocal favorites_items
            if not item:
                return
            uid = get_item_unique_id(item)
            if not uid:
                return
            if is_favorite(item):
                favorites_items = [f for f in favorites_items if get_item_unique_id(f) != uid]
                show_toast("Usunięto z Ulubionych", "#3f3f46")
            else:
                fav_copy = dict(item)
                fav_copy.setdefault("kind", state["kind"])
                favorites_items.insert(0, fav_copy)
                show_toast("Dodano do Ulubionych ⭐", "#ca8a04")
            cache_set("favorites_v2", favorites_items)
            if state["in_fav"]:
                state["streams"] = list(favorites_items)
                apply_filter_and_rebuild()
            else:
                rebuild_UI()

        def build_url_for_item(item: dict) -> str:
            if not item or not state["client"]:
                return ""
            if item.get("direct_url"):
                return item["direct_url"]
            item_kind = item.get("kind") or state["kind"]
            sid = get_item_unique_id(item)
            default_ext = "ts" if item_kind == "live" else (item.get("container_extension") or "mp4")
            return state["client"].build_stream_url(item_kind, sid, default_ext, item=item)

        def play_stream_item(item: dict, force_external: bool = False, keep_osd_open: bool = False):
            if not item or not state["client"]:
                return
            url = build_url_for_item(item)
            if not url:
                show_toast("Brak adresu URL dla tego strumienia.", "#dc2626")
                return

            title = item.get("name") or item.get("title") or "Kanał IPTV"
            cur_playing = state.get("current_item")
            if cur_playing and get_item_unique_id(cur_playing) != get_item_unique_id(item):
                state["previous_live_item"] = cur_playing

            state["current_item"] = item
            state["playing_url"] = url
            state["now_playing"] = title
            state["is_playing"] = True
            state["paused"] = False

            sync_osd_category_context()

            # Synchronizacja indeksu na liście głównej i liście OSD
            ch_idx, _, _ = find_channel_index_and_number(item)
            state["osd_idx"] = ch_idx
            state["right_idx"] = ch_idx

            cache_set("last_session", {
                "title": title,
                "url": url,
                "stream_id": get_item_unique_id(item),
                "kind": item.get("kind") or state["kind"],
            })
            publish_status()
            refresh_osd_epg_for_item(item)

            use_ext = (
                force_external
                or state["external_vlc_only"]
                or not state["use_native_video_ctrl"]
                or VideoControlClass is None
            )

            if use_ext:
                launch_external_vlc(url, title)
                rebuild_UI()
                return

            try:
                media_obj = VideoMediaClass(url)
                if app_state["video_ctrl"] is None:
                    app_state["video_ctrl"] = VideoControlClass(
                        expand=True,
                        playlist=[media_obj],
                        autoplay=True,
                        volume=0 if state["muted"] else state["volume"],
                        show_controls=False,
                    )
                else:
                    vc = app_state["video_ctrl"]
                    vc.playlist = [media_obj]
                    vc.autoplay = True
                    try:
                        vc.jump_to(0)
                        vc.play()
                    except Exception:
                        pass
                app_state["in_fullscreen_player"] = True
                app_state["osd_list_open"] = keep_osd_open
                schedule_zap_banner_autohide(4.5)
                rebuild_UI()
            except Exception:
                launch_external_vlc(url, title)
                rebuild_UI()

        def stop_playback():
            state["is_playing"] = False
            state["paused"] = False
            app_state["in_fullscreen_player"] = False
            app_state["osd_list_open"] = False
            if app_state["video_ctrl"] is not None:
                try:
                    app_state["video_ctrl"].pause()
                except Exception:
                    pass
            publish_status()
            rebuild_UI()

        def zap_channel(delta: int):
            """Przełącza kanał w górę/dół bez wychodzenia z pełnoekranowego odtwarzacza."""
            ch_list = get_active_channel_list()
            if not ch_list:
                return
            cur_idx, _, total = find_channel_index_and_number(state.get("current_item"))
            new_idx = (cur_idx + delta) % total
            next_item = ch_list[new_idx]
            play_stream_item(
                next_item,
                force_external=state["auto_launch_vlc_on_zap"],
                keep_osd_open=app_state["osd_list_open"],
            )

        def zap_to_number(ch_number: int):
            """Przełącza bezpośrednio na kanał o wybranym numerze (1..N) bez wychodzenia z odtwarzacza."""
            ch_list = get_active_channel_list()
            if not ch_list:
                return
            target_idx = max(0, min(len(ch_list) - 1, ch_number - 1))
            play_stream_item(
                ch_list[target_idx],
                force_external=state["auto_launch_vlc_on_zap"],
                keep_osd_open=False,
            )

        def recall_previous_channel():
            """Powrót do poprzednio oglądanego kanału (klawisz 0 lub Strzałka w prawo)."""
            prev_it = state.get("previous_live_item")
            if prev_it:
                play_stream_item(prev_it, force_external=state["auto_launch_vlc_on_zap"], keep_osd_open=False)
            else:
                show_toast("Brak poprzedniego kanału w historii.", "#3f3f46")

        def toggle_osd_channel_drawer():
            """Otwiera/zamyka boczną listę kanałów na pełnym ekranie podczas oglądania."""
            if not app_state["osd_list_open"]:
                sync_osd_category_context()
                cur_idx, _, _ = find_channel_index_and_number(state.get("current_item"))
                state["osd_idx"] = cur_idx
                app_state["osd_list_open"] = True
                app_state["zap_banner_visible"] = True
            else:
                app_state["osd_list_open"] = False
                schedule_zap_banner_autohide(3.5)
            rebuild_UI()

        def handle_numeric_key_input(digit_str: str):
            """Obsługa bezpośredniego wpisywania numeru kanału z pilota (np. 12 -> Kanał 12)."""
            app_state["num_buffer"] = (app_state["num_buffer"] + digit_str)[-4:]
            app_state["num_seq"] += 1
            cur_seq = app_state["num_seq"]
            rebuild_UI()

            def _commit_number():
                time.sleep(1.4)
                if stop_event.is_set() or cur_seq != app_state["num_seq"]:
                    return
                buf = app_state["num_buffer"]
                app_state["num_buffer"] = ""
                if not buf:
                    return
                num_val = parse_int_safe(buf, 0)
                if num_val == 0:
                    recall_previous_channel()
                elif num_val > 0:
                    if app_state["in_fullscreen_player"]:
                        zap_to_number(num_val)
                    else:
                        items = state["filtered"]
                        if items:
                            idx = max(0, min(len(items) - 1, num_val - 1))
                            on_stream_row_click(idx, items[idx])

            threading.Thread(target=_commit_number, daemon=True).start()

        # =====================================================================
        # LOGIKA POBIERANIA DANYCH I FILTROWANIA
        # =====================================================================
        preview_title_txt = ft.Text("Wybierz pozycję z listy", size=20, weight=ft.FontWeight.BOLD, color="#f4f4f5")
        preview_meta_txt = ft.Text("", size=13, color="#eab308", weight=ft.FontWeight.W_600)
        preview_plot_txt = ft.Text(
            "Najedź na kanał, film lub serial, aby pobrać szczegółowy polski opis oraz program EPG.",
            size=13,
            color="#d4d4d8",
            max_lines=12,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        preview_poster_img = ft.Image(
            src="",
            width=150,
            height=210,
            fit=ft.ImageFit.COVER,
            border_radius=8,
            visible=False,
        )

        def update_preview_async(item: dict):
            if not item:
                return
            app_state["preview_seq"] += 1
            cur_seq = app_state["preview_seq"]
            title = item.get("name") or item.get("title") or "Brak tytułu"
            icon_url = item.get("stream_icon") or item.get("cover") or ""

            preview_title_txt.value = title
            preview_meta_txt.value = "Ładowanie informacji PL..."
            preview_plot_txt.value = "Pobieranie i tłumaczenie opisu na język polski..."
            if icon_url:
                preview_poster_img.src = icon_url
                preview_poster_img.visible = True
            else:
                preview_poster_img.visible = False
            safe_ui_update()

            def _bg():
                time.sleep(0.18)
                if cur_seq != app_state["preview_seq"] or stop_event.is_set():
                    return
                item_kind = item.get("kind") or state["kind"]
                if item_kind == "live" and state["client"]:
                    sid = get_item_unique_id(item)
                    epg_lines = state["client"].get_short_epg(sid)
                    if cur_seq != app_state["preview_seq"]:
                        return
                    preview_meta_txt.value = f"Kanał TV na żywo | Grupa: {item.get('group') or state.get('osd_cat_name') or 'TV'}"
                    preview_plot_txt.value = "\n\n".join(epg_lines) if epg_lines else "Brak aktualnego programu EPG dla tego kanału."
                    safe_ui_update()
                else:
                    xt_info = {}
                    sid = get_item_unique_id(item)
                    if state["client"] and sid:
                        if item_kind == "vod":
                            vod_resp = state["client"].get_vod_info(sid) or {}
                            xt_info = vod_resp.get("info") or vod_resp.get("movie_data") or {}
                        elif item_kind == "series":
                            ser_resp = state["client"].get_series_info(sid) or {}
                            xt_info = ser_resp.get("info") or {}
                    if not xt_info and (item.get("plot") or item.get("description")):
                        xt_info = {"plot": item.get("plot") or item.get("description")}

                    meta = fetch_rich_polish_metadata(title, media_hint=item_kind, xtream_info=xt_info)
                    if cur_seq != app_state["preview_seq"]:
                        return
                    meta_parts = []
                    if meta.get("year"):
                        meta_parts.append(f"📅 {meta['year']}")
                    if meta.get("rating"):
                        meta_parts.append(f"⭐ {meta['rating']}/10")
                    if meta.get("runtime"):
                        meta_parts.append(f"⏱ {meta['runtime']}")
                    if meta.get("genres"):
                        meta_parts.append(meta["genres"])
                    if meta.get("plot_source"):
                        meta_parts.append(f"[{meta['plot_source']}]")

                    preview_title_txt.value = meta.get("title_pl") or title
                    preview_meta_txt.value = "  •  ".join(meta_parts) if meta_parts else "Film / Serial VOD (PL)"
                    plot_str = meta.get("plot") or "Brak opisu w bazach zewnętrznych."
                    if meta.get("director"):
                        plot_str += f"\n\n🎬 Reżyseria: {meta['director']}"
                    if meta.get("cast"):
                        plot_str += f"\n👥 Obsada: {meta['cast']}"
                    preview_plot_txt.value = plot_str
                    if meta.get("poster"):
                        preview_poster_img.src = meta["poster"]
                        preview_poster_img.visible = True
                    safe_ui_update()

            threading.Thread(target=_bg, daemon=True).start()

        def apply_filter_and_rebuild():
            q = (state.get("search_query") or "").strip().lower()
            only_pl = state.get("only_pl", False)
            pl_ids = (
                state["client"].pl_cat_ids.get(state["kind"], set())
                if state["client"]
                else set()
            )
            base_list = state["episodes"] if state["view_mode"] == "episodes" else state["streams"]
            res = []
            for it in base_list:
                nm = (it.get("name") or it.get("title") or "").lower()
                if q and q not in nm:
                    continue
                if only_pl and state["view_mode"] != "episodes":
                    if not is_polish_item(it, pl_ids):
                        continue
                res.append(it)
            state["filtered"] = res
            state["osd_channels"] = list(res)
            sync_osd_category_context()
            state["right_idx"] = min(state["right_idx"], max(0, len(res) - 1))
            if res:
                update_preview_async(res[state["right_idx"]])
            rebuild_UI()

        def load_category_streams(cat_id: str):
            if not state["client"]:
                return
            state["active_cat"] = cat_id
            state["osd_cat_id"] = cat_id
            for c_obj in state["categories"].get(state["kind"], []):
                if c_obj.get("category_id") == cat_id:
                    state["osd_cat_name"] = c_obj.get("category_name") or "Kategoria"
                    break
            state["view_mode"] = "streams"
            cache_key = f"{state['kind']}:{cat_id}"
            if cache_key in state["streams_cache"]:
                state["streams"] = state["streams_cache"][cache_key]
                state["right_idx"] = 0
                apply_filter_and_rebuild()
                return

            show_toast("Pobieranie listy strumieni...", "#2563eb")

            def _worker():
                try:
                    items = state["client"].get_streams(state["kind"], cat_id)
                    state["streams_cache"][cache_key] = items
                    state["streams"] = items
                    state["right_idx"] = 0
                    apply_filter_and_rebuild()
                except Exception as e:
                    show_toast(f"Błąd pobierania listy: {e}", "#dc2626")

            threading.Thread(target=_worker, daemon=True).start()

        def open_kind_section(kind: str):
            if not state["client"]:
                return
            state["kind"] = kind
            state["in_dashboard"] = False
            state["in_fav"] = False
            state["in_profiles"] = False
            state["view_mode"] = "streams"
            state["zone"] = "left"
            state["list_idx"] = 0
            state["right_idx"] = 0

            def _worker():
                try:
                    if state["categories_cache"].get(kind) is None:
                        raw_c = state["client"].get_categories(kind)
                        full_c = build_special_top_categories(kind, raw_c)
                        state["categories_cache"][kind] = full_c
                    state["categories"][kind] = state["categories_cache"][kind]
                    first_cat = state["categories"][kind][0]["category_id"] if state["categories"][kind] else "__ALL__"
                    load_category_streams(first_cat)
                except Exception as e:
                    show_toast(f"Błąd kategorii: {e}", "#dc2626")

            threading.Thread(target=_worker, daemon=True).start()

        def open_series_episodes(series_item: dict):
            if not state["client"] or not series_item:
                return
            sid = get_item_unique_id(series_item)
            stitle = series_item.get("name") or series_item.get("title") or "Serial"
            show_toast(f"Wczytywanie odcinków: {stitle}...", "#2563eb")

            def _worker():
                try:
                    info = state["client"].get_series_info(sid) or {}
                    eps_dict = info.get("episodes") or {}
                    flat_eps = []
                    seasons_sorted = sorted(eps_dict.keys(), key=lambda x: parse_int_safe(x, 0))
                    for s_num in seasons_sorted:
                        ep_list = eps_dict.get(s_num) or []
                        for ep in ep_list:
                            ep_num = ep.get("episode_num") or 1
                            ep_title = clean_display_name(ep.get("title") or f"Odcinek {ep_num}")
                            ext = ep.get("container_extension") or "mp4"
                            ep_id = ep.get("id") or ep.get("stream_id")
                            ep_plot = ep.get("info", {}).get("plot") if isinstance(ep.get("info"), dict) else ep.get("plot")
                            flat_eps.append({
                                "stream_id": ep_id,
                                "name": f"S{int(parse_int_safe(s_num, 1)):02d}E{int(parse_int_safe(ep_num, 1)):02d} - {ep_title}",
                                "container_extension": ext,
                                "direct_url": ep.get("direct_url"),
                                "plot": ep_plot,
                                "kind": "series",
                            })
                    if not flat_eps:
                        show_toast("Brak dostępnych odcinków dla tego serialu.", "#ca8a04")
                        return
                    state["active_series_id"] = sid
                    state["active_series_title"] = stitle
                    state["episodes"] = flat_eps
                    state["view_mode"] = "episodes"
                    state["zone"] = "right"
                    state["right_idx"] = 0
                    apply_filter_and_rebuild()
                except Exception as e:
                    show_toast(f"Błąd odcinków: {e}", "#dc2626")

            threading.Thread(target=_worker, daemon=True).start()

        # =====================================================================
        # LOGOWANIE DO SERWERA XTREAM / LISTY M3U
        # =====================================================================
        srv_input = ft.TextField(label="Adres serwera Xtream (http://...)", value=creds["server"], bgcolor="#18181b", border_color="#3f3f46")
        usr_input = ft.TextField(label="Użytkownik (Username)", value=creds["username"], bgcolor="#18181b", border_color="#3f3f46")
        pas_input = ft.TextField(label="Hasło (Password)", value=creds["password"], password=True, can_reveal_password=True, bgcolor="#18181b", border_color="#3f3f46")
        m3u_input = ft.TextField(label="LUB pełny link do listy M3U / M3U8", value=creds["m3u_url"], bgcolor="#18181b", border_color="#3f3f46")
        login_status_txt = ft.Text("", size=14, color="#eab308")

        def perform_login(srv="", usr="", pas="", m3u="", profile_name_hint=""):
            if state["is_logging_in"]:
                return
            srv_v = (srv or srv_input.value or "").strip()
            usr_v = (usr or usr_input.value or "").strip()
            pas_v = (pas or pas_input.value or "").strip()
            m3u_v = (m3u or m3u_input.value or "").strip()

            if m3u_v and (not usr_v or not pas_v):
                extracted = extract_xtream_from_m3u_url(m3u_v)
                if extracted:
                    srv_v, usr_v, pas_v = extracted
                    srv_input.value, usr_input.value, pas_input.value = srv_v, usr_v, pas_v

            state["is_logging_in"] = True
            login_status_txt.value = "Łączenie z serwerem IPTV..."
            safe_ui_update()

            def _login_worker():
                try:
                    if usr_v and pas_v and srv_v and srv_v not in ("http://", "https://"):
                        client = XtreamClient(srv_v, usr_v, pas_v)
                        uinfo = client.login()
                        exp_raw = uinfo.get("exp_date")
                        if exp_raw:
                            try:
                                state["exp_date_str"] = datetime.datetime.fromtimestamp(int(exp_raw)).strftime("%Y-%m-%d")
                            except Exception:
                                state["exp_date_str"] = "Aktywne"
                        else:
                            state["exp_date_str"] = "Bez limitu"
                    elif m3u_v:
                        client = M3UClient(m3u_v)
                        client.login()
                        state["exp_date_str"] = "Playlista M3U"
                    else:
                        raise ValueError("Podaj dane Xtream Codes lub link M3U.")

                    creds["server"], creds["username"], creds["password"], creds["m3u_url"] = srv_v, usr_v, pas_v, m3u_v
                    cache_set("server", srv_v)
                    cache_set("username", usr_v)
                    cache_set("password", pas_v)
                    cache_set("m3u_url", m3u_v)

                    p_name = register_or_update_profile(profile_name_hint, srv_v, usr_v, pas_v, m3u_v)
                    state["active_profile_name"] = p_name
                    state["client"] = client
                    state["categories_cache"] = {"live": None, "vod": None, "series": None}
                    state["streams_cache"].clear()
                    state["in_dashboard"] = True
                    state["zone"] = "dashboard"
                    state["is_logging_in"] = False
                    login_status_txt.value = ""
                    publish_status()
                    rebuild_UI()
                except Exception as e:
                    state["is_logging_in"] = False
                    login_status_txt.value = f"❌ Błąd logowania: {e}"
                    safe_ui_update()

            threading.Thread(target=_login_worker, daemon=True).start()

        # =====================================================================
        # BUDOWANIE WIDOKÓW INTERFEJSU (DASHBOARD, LISTY, PLAYER Z OSD)
        # =====================================================================
        root_container = ft.Container(expand=True, bgcolor="#101216")
        page.add(root_container)

        def build_login_view():
            return ft.Container(
                expand=True,
                alignment=ft.alignment.center,
                padding=30,
                content=ft.Container(
                    width=560,
                    padding=28,
                    bgcolor="#18181b",
                    border_radius=16,
                    border=ft.border.all(1.5, "#3f3f46"),
                    content=ft.Column(
                        [
                            ft.Row([make_iptv_bp_badge(48), ft.Text("Logowanie IPTV BP", size=24, weight=ft.FontWeight.BOLD)], spacing=14),
                            ft.Divider(color="#27272a"),
                            srv_input,
                            ft.Row([ft.Container(usr_input, expand=True), ft.Container(pas_input, expand=True)], spacing=10),
                            m3u_input,
                            login_status_txt,
                            ft.Row(
                                [
                                    ft.ElevatedButton(
                                        "ZALOGUJ I WCZYTAJ LISTĘ",
                                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                                        bgcolor="#2563eb",
                                        color="#ffffff",
                                        expand=True,
                                        height=46,
                                        on_click=lambda _: perform_login(),
                                    ),
                                    ft.OutlinedButton(
                                        "WYJDŹ",
                                        icon=ft.Icons.POWER_SETTINGS_NEW,
                                        height=46,
                                        on_click=force_exit_app,
                                    ),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=14,
                        tight=True,
                    ),
                ),
            )

        def build_dashboard_view():
            cards_spec = [
                ("📺 TELEWIZJA NA ŻYWO", "Kanały TV + Szybkie przełączanie OSD", "#2563eb", lambda _: open_kind_section("live")),
                ("🎬 FILMY VOD", "Katalog filmów + Tłumacz opisów PL", "#7c3aed", lambda _: open_kind_section("vod")),
                ("🍿 SERIALE", "Sezony i odcinki + Tłumacz opisów PL", "#059669", lambda _: open_kind_section("series")),
                (f"⭐ ULUBIONE ({len(favorites_items)})", "Twoje zapisane kanały i VOD", "#ca8a04", lambda _: open_favorites_view()),
                ("🔄 ZMIEŃ PROFIL / LISTĘ", f"Aktywny: {state['active_profile_name']}", "#4b5563", lambda _: logout_to_login()),
                ("🚪 ZAMKNIJ APLIKACJĘ", "Bezpieczne wyjście do Android TV", "#dc2626", force_exit_app),
            ]

            grid_rows = []
            for idx, (title, sub, col, cb) in enumerate(cards_spec):
                is_focused = (state["zone"] == "dashboard" and state["dash_idx"] == idx)
                card = ft.Container(
                    width=340,
                    height=115,
                    padding=18,
                    bgcolor="#27272a" if is_focused else "#18181b",
                    border_radius=14,
                    border=ft.border.all(3 if is_focused else 1, "#60a5fa" if is_focused else col),
                    on_click=cb,
                    content=ft.Column(
                        [
                            ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color="#ffffff"),
                            ft.Text(sub, size=12, color="#a1a1aa"),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=6,
                    ),
                )
                grid_rows.append(card)

            return ft.Container(
                expand=True,
                padding=28,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                make_iptv_bp_badge(46),
                                ft.Column(
                                    [
                                        ft.Text(f"Profil: {state['active_profile_name']}", size=18, weight=ft.FontWeight.BOLD),
                                        ft.Text(f"Ważność konta: {state['exp_date_str']}  |  ID: {device_id}", size=12, color="#a1a1aa"),
                                    ],
                                    spacing=2,
                                ),
                                ft.Container(expand=True),
                                ft.Switch(
                                    label="Tylko Polskie (PL)",
                                    value=state["only_pl"],
                                    on_change=lambda e: toggle_only_pl(e.control.value),
                                ),
                                ft.Switch(
                                    label="Zawsze zewnętrzny VLC",
                                    value=state["external_vlc_only"],
                                    on_change=lambda e: toggle_external_vlc(e.control.value),
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color="#27272a"),
                        ft.Row(grid_rows[:3], spacing=18, alignment=ft.MainAxisAlignment.CENTER),
                        ft.Row(grid_rows[3:], spacing=18, alignment=ft.MainAxisAlignment.CENTER),
                        ft.Container(expand=True),
                        ft.Text(
                            f"▶ Ostatnio odtwarzane: {state['now_playing']}  |  {VLC_MODE_LABELS.get(state['vlc_mode'], '')}",
                            size=13,
                            color="#9ca3af",
                        ),
                    ],
                    spacing=20,
                ),
            )

        def open_favorites_view():
            state["in_dashboard"] = False
            state["in_fav"] = True
            state["osd_cat_id"] = "__FAV__"
            state["osd_cat_name"] = f"⭐ ULUBIONE ({len(favorites_items)})"
            state["view_mode"] = "streams"
            state["streams"] = list(favorites_items)
            state["zone"] = "right"
            state["right_idx"] = 0
            apply_filter_and_rebuild()

        def logout_to_login():
            stop_playback()
            state["client"] = None
            state["in_dashboard"] = False
            rebuild_UI()

        def toggle_only_pl(val: bool):
            state["only_pl"] = bool(val)
            cache_set("only_pl", state["only_pl"])
            apply_filter_and_rebuild()

        def toggle_external_vlc(val: bool):
            state["external_vlc_only"] = bool(val)
            cache_set("auto_vlc", state["external_vlc_only"])
            rebuild_UI()

        def build_browser_view():
            cats = state["categories"].get(state["kind"], [])
            cat_controls = []
            start_c = max(0, state["list_idx"] - VISIBLE_ROWS // 2)
            for idx in range(start_c, min(len(cats), start_c + VISIBLE_ROWS)):
                c = cats[idx]
                cid = c.get("category_id")
                cname = c.get("category_name") or "Kategoria"
                is_sel = (state["zone"] == "left" and state["list_idx"] == idx)
                is_act = (cid == state["active_cat"])
                cat_controls.append(
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=12, vertical=10),
                        bgcolor="#2563eb" if is_sel else ("#27272a" if is_act else "#18181b"),
                        border_radius=8,
                        on_click=lambda _, i=idx, cat_id=cid: (
                            state.update({"zone": "left", "list_idx": i}),
                            load_category_streams(cat_id),
                        ),
                        content=ft.Text(cname, size=14, weight=ft.FontWeight.BOLD if is_act else ft.FontWeight.NORMAL, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    )
                )

            items = state["filtered"]
            item_controls = []
            start_r = max(0, state["right_idx"] - VISIBLE_ROWS // 2)
            for idx in range(start_r, min(len(items), start_r + VISIBLE_ROWS)):
                it = items[idx]
                title = it.get("name") or it.get("title") or f"Pozycja {idx + 1}"
                is_sel = (state["zone"] == "right" and state["right_idx"] == idx)
                fav_mark = "⭐ " if is_favorite(it) else ""
                item_controls.append(
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=14, vertical=10),
                        bgcolor="#2563eb" if is_sel else "#18181b",
                        border_radius=8,
                        border=ft.border.all(1.5, "#93c5fd" if is_sel else "#27272a"),
                        on_click=lambda _, i=idx, item_obj=it: on_stream_row_click(i, item_obj),
                        content=ft.Row(
                            [
                                ft.Text(f"{idx + 1}.", size=13, color="#a1a1aa", width=42),
                                ft.Text(f"{fav_mark}{title}", size=15, weight=ft.FontWeight.W_600, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.IconButton(
                                    icon=ft.Icons.STAR if is_favorite(it) else ft.Icons.STAR_BORDER,
                                    icon_color="#eab308",
                                    icon_size=18,
                                    on_click=lambda _, item_obj=it: toggle_favorite(item_obj),
                                ),
                            ],
                            spacing=8,
                        ),
                    )
                )

            search_field = ft.TextField(
                hint_text="Szukaj tytułu lub kanału...",
                value=state["search_query"],
                width=260,
                height=38,
                content_padding=8,
                bgcolor="#18181b",
                on_submit=lambda e: (state.update({"search_query": e.control.value}), apply_filter_and_rebuild()),
            )

            return ft.Container(
                expand=True,
                padding=16,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.ElevatedButton("⬅ MENU", bgcolor="#27272a", color="#ffffff", on_click=lambda _: go_back_navigation()),
                                ft.Text(
                                    f"Odcinki: {state['active_series_title']}" if state["view_mode"] == "episodes" else f"Dział: {state['kind'].upper()} ({len(items)})",
                                    size=18,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Container(expand=True),
                                search_field,
                                ft.ElevatedButton(
                                    "VLC",
                                    icon=ft.Icons.OPEN_IN_NEW,
                                    bgcolor="#ea580c",
                                    color="#ffffff",
                                    on_click=lambda _: (
                                        play_stream_item(items[state["right_idx"]], force_external=True)
                                        if items
                                        else None
                                    ),
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Row(
                            [
                                ft.Container(
                                    width=270,
                                    visible=(not state["in_fav"] and state["view_mode"] != "episodes"),
                                    content=ft.Column(cat_controls, spacing=6),
                                ),
                                ft.Container(
                                    expand=2,
                                    content=ft.Column(item_controls or [ft.Text("Brak pozycji spełniających kryteria.", color="#a1a1aa")], spacing=6),
                                ),
                                ft.Container(
                                    width=370,
                                    padding=14,
                                    bgcolor="#18181b",
                                    border_radius=12,
                                    border=ft.border.all(1, "#27272a"),
                                    content=ft.Column(
                                        [
                                            preview_poster_img,
                                            preview_title_txt,
                                            preview_meta_txt,
                                            ft.Divider(color="#27272a"),
                                            preview_plot_txt,
                                        ],
                                        spacing=8,
                                    ),
                                ),
                            ],
                            expand=True,
                            spacing=14,
                        ),
                    ],
                    expand=True,
                    spacing=12,
                ),
            )

        def on_stream_row_click(idx: int, item_obj: dict):
            state["zone"] = "right"
            state["right_idx"] = idx
            update_preview_async(item_obj)
            if state["kind"] == "series" and state["view_mode"] != "episodes" and not state["in_fav"]:
                open_series_episodes(item_obj)
            else:
                play_stream_item(item_obj)

        def build_fullscreen_player_view():
            """
            Widok pełnoekranowego odtwarzacza z:
            1) Boczną listą kanałów OSD + przełącznikiem kategorii (Lewo/Prawo) bez przerywania odtwarzania
            2) Dolnym paskiem Zappingu (Góra / Dół) z EPG
            3) Tarczą numeryczną (0-9) do szybkiego wybierania numeru kanału
            """
            ch_list = get_active_channel_list()
            ch_idx, ch_num, ch_total = find_channel_index_and_number(state.get("current_item"))

            osd_cats = get_osd_categories_list()
            cur_cat_id = state.get("osd_cat_id") or "__ALL__"
            cat_pos_idx = 0
            for idx_c, c_it in enumerate(osd_cats):
                if c_it.get("category_id") == cur_cat_id:
                    cat_pos_idx = idx_c
                    break
            cat_title_str = state.get("osd_cat_name") or (osd_cats[cat_pos_idx].get("category_name") if osd_cats else "Kanały TV")

            # 1. BOCZNA LISTA KANAŁÓW OSD (po lewej stronie na wideo)
            osd_rows = []
            if app_state["osd_list_open"]:
                if state.get("osd_cat_loading"):
                    osd_rows.append(
                        ft.Container(
                            padding=16,
                            content=ft.Text("⏳ Pobieranie kanałów z wybranej kategorii...", size=14, color="#eab308"),
                        )
                    )
                elif ch_list:
                    osd_sel = max(0, min(len(ch_list) - 1, state["osd_idx"]))
                    start_o = max(0, osd_sel - OSD_VISIBLE_ROWS // 2)
                    for idx in range(start_o, min(len(ch_list), start_o + OSD_VISIBLE_ROWS)):
                        it = ch_list[idx]
                        cname = it.get("name") or it.get("title") or f"Kanał {idx + 1}"
                        is_cursor = (idx == osd_sel)
                        is_playing_now = (get_item_unique_id(it) == get_item_unique_id(state.get("current_item")))
                        osd_rows.append(
                            ft.Container(
                                padding=ft.padding.symmetric(horizontal=12, vertical=9),
                                bgcolor="#2563eb" if is_cursor else ("#27272a" if is_playing_now else "#cc18181b"),
                                border_radius=8,
                                border=ft.border.all(2 if is_cursor else 1, "#93c5fd" if is_cursor else ("#eab308" if is_playing_now else "#3f3f46")),
                                on_click=lambda _, i=idx, ch_obj=it: (
                                    state.update({"osd_idx": i}),
                                    play_stream_item(ch_obj, keep_osd_open=False),
                                ),
                                content=ft.Row(
                                    [
                                        ft.Text(f"{idx + 1}.", size=13, color="#eab308" if is_playing_now else "#a1a1aa", width=38, weight=ft.FontWeight.BOLD),
                                        ft.Text(
                                            f"{'▶ ' if is_playing_now else ''}{cname}",
                                            size=14,
                                            weight=ft.FontWeight.BOLD if (is_cursor or is_playing_now) else ft.FontWeight.NORMAL,
                                            color="#ffffff",
                                            expand=True,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                    spacing=6,
                                ),
                            )
                        )
                else:
                    osd_rows.append(
                        ft.Container(
                            padding=16,
                            content=ft.Text("Brak kanałów w tej kategorii.", size=14, color="#a1a1aa"),
                        )
                    )

            # Pasek wyboru kategorii wewnątrz OSD (obsługa Lewo / Prawo na pilocie lub kliknięciem)
            osd_category_selector = ft.Container(
                padding=ft.padding.symmetric(horizontal=8, vertical=6),
                bgcolor="#18181b",
                border_radius=10,
                border=ft.border.all(1.5, "#3b82f6"),
                content=ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.CHEVRON_LEFT,
                            icon_color="#60a5fa",
                            icon_size=22,
                            tooltip="Poprzednia kategoria (Strzałka w lewo)",
                            on_click=lambda _: switch_osd_category(-1),
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    cat_title_str,
                                    size=13,
                                    weight=ft.FontWeight.BOLD,
                                    color="#f4f4f5",
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                ft.Text(
                                    f"Kategoria {cat_pos_idx + 1}/{max(1, len(osd_cats))}  •  ({len(ch_list)} kan.)",
                                    size=11,
                                    color="#eab308",
                                    text_align=ft.TextAlign.CENTER,
                                ),
                            ],
                            spacing=1,
                            expand=True,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CHEVRON_RIGHT,
                            icon_color="#60a5fa",
                            icon_size=22,
                            tooltip="Następna kategoria (Strzałka w prawo)",
                            on_click=lambda _: switch_osd_category(1),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

            osd_drawer = ft.Container(
                top=16,
                bottom=16,
                left=16,
                width=400,
                padding=14,
                bgcolor="#e6101216",
                border_radius=14,
                border=ft.border.all(1.5, "#3b82f6"),
                visible=bool(app_state["osd_list_open"]),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text("📺 LISTA KANAŁÓW (OSD)", size=15, weight=ft.FontWeight.BOLD, color="#60a5fa", expand=True),
                                ft.Text(f"Poz. {min(len(ch_list), state['osd_idx'] + 1)}/{max(1, len(ch_list))}", size=12, color="#a1a1aa"),
                            ]
                        ),
                        osd_category_selector,
                        ft.Text("◄/►: Zmień kategorię  •  ▲/▼: Kanał  •  OK: Włącz  •  Wstecz: Ukryj", size=11, color="#9ca3af"),
                        ft.Divider(color="#27272a", height=6),
                        ft.Column(osd_rows, spacing=5, expand=True),
                    ],
                    spacing=6,
                ),
            )

            # 2. DOLNY PASEK INFORMACYJNY (ZAP BANNER) + SZYBKIE PRZYCISKI
            bottom_zap_bar = ft.Container(
                left=430 if app_state["osd_list_open"] else 20,
                right=20,
                bottom=20,
                padding=ft.padding.symmetric(horizontal=20, vertical=14),
                bgcolor="#d9101216",
                border_radius=14,
                border=ft.border.all(1, "#3f3f46"),
                visible=bool(app_state["zap_banner_visible"] or app_state["osd_list_open"]),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    padding=ft.padding.symmetric(horizontal=10, vertical=4),
                                    bgcolor="#2563eb",
                                    border_radius=6,
                                    content=ft.Text(f"CH {ch_num}/{max(1, ch_total)}", size=15, weight=ft.FontWeight.BOLD, color="#ffffff"),
                                ),
                                ft.Text(state["now_playing"], size=19, weight=ft.FontWeight.BOLD, color="#ffffff", expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.ElevatedButton("📋 Lista [OK]", bgcolor="#2563eb", color="#ffffff", on_click=lambda _: toggle_osd_channel_drawer()),
                                ft.ElevatedButton("⬆ CH+", bgcolor="#27272a", color="#ffffff", on_click=lambda _: zap_channel(1)),
                                ft.ElevatedButton("⬇ CH-", bgcolor="#27272a", color="#ffffff", on_click=lambda _: zap_channel(-1)),
                                ft.ElevatedButton("↩ Poprzedni [0]", bgcolor="#27272a", color="#ffffff", on_click=lambda _: recall_previous_channel()),
                                ft.ElevatedButton("VLC", bgcolor="#ea580c", color="#ffffff", on_click=lambda _: launch_external_vlc(state["playing_url"], state["now_playing"])),
                                ft.ElevatedButton("Wyjdź", bgcolor="#dc2626", color="#ffffff", on_click=lambda _: stop_playback()),
                            ],
                            spacing=8,
                        ),
                        ft.Text(state.get("osd_epg_text") or "", size=13, color="#eab308", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ],
                    spacing=6,
                    tight=True,
                ),
            )

            # 3. TARCZA NUMERYCZNA (po wpisaniu cyfr 0-9 na pilocie)
            num_overlay = ft.Container(
                top=24,
                right=24,
                padding=ft.padding.symmetric(horizontal=22, vertical=12),
                bgcolor="#e618181b",
                border_radius=12,
                border=ft.border.all(2, "#eab308"),
                visible=bool(app_state["num_buffer"]),
                content=ft.Text(f"🔢 CH {app_state['num_buffer']}", size=26, weight=ft.FontWeight.BOLD, color="#eab308"),
            )

            return ft.Stack(
                expand=True,
                controls=[
                    ft.Container(
                        expand=True,
                        bgcolor="#000000",
                        on_click=lambda _: toggle_osd_channel_drawer(),
                        content=app_state["video_ctrl"],
                    ),
                    osd_drawer,
                    bottom_zap_bar,
                    num_overlay,
                ],
            )

        def rebuild_UI():
            if stop_event.is_set():
                return
            if state["client"] is None:
                root_container.content = build_login_view()
            elif app_state["in_fullscreen_player"] and app_state["video_ctrl"] is not None:
                root_container.content = build_fullscreen_player_view()
            elif state["in_dashboard"]:
                root_container.content = build_dashboard_view()
            else:
                root_container.content = build_browser_view()
            safe_ui_update()

        # =====================================================================
        # NAWIGACJA PILOTEM ANDROID TV (STRZAŁKI, OK, BACK, NUMERY 0-9)
        # =====================================================================
        def go_back_navigation():
            if app_state["in_fullscreen_player"]:
                # Jeśli boczna lista kanałów OSD jest otwarta -> BACK zamyka tylko listę OSD, a kanał gra dalej!
                if app_state["osd_list_open"]:
                    app_state["osd_list_open"] = False
                    schedule_zap_banner_autohide(3.0)
                    rebuild_UI()
                    return
                stop_playback()
                return
            if state["view_mode"] == "episodes":
                state["view_mode"] = "streams"
                state["zone"] = "right"
                apply_filter_and_rebuild()
                return
            if not state["in_dashboard"]:
                state["in_dashboard"] = True
                state["in_fav"] = False
                state["zone"] = "dashboard"
                rebuild_UI()
                return
            now = time.time()
            if now - app_state["last_back_press_ts"] < 2.0:
                force_exit_app()
            else:
                app_state["last_back_press_ts"] = now
                show_toast("Naciśnij WSTECZ ponownie, aby zamknąć aplikację.", "#3f3f46")

        def on_key_event(e: ft.KeyboardEvent):
            key = (e.key or "").upper()

            # Obsługa klawiszy numerycznych 0-9 (szybkie wybieranie kanału)
            if key in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9") or (
                key.startswith("NUMPAD ") and key[-1].isdigit()
            ):
                digit = key[-1]
                handle_numeric_key_input(digit)
                return

            if key in ("ESCAPE", "BACKSPACE", "GO BACK", "BROWSER BACK"):
                go_back_navigation()
                return

            # Sterowanie wewnątrz pełnoekranowego odtwarzacza BEZ wychodzenia z kanału
            if app_state["in_fullscreen_player"]:
                ch_list = get_active_channel_list()
                if app_state["osd_list_open"]:
                    if key == "ARROW DOWN" and ch_list:
                        state["osd_idx"] = (state["osd_idx"] + 1) % len(ch_list)
                        refresh_osd_epg_for_item(ch_list[state["osd_idx"]])
                        rebuild_UI()
                    elif key == "ARROW UP" and ch_list:
                        state["osd_idx"] = (state["osd_idx"] - 1) % len(ch_list)
                        refresh_osd_epg_for_item(ch_list[state["osd_idx"]])
                        rebuild_UI()
                    elif key == "ARROW LEFT":
                        # Przełączenie na poprzednią kategorię w bocznej liście OSD
                        switch_osd_category(-1)
                    elif key == "ARROW RIGHT":
                        # Przełączenie na następną kategorię w bocznej liście OSD
                        switch_osd_category(1)
                    elif key in ("PAGE DOWN", "CHANNEL DOWN") and ch_list:
                        state["osd_idx"] = max(0, state["osd_idx"] - OSD_VISIBLE_ROWS)
                        rebuild_UI()
                    elif key in ("PAGE UP", "CHANNEL UP") and ch_list:
                        state["osd_idx"] = min(len(ch_list) - 1, state["osd_idx"] + OSD_VISIBLE_ROWS)
                        rebuild_UI()
                    elif key in ("ENTER", "SELECT"):
                        if ch_list:
                            chosen = ch_list[state["osd_idx"]]
                            play_stream_item(chosen, keep_osd_open=False)
                else:
                    # Gdy lista OSD jest ukryta: Góra/Dół natychmiast przełącza kanały, OK/Lewo otwiera listę OSD
                    if key in ("ARROW UP", "CHANNEL UP", "PAGE UP"):
                        zap_channel(1)
                    elif key in ("ARROW DOWN", "CHANNEL DOWN", "PAGE DOWN"):
                        zap_channel(-1)
                    elif key in ("ENTER", "SELECT", "ARROW LEFT", "CONTEXT MENU", "INFO"):
                        if app_state["num_buffer"]:
                            num_v = parse_int_safe(app_state["num_buffer"], 0)
                            app_state["num_buffer"] = ""
                            if num_v > 0:
                                zap_to_number(num_v)
                                return
                        toggle_osd_channel_drawer()
                    elif key == "ARROW RIGHT":
                        recall_previous_channel()
                return

            if state["client"] is None:
                return

            if state["in_dashboard"]:
                if key == "ARROW RIGHT":
                    state["dash_idx"] = (state["dash_idx"] + 1) % 6
                    rebuild_UI()
                elif key == "ARROW LEFT":
                    state["dash_idx"] = (state["dash_idx"] - 1) % 6
                    rebuild_UI()
                elif key == "ARROW DOWN":
                    state["dash_idx"] = (state["dash_idx"] + 3) % 6
                    rebuild_UI()
                elif key == "ARROW UP":
                    state["dash_idx"] = (state["dash_idx"] - 3) % 6
                    rebuild_UI()
                elif key in ("ENTER", "SELECT"):
                    idx = state["dash_idx"]
                    if idx == 0:
                        open_kind_section("live")
                    elif idx == 1:
                        open_kind_section("vod")
                    elif idx == 2:
                        open_kind_section("series")
                    elif idx == 3:
                        open_favorites_view()
                    elif idx == 4:
                        logout_to_login()
                    elif idx == 5:
                        force_exit_app()
                return

            cats = state["categories"].get(state["kind"], [])
            items = state["filtered"]

            if key == "ARROW DOWN":
                if state["zone"] == "left" and cats:
                    state["list_idx"] = min(len(cats) - 1, state["list_idx"] + 1)
                    rebuild_UI()
                elif state["zone"] == "right" and items:
                    state["right_idx"] = min(len(items) - 1, state["right_idx"] + 1)
                    update_preview_async(items[state["right_idx"]])
                    rebuild_UI()
            elif key == "ARROW UP":
                if state["zone"] == "left" and cats:
                    state["list_idx"] = max(0, state["list_idx"] - 1)
                    rebuild_UI()
                elif state["zone"] == "right" and items:
                    state["right_idx"] = max(0, state["right_idx"] - 1)
                    update_preview_async(items[state["right_idx"]])
                    rebuild_UI()
            elif key == "ARROW RIGHT":
                if state["zone"] == "left" and items:
                    state["zone"] = "right"
                    update_preview_async(items[state["right_idx"]])
                    rebuild_UI()
            elif key == "ARROW LEFT":
                if state["zone"] == "right" and not state["in_fav"] and state["view_mode"] != "episodes":
                    state["zone"] = "left"
                    rebuild_UI()
            elif key in ("ENTER", "SELECT"):
                if state["zone"] == "left" and cats:
                    cid = cats[state["list_idx"]].get("category_id")
                    load_category_streams(cid)
                elif state["zone"] == "right" and items:
                    on_stream_row_click(state["right_idx"], items[state["right_idx"]])

        page.on_keyboard_event = on_key_event

        rebuild_UI()
        if state["auto_login"] and has_saved_credentials():
            perform_login(
                creds["server"],
                creds["username"],
                creds["password"],
                creds["m3u_url"],
                state["active_profile_name"],
            )

    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    ft.app(target=main)
