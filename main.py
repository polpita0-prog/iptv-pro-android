# -*- coding: utf-8 -*-
import base64
import datetime
import html
import json
import os
import re
import tempfile
import threading
import time
import traceback
from urllib.parse import parse_qs, quote, urlparse
import uuid

import flet as ft
import requests

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

PL_REGEX = re.compile(
    r"(?:^|[\s\|\[\(\-_:•/])(PL|POL|POLAND|POLSKA|POLSKIE|POLISH|LEKTOR|DUBBING|NAPISY[\s_]*PL|PL[\s_]*SUB|VIP[\s_]*PL)(?:$|[\s\|\]\)\-_:•/])"
    r"|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"
    r"|\b(tvp|polsat|tvn|canal\+|c\+|ale\s*kino|kino\s*polska|planete\+|domo\+|kuchnia\+|teletoon\+|minimini\+|ttv|puls|fokus\s*tv|eska|polo\s*tv|trwam|republika|wpolsce|wydarzenia\s*24|stopklatka|zoom\s*tv|wp\s*tv|nowa\s*tv|metro\s*tv|eleven\s*sports|polsat\s*sport|motowizja|stars\s*tv|kino\s*tv|gametoon|active\s*family|romance\s*tv|13\s*ulica|sci\s*fi|paramount|comedy\s*central|national\s*geographic|discovery|animal\s*planet|tlc|id\s*investigation|hbo|cinemax|filmbox|axn|fox|epic\s*drama|bbc\s*brit|bbc\s*earth|bbc\s*first|bbc\s*lifestyle|viasat|history|crime\s*investigation|sundance|red\s*carpet|antena\s*hd|tv\s*okazje|super\s*polsat|tv4|tv6|polsat\s*news|tvn24|tvp\s*info|biznes24|news24|pogoda24|4fun|vox\s*music|disco\s*polo|power\s*tv|nuta\s*tv|mixtape|hip\s*hop\s*tv)\b",
    re.IGNORECASE,
)


def strip_unwanted_chars(text: str) -> str:
    """Usuwa niepotrzebne znaki sterujące, znaczniki HTML i dziwne symbole z tekstu."""
    if not text:
        return ""
    s = html.unescape(str(text))
    s = s.replace("\xa0", " ").replace("\u200b", "").replace("\r", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[★☆●•▪▫◆◇■□►▶◀◁⚡🔥]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def clean_display_name(name: str) -> str:
    """Czyści nazwę wyświetlaną na liście z niepotrzebnych prefiksów typu |PL|, [VIP] itp."""
    if not name:
        return ""
    s = strip_unwanted_chars(name)
    s = re.sub(r"^(\s*[\|\[\(\{]\s*[A-Za-z0-9\+\-\s_]{1,12}\s*[\|\]\)\}]\s*[:\-_\|]*\s*)+", "", s)
    s = re.sub(r"^(PL|POL|VIP|TV|VOD|SER|FILMY|SERIALE)\s*[:\|\-]+\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^[#\-_:\|\•\*\+]+\s*", "", s)
    s = re.sub(r"\s*[#\-_:\|]+$", "", s)
    return s.strip() or strip_unwanted_chars(name)


def clean_media_title(title: str) -> str:
    """Agresywne czyszczenie tytułu filmu/serialu pod kątem wyszukiwarek opisów (TMDB, Wiki, TVMaze)."""
    if not title:
        return ""
    clean = clean_display_name(title)
    clean = re.sub(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+", "", clean)
    clean = re.sub(r"\[.*?\]|\{.*?\}", " ", clean)
    clean = re.sub(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$", "", clean)
    clean = re.sub(r"\s*-\s*Sezon\s*\d+.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*\(\d{4}\).*$", "", clean)
    clean = re.sub(r"\s+\b(19\d{2}|20\d{2})\b.*$", "", clean)
    clean = re.sub(
        r"\b(FHD|UHD|4K|8K|HD|SD|1080p|720p|2160p|HEVC|H265|H264|x264|x265|WEB-DL|WEBRip|BluRay|BRRip|DVDRip|LEKTOR|DUBBING|NAPISY|PL|POL|EN|ENG|MULTI|RAW|VIP)\b.*$",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"[_•\|]+", " ", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean.strip(" -|_:/•.")


def extract_year_from_title(title: str) -> str:
    if not title:
        return ""
    m = re.search(r"(?:[\(\[\s])(19\d{2}|20\d{2})(?:[\)\]\s]|$)", str(title))
    return m.group(1) if m else ""


def is_polish_text(text: str) -> bool:
    if not text:
        return False
    return bool(PL_REGEX.search(str(text)))


def has_polish_readability(text: str) -> bool:
    """Sprawdza, czy opis jest faktycznie w języku polskim."""
    if not text or len(text.strip()) < 15:
        return False
    if re.search(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", text):
        return True
    pl_words = re.findall(
        r"\b(jest|oraz|który|która|które|jego|jej|przez|kiedy|gdy|życie|historia|bohater|świat|film|serial|opowiada|losy|młody|czasie|jednak|tylko|więcej|między)\b",
        text.lower(),
    )
    return len(pl_words) >= 2


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
        m = re.search(r"(\d+)$", str(val))
        return int(m.group(1)) if m else default


def decode_b64_safe(val: str) -> str:
    if not val:
        return ""
    try:
        raw = base64.b64decode(val).decode("utf-8", errors="ignore")
        return strip_unwanted_chars(raw)
    except Exception:
        return strip_unwanted_chars(str(val))


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


def make_session():
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


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
# WIELOŹRÓDŁOWY SILNIK OPISÓW PL (TMDB + WIKIPEDIA PL + TVMAZE + TRANSLATOR)
# =====================================================================
tmdb_memory_cache = {}
epg_memory_cache = {}
translate_memory_cache = {}


def translate_to_polish_free(text: str) -> str:
    """Automatycznie tłumaczy opis obcojęzyczny (np. z angielskiego) na język polski."""
    clean_t = strip_unwanted_chars(text)
    if not clean_t or len(clean_t) < 8:
        return ""
    if has_polish_readability(clean_t):
        return clean_t

    cache_k = clean_t[:120].lower()
    if cache_k in translate_memory_cache:
        return translate_memory_cache[cache_k]

    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=pl&dt=t&q={quote(clean_t[:1800])}"
        )
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=3.5)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(part[0] for part in data[0] if part and part[0])
                translated = strip_unwanted_chars(translated)
                if translated:
                    translate_memory_cache[cache_k] = translated
                    return translated
    except Exception:
        pass
    return clean_t


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
                r = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=3.0)
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
                    rs = requests.get(sum_url, headers=DEFAULT_HEADERS, timeout=3.0)
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
    """Alternatywne źródło metadanych z bazy TVMaze (szczególnie przydatne dla seriali i programów)."""
    for q in title_queries:
        q_clean = clean_media_title(q)
        if not q_clean:
            continue
        try:
            url = f"https://api.tvmaze.com/singlesearch/shows?q={quote(q_clean)}"
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=3.0)
            if r.status_code == 200:
                data = r.json() or {}
                summary_raw = strip_unwanted_chars(data.get("summary") or "")
                summary_pl = translate_to_polish_free(summary_raw) if summary_raw else ""
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
    Kompleksowe pobieranie polskich opisów z wielu źródeł:
    1) TMDB pl-PL (szukanie multi + movie/tv + dopasowanie roku)
    2) Serwer Xtream VOD/Series Info (z tłumaczeniem na PL, jeśli opis jest po angielsku)
    3) Polska Wikipedia API (pl.wikipedia.org)
    4) TVMaze API + Auto-tłumaczenie EN -> PL
    5) TMDB en-US + Auto-tłumaczenie EN -> PL
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
                r = requests.get(s_url, timeout=3.5)
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

    en_overview_fallback = ""

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
                rd = requests.get(det_url, timeout=3.5)
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

            if not overview_pl:
                try:
                    en_url = f"https://api.themoviedb.org/3/{m_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language=en-US"
                    ren = requests.get(en_url, timeout=3.0)
                    if ren.status_code == 200:
                        en_overview_fallback = strip_unwanted_chars(ren.json().get("overview") or "")
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
            en_overview_fallback = overview_pl

    if xtream_info and isinstance(xtream_info, dict):
        xt_plot = strip_unwanted_chars(
            xtream_info.get("plot")
            or xtream_info.get("description")
            or xtream_info.get("overview")
            or ""
        )
        if not res["plot"] and xt_plot:
            if has_polish_readability(xt_plot):
                res["plot"] = xt_plot
                res["plot_source"] = "Serwer VOD PL"
            elif not en_overview_fallback:
                en_overview_fallback = xt_plot

        if not res["poster"]:
            res["poster"] = (
                xtream_info.get("movie_image")
                or xtream_info.get("cover_big")
                or xtream_info.get("cover")
            )
        if not res["genres"] and xtream_info.get("genre"):
            res["genres"] = translate_to_polish_free(str(xtream_info.get("genre")))
        if not res["cast"] and (xtream_info.get("cast") or xtream_info.get("actors")):
            res["cast"] = strip_unwanted_chars(xtream_info.get("cast") or xtream_info.get("actors"))
        if not res["director"] and xtream_info.get("director"):
            res["director"] = strip_unwanted_chars(xtream_info.get("director"))
        if not res["year"]:
            res["year"] = str(xtream_info.get("releasedate") or xtream_info.get("releaseDate") or "")[:4]
        if not res["rating"] and xtream_info.get("rating"):
            res["rating"] = str(xtream_info.get("rating"))

    if not res["plot"]:
        wiki_plot = fetch_polish_wikipedia_plot(candidate_queries, media_hint=media_hint)
        if wiki_plot:
            res["plot"] = wiki_plot
            res["plot_source"] = "Wikipedia PL"

    if not res["plot"] and en_overview_fallback:
        translated_plot = translate_to_polish_free(en_overview_fallback)
        if translated_plot:
            res["plot"] = translated_plot
            res["plot_source"] = "TMDB / Tłumaczenie PL"

    if not res["plot"] or not res["poster"]:
        tvm = fetch_tvmaze_details(candidate_queries)
        if tvm:
            if not res["plot"] and tvm.get("plot"):
                res["plot"] = tvm["plot"]
                res["plot_source"] = "TVMaze PL"
            if not res["poster"] and tvm.get("poster"):
                res["poster"] = tvm["poster"]
            if not res["rating"] and tvm.get("rating"):
                res["rating"] = tvm["rating"]
            if not res["year"] and tvm.get("year"):
                res["year"] = tvm["year"]
            if not res["genres"] and tvm.get("genres"):
                res["genres"] = tvm["genres"]

    if res["plot"]:
        tmdb_memory_cache[cache_key] = res
    return res


class XtreamClient:

    def __init__(self, server, username, password):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.session = make_session()
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
                start_t = (el.get("start") or "")[11:16]
                end_t = (el.get("end") or "")[11:16]
                if title:
                    line_str = f"🕒 {start_t} - {end_t} | {title}"
                    if desc:
                        line_str += f"\n{desc}"
                    parsed.append(line_str.strip())
            epg_memory_cache[str(stream_id)] = (now_ts, parsed)
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
        self.session = make_session()
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
        r = self.session.get(self.source, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        self._parse_m3u(r.text)
        return self.user_info

    def _classify_entry(self, url, group, name):
        lower_url = url.lower()
        lower_group = group.lower()
        ep_match = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", name)
        if (
            "/series/" in lower_url
            or ep_match
            or any(k in lower_group for k in ["seriale", "series", "serial"])
        ):
            return "series", ep_match
        if (
            "/movie/" in lower_url
            or lower_url.endswith((".mp4", ".mkv", ".avi"))
            or any(
                k in lower_group
                for k in ["vod", "filmy", "movies", "film", "kino"]
            )
        ):
            return "vod", None
        return "live", None

    def _parse_m3u(self, text):
        cat_sets = {"live": {}, "vod": {}, "series": {}}
        series_groups = {}
        attr_re = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
        extinf_line = None
        seq_id = 1

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                extinf_line = line
                continue
            if line.startswith("#"):
                continue

            url = line
            attrs = dict(attr_re.findall(extinf_line)) if extinf_line else {}
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
            "paused_in_bg": False,
            "preview_seq": 0,
            "detail_seq": 0,
            "osd_hide_seq": 0,
            "zap_banner_seq": 0,
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
                with storage_lock:
                    items_to_save = list(pending_writes.items())
                    pending_writes.clear()
                    write_timer_active["running"] = False

                for k_save, v_save in items_to_save:
                    if stop_event.is_set():
                        break
                    try:
                        page.client_storage.set(k_save, v_save)
                    except Exception:
                        pass

            threading.Thread(target=_flush_writer, daemon=True).start()

        def safe_ui_update():
            if stop_event.is_set():
                return
            try:
                page.update()
            except Exception:
                pass

        def show_modal_dialog(dlg: ft.AlertDialog):
            """Bezpiecznie wyświetla okno dialogowe bez wycieków pamięci w page.overlay."""
            app_state["dialog_open"] = True
            page.overlay.clear()
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def force_exit_app(_e=None):
            stop_event.set()
            try:
                page.window.prevent_close = False
            except Exception:
                pass
            try:
                page.window_prevent_close = False
            except Exception:
                pass
            try:
                page.window.close()
            except Exception:
                pass
            try:
                page.window_close()
            except Exception:
                pass
            try:
                page.window_destroy()
            except Exception:
                pass

            def _kill():
                time.sleep(0.15)
                os._exit(0)

            threading.Thread(target=_kill, daemon=True).start()

        def on_lifecycle_change(e: ft.AppLifecycleStateChangeEvent):
            st_str = str(e.data).lower()
            if "detach" in st_str:
                stop_event.set()
            elif "pause" in st_str or "inactive" in st_str or "hidden" in st_str:
                app_state["paused_in_bg"] = True
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
        processed_ids = set(cache_get("processed_ids", []))
        auto_vlc_saved = cache_get("auto_vlc", False)
        use_native_vid_saved = cache_get("use_native_video_ctrl", bool(HAS_FLET_VIDEO_PKG))
        auto_vlc_zap_saved = cache_get("auto_launch_vlc_on_zap", False)
        auto_login_saved = cache_get("auto_login", True)
        only_pl_saved = cache_get("only_pl", False)
        saved_vlc_mode = cache_get("vlc_mode", "vlc_clean")
        favorites_items = list(cache_get("favorites_v2", []))

        VLC_MODE_ORDER = ["vlc_clean", "intent", "vlc_full", "http"]
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
            "all_built_items": [],
            "osd_channels": [],
            "osd_idx": 0,
            "osd_cat_idx": 0,
            "detail_item": None,
            "detail_btn_idx": 0,
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
            "dash_top_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "settings_idx": 0,
            "search_idx": 0,
            "login_idx": 5 if has_saved_credentials() else 1,
        }

        VISIBLE_ROWS = 8
        OSD_VISIBLE_ROWS = 9

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

        def publish_status():
            if stop_event.is_set() or app_state["paused_in_bg"]:
                return

            def _worker():
                try:
                    local_session = make_session()
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
                    local_session.post(
                        f"https://ntfy.sh/{status_topic}",
                        data=json.
