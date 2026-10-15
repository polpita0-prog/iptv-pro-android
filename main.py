# -*- coding: utf-8 -*-
"""
IPTV Client V4.4 PRO - ANDROID EDITION (Flet + Native MediaKit Video + Cloud Remote)
---------------------------------------------------------------------------------------
- Natywny interfejs Android / Android TV (2-kolumnowy Dark Navy Dashboard).
- Wbudowany odtwarzacz wideo Flet Video (MediaKit / libmpv) + uruchamianie VLC na Androidzie.
- Pełna kompatybilność z dwukierunkowym panelem WWW (ntfy.sh Cloud Sync).
- Obsługa Xtream Codes API, M3U/M3U8, TMDB PL oraz EPG.
"""

import os
import re
import json
import time
import uuid
import base64
import datetime
import threading
from urllib.parse import urlparse, parse_qs

import requests
import flet as ft

TMDB_API_KEY = "6e2d327ee657c8671b3eda952cd260a0"

DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}


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


def clean_media_title(title):
    if not title:
        return ""
    clean = re.sub(r'^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+', '', title)
    clean = re.sub(r'\[.*?\]', '', clean)
    clean = re.sub(r'\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$', '', clean)
    clean = re.sub(r'\s*\(\d{4}\).*$', '', clean)
    clean = re.sub(r'\b(FHD|UHD|4K|HD|1080p|720p|HEVC|H265|LEKTOR|DUBBING|NAPISY|PL|EN|FR)\b.*$',
                   '', clean, flags=re.IGNORECASE)
    return clean.strip(" -|_:")


def fetch_tmdb_details(title):
    clean_title = clean_media_title(title)
    if not clean_title:
        return None
    try:
        url = (f"https://api.themoviedb.org/3/search/multi"
               f"?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_title)}&language=pl-PL")
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                best = results[0]
                poster_path = best.get("poster_path")
                release = best.get("release_date") or best.get("first_air_date") or ""
                return {
                    "poster": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
                    "plot": best.get("overview") or "",
                    "rating": str(round(best.get("vote_average", 0), 1)) if best.get("vote_average") else "",
                    "year": release[:4] if release else "",
                }
    except Exception:
        pass
    return None


def decode_epg_text(text):
    if not text:
        return ""
    try:
        decoded = base64.b64decode(text).decode("utf-8")
        if all(ord(c) >= 32 or c in "\n\r\t" for c in decoded):
            return decoded
    except Exception:
        pass
    return text


# ==========================================
# KLIENT XTREAM CODES API
# ==========================================
class XtreamClient:
    def __init__(self, server, username, password):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.session = make_session()
        self.user_info = {}

    def _api(self, params, timeout=18):
        url = f"{self.server}/player_api.php"
        full_params = {"username": self.username, "password": self.password}
        full_params.update(params)
        r = self.session.get(url, params=full_params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def login(self):
        data = self._api({})
        self.user_info = data.get("user_info", {})
        if (self.user_info or {}).get("auth") != 1:
            raise ValueError("Nieprawidłowy login lub hasło.")
        return self.user_info

    def get_categories(self, kind):
        action = {
            "live": "get_live_categories",
            "vod": "get_vod_categories",
            "series": "get_series_categories",
        }[kind]
        return self._api({"action": action}) or []

    def get_streams(self, kind, category_id=None):
        action = {
            "live": "get_live_streams",
            "vod": "get_vod_streams",
            "series": "get_series",
        }[kind]
        params = {"action": action}
        if category_id and category_id != "__ALL__":
            params["category_id"] = category_id
        return self._api(params, timeout=25) or []

    def get_series_info(self, series_id):
        return self._api({"action": "get_series_info", "series_id": series_id})

    def get_short_epg(self, stream_id, limit=5):
        return self._api({"action": "get_short_epg", "stream_id": stream_id, "limit": limit}, timeout=5)

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


# ==========================================
# KLIENT LIST M3U / M3U8
# ==========================================
class M3UClient:
    def __init__(self, source):
        self.source = source.strip()
        self.server = "Lista M3U"
        self.username = "M3U Playlist"
        self.session = make_session()
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.series_map = {}
        self.direct_urls = {}

    def login(self):
        r = self.session.get(self.source, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        self._parse_m3u(r.text)
        return {"auth": 1, "exp_date": "0"}

    def _classify_entry(self, url, group, name):
        lower_url = url.lower()
        lower_group = group.lower()
        ep_match = re.search(r'[Ss](\d{1,2})\s*[Ee](\d{1,3})', name)
        if "/series/" in lower_url or ep_match or any(k in lower_group for k in ["seriale", "series", "serial"]):
            return "series", ep_match
        if "/movie/" in lower_url or lower_url.endswith((".mp4", ".mkv", ".avi")) or any(
            k in lower_group for k in ["vod", "filmy", "movies", "film", "kino"]
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
            name = extinf_line.split(",", 1)[1].strip() if (extinf_line and "," in extinf_line) else f"Kanał {seq_id}"
            group = (attrs.get("group-title") or "Bez kategorii").strip()
            logo = attrs.get("tvg-logo") or ""
            kind, ep_match = self._classify_entry(url, group, name)

            if group not in cat_sets[kind]:
                cat_id = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cat_id
                self.categories[kind].append({"category_id": cat_id, "category_name": group})
                self.streams_by_cat[kind][cat_id] = []
            else:
                cat_id = cat_sets[kind][group]

            item_id = f"m3u_item_{seq_id}"
            seq_id += 1
            self.direct_urls[item_id] = url

            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cat_id].append({
                    "stream_id": item_id, "name": name, "stream_icon": logo,
                    "direct_url": url, "kind": kind
                })
            else:
                season_num = str(int(ep_match.group(1))) if ep_match else "1"
                ep_num = int(ep_match.group(2)) if ep_match else 1
                base_title = name[:ep_match.start()].strip(" -|:") if ep_match else name
                series_key = (cat_id, base_title.lower())
                if series_key not in series_groups:
                    series_id = f"m3u_ser_{seq_id}"
                    series_groups[series_key] = series_id
                    self.streams_by_cat["series"][cat_id].append({
                        "series_id": series_id, "name": base_title, "cover": logo, "kind": "series"
                    })
                    self.series_map[series_id] = {"episodes": {}}
                else:
                    series_id = series_groups[series_key]
                eps = self.series_map[series_id]["episodes"].setdefault(season_num, [])
                eps.append({"id": item_id, "episode_num": ep_num, "title": name, "direct_url": url})
            extinf_line = None

    def get_categories(self, kind):
        return self.categories.get(kind, [])

    def get_streams(self, kind, category_id=None):
        cat_map = self.streams_by_cat.get(kind, {})
        if not category_id or category_id == "__ALL__":
            res = []
            for v in cat_map.values():
                res.extend(v)
            return res
        return cat_map.get(category_id, [])

    def get_series_info(self, series_id):
        return self.series_map.get(series_id, {"episodes": {}})

    def get_short_epg(self, stream_id, limit=5):
        return {"epg_listings": []}

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        return self.direct_urls.get(stream_id, "")


# ==========================================
# GŁÓWNA APLIKACJA ANDROID / FLET
# ==========================================
def main(page: ft.Page):
    page.title = "IPTV V4.4 PRO Android"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0b0e17"
    page.padding = 8

    # Odczyt danych z pamięci urządzenia Android (client_storage)
    device_id = page.client_storage.get("device_id")
    if not device_id:
        device_id = f"android-{uuid.uuid4().hex[:6]}"
        page.client_storage.set("device_id", device_id)

    device_name = page.client_storage.get("device_name") or "Android TV Box"
    profiles = page.client_storage.get("profiles") or []
    favorites = page.client_storage.get("favorites") or {"live": [], "vod": [], "series": []}
    processed_ids = set(page.client_storage.get("processed_ids") or [])

    state = {
        "client": None,
        "kind": "live",
        "in_fav": False,
        "view_mode": "categories",
        "active_cat": None,
        "active_profile_name": "",
        "categories": {"live": [], "vod": [], "series": []},
        "streams": [],
        "filtered": [],
        "now_playing": "Brak aktywnego strumienia",
        "playing_url": "",
        "is_playing": False,
        "paused": False,
        "volume": 100,
        "muted": False,
    }

    # --- USŁUGA CHMUROWA NTFY.SH (KOMPATYBILNA Z PANEL_ZDALNY.HTML) ---
    session = make_session()
    start_ts = int(time.time()) - 180

    def publish_status():
        def _worker():
            try:
                clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', page.client_storage.get("device_id") or device_id)
                status_topic = f"iptv_v4_pro_{clean_id}_status"
                if state["is_playing"]:
                    st_lbl = "⏸ PAUZA" if state["paused"] else "▶ ODTWARZANIE"
                else:
                    st_lbl = "⏹ ZATRZYMANO"

                srv_txt = state["active_profile_name"] if state["client"] else "Ekran logowania"
                prof_summary = []
                for p in profiles:
                    prof_summary.append({
                        "name": p.get("name", "Lista"),
                        "mode": p.get("mode", "xtream"),
                        "host": (p.get("server") or p.get("m3u_url") or "")[:30],
                        "added_at": p.get("added_at", ""),
                        "active": (state["client"] is not None and p.get("name") == state["active_profile_name"])
                    })

                snapshot = {
                    "type": "player_status",
                    "device_name": page.client_storage.get("device_name") or device_name,
                    "device_id": clean_id,
                    "ts": int(time.time() * 1000),
                    "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
                    "state_label": st_lbl,
                    "now_playing": state["now_playing"],
                    "active_server": srv_txt,
                    "current_tab": "ulubione" if state["in_fav"] else state["kind"],
                    "volume": state["volume"],
                    "muted": state["muted"],
                    "profiles": prof_summary,
                }
                session.post(
                    f"https://ntfy.sh/{status_topic}",
                    data=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
                    timeout=6
                )
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    # --- KOMPONENT WIDEO (NATYWNY ANDROID MEDIAKIT) ---
    video_player = ft.Video(
        expand=True,
        playlist=[],
        autoplay=True,
        volume=100,
        aspect_ratio=16 / 9,
        show_controls=True,
    )

    now_playing_txt = ft.Text("▶ Wybierz kanał lub film z listy", weight=ft.FontWeight.BOLD, size=14, color="#60a5fa")
    header_dev_lbl = ft.Text(f"📺 {device_name} [{device_id}]", size=12, color="#10b981", weight=ft.FontWeight.BOLD)
    list_view = ft.ListView(expand=True, spacing=6, padding=4)
    search_tf = ft.TextField(hint_text="Szukaj...", height=40, text_size=13, content_padding=8, expand=True)
    status_banner = ft.Text(f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}", color="#10b981", size=12)

    # Pola logowania
    srv_tf = ft.TextField(label="Adres serwera (http://serwer:8080)", value=page.client_storage.get("server") or "http://")
    usr_tf = ft.TextField(label="Użytkownik", value=page.client_storage.get("username") or "")
    pas_tf = ft.TextField(label="Hasło", password=True, can_reveal_password=True, value=page.client_storage.get("password") or "")
    m3u_tf = ft.TextField(label="Lub link do listy M3U / M3U8", value=page.client_storage.get("m3u_url") or "")

    def play_stream_url(url, title):
        state["playing_url"] = url
        state["now_playing"] = title
        state["is_playing"] = True
        state["paused"] = False
        now_playing_txt.value = f"▶ {title}"
        try:
            video_player.playlist_clear()
            video_player.playlist_add(ft.VideoMedia(url))
            video_player.play()
        except Exception:
            pass
        page.update()
        publish_status()

    def stop_playback(_e=None):
        state["is_playing"] = False
        state["paused"] = False
        state["now_playing"] = "Brak aktywnego strumienia"
        now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
        try:
            video_player.stop()
        except Exception:
            pass
        page.update()
        publish_status()

    def toggle_play_pause(_e=None):
        try:
            video_player.play_or_pause()
            state["paused"] = not state["paused"]
        except Exception:
            pass
        page.update()
        publish_status()

    def open_in_android_vlc(_e=None):
        if state["playing_url"]:
            # Na Androidzie otwiera strumień w zewnętrznym VLC lub domyślnym odtwarzaczu wideo
            page.launch_url(state["playing_url"])

    def show_info_dialog(_e=None):
        title = state["now_playing"]
        dlg = ft.AlertDialog(
            title=ft.Text(f"Informacje TMDB: {title}"),
            content=ft.Text("Pobieranie polskiego opisu z TMDB..."),
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

        def _w():
            tmdb = fetch_tmdb_details(title)
            if tmdb:
                txt = f"Rok: {tmdb.get('year')}\nOcena: ⭐ {tmdb.get('rating')}/10\n\n{tmdb.get('plot') or 'Brak opisu.'}"
            else:
                txt = "Brak danych w bazie TMDB dla tej pozycji."
            dlg.content = ft.Text(txt)
            page.update()

        threading.Thread(target=_w, daemon=True).start()

    # --- RENDEROWANIE LISTY KANAŁÓW / KATEGORII ---
    def render_list():
        list_view.controls.clear()
        q = (search_tf.value or "").strip().lower()
        is_cat = (state["view_mode"] == "categories" and not state["in_fav"])

        raw = state["categories"].get(state["kind"], []) if is_cat else state["streams"]
        filtered = []
        for it in raw:
            nm = it.get("category_name") if is_cat else (it.get("name") or it.get("title") or "")
            if not q or q in (nm or "").lower():
                filtered.append(it)
        state["filtered"] = filtered

        if not is_cat and not state["in_fav"]:
            list_view.controls.append(
                ft.ElevatedButton("↩ Wróć do kategorii", on_click=lambda _: back_to_cats(), bgcolor="#1e293b", color="#60a5fa")
            )

        for idx, item in enumerate(filtered[:250]):  # Limit 250 dla płynności na TV Boxach
            if is_cat:
                cat_name = item.get("category_name", "Kategoria")
                list_view.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Text(f"{idx+1}. 📁 {cat_name}", weight=ft.FontWeight.BOLD, size=13, expand=True),
                            ft.Text("❯", color="#64748b")
                        ]),
                        bgcolor="#161d30",
                        padding=10,
                        border_radius=6,
                        on_click=lambda _e, c=item: open_category(c)
                    )
                )
            else:
                k = item.get("kind") or state["kind"]
                icon = "📺" if k == "live" else ("🎬" if k == "vod" else "🎞")
                nm = item.get("name") or item.get("title") or "Pozycja"
                list_view.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Text(f"{idx+1}. {icon} {nm}", weight=ft.FontWeight.BOLD, size=13, expand=True),
                        ]),
                        bgcolor="#161d30",
                        padding=10,
                        border_radius=6,
                        on_click=lambda _e, it=item: on_stream_click(it)
                    )
                )
        page.update()

    search_tf.on_change = lambda _: render_list()

    def back_to_cats():
        state["view_mode"] = "categories"
        state["active_cat"] = None
        if not state["categories"].get(state["kind"]):
            def _w():
                try:
                    cats = state["client"].get_categories(state["kind"]) or []
                    state["categories"][state["kind"]] = [{"category_id": "__ALL__", "category_name": "🔥 WSZYSTKIE"}] + cats
                    render_list()
                except Exception:
                    pass
            threading.Thread(target=_w, daemon=True).start()
        else:
            render_list()

    def open_category(cat_obj):
        state["view_mode"] = "streams"
        state["active_cat"] = cat_obj
        list_view.controls.clear()
        list_view.controls.append(ft.Text("⏳ Pobieranie listy z serwera...", color="#60a5fa"))
        page.update()

        def _w():
            try:
                items = state["client"].get_streams(state["kind"], cat_obj.get("category_id")) or []
                for it in items:
                    it["kind"] = state["kind"]
                state["streams"] = items
                render_list()
            except Exception:
                pass

        threading.Thread(target=_w, daemon=True).start()

    def on_stream_click(item):
        k = item.get("kind") or state["kind"]
        title = item.get("name") or item.get("title") or "Strumień"
        if k == "series":
            def _w():
                try:
                    info = state["client"].get_series_info(item.get("series_id"))
                    eps_map = info.get("episodes") or {}
                    if eps_map:
                        first_s = sorted(eps_map.keys())[0]
                        first_ep = eps_map[first_s][0]
                        ext = first_ep.get("container_extension", "mp4") or "mp4"
                        url = state["client"].build_stream_url("series", first_ep.get("id"), ext, item=first_ep)
                        play_stream_url(url, f"{title} - {first_ep.get('title', 'Odcinek 1')}")
                except Exception:
                    pass
            threading.Thread(target=_w, daemon=True).start()
            return

        ext = item.get("container_extension", "ts" if k == "live" else "mp4") or ("ts" if k == "live" else "mp4")
        url = state["client"].build_stream_url(k, item.get("stream_id"), ext, item=item)
        play_stream_url(url, title)

    def switch_tab(tab_key):
        search_tf.value = ""
        if tab_key == "fav":
            state["in_fav"] = True
            state["view_mode"] = "streams"
            combined = []
            for k in ("live", "vod", "series"):
                combined.extend(favorites.get(k, []))
            state["streams"] = combined
            render_list()
        else:
            state["in_fav"] = False
            state["kind"] = tab_key
            back_to_cats()
        publish_status()

    # --- WIDOK LOGOWANIA I WIDOK DASHBOARDU ---
    login_view = ft.Container(expand=True)
    dashboard_view = ft.Container(expand=True, visible=False)

    def do_login(_e=None):
        status_banner.value = "⏳ Łączenie z serwerem..."
        page.update()

        def _w():
            try:
                m3u_val = m3u_tf.value.strip()
                srv_val = srv_tf.value.strip()
                usr_val = usr_tf.value.strip()
                pas_val = pas_tf.value

                if m3u_val and (not usr_val or not pas_val):
                    ext = extract_xtream_from_m3u_url(m3u_val)
                    if ext:
                        client = XtreamClient(*ext)
                    else:
                        client = M3UClient(m3u_val)
                else:
                    client = XtreamClient(srv_val, usr_val, pas_val)

                client.login()
                state["client"] = client
                state["categories"] = {"live": [], "vod": [], "series": []}
                if not state["active_profile_name"]:
                    state["active_profile_name"] = f"{client.username} ({client.server})"

                page.client_storage.set("server", srv_val)
                page.client_storage.set("username", usr_val)
                page.client_storage.set("password", pas_val)
                page.client_storage.set("m3u_url", m3u_val)

                login_view.visible = False
                dashboard_view.visible = True
                switch_tab("live")
                publish_status()
            except Exception as ex:
                status_banner.value = f"❌ Błąd logowania: {ex}"
                status_banner.color = "#f87171"
                page.update()

        threading.Thread(target=_w, daemon=True).start()

    def do_logout(_e=None):
        stop_playback()
        state["client"] = None
        dashboard_view.visible = False
        login_view.visible = True
        page.update()
        publish_status()

    def open_remote_settings_dlg(_e=None):
        name_in = ft.TextField(label="Nazwa własna tego odbiornika", value=page.client_storage.get("device_name") or device_name)
        id_in = ft.TextField(label="Unikalny Kod ID (wpisz ten sam w panel_zdalny.html)", value=page.client_storage.get("device_id") or device_id)

        def _save(_):
            nn = name_in.value.strip() or "Android TV"
            ni = re.sub(r'[^a-zA-Z0-9_-]', '', id_in.value.strip()) or device_id
            page.client_storage.set("device_name", nn)
            page.client_storage.set("device_id", ni)
            header_dev_lbl.value = f"📺 {nn} [{ni}]"
            status_banner.value = f"🟢 Zdalny Cloud aktywny — Odbiornik: {nn} | Kod ID: {ni}"
            dlg.open = False
            page.update()
            publish_status()

        dlg = ft.AlertDialog(
            title=ft.Text("🌐 Konfiguracja Zdalnego Odbiornika"),
            content=ft.Column([
                ft.Text("Wpisz ten Kod ID w pliku panel_zdalny.html w przeglądarce, aby zdalnie dodawać listy i sterować aplikacją:", size=12),
                name_in,
                id_in,
            ], tight=True, spacing=10),
            actions=[ft.ElevatedButton("💾 Zapisz", on_click=_save, bgcolor="#2563eb", color="white")]
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    # Budowa ekranu logowania
    login_view.content = ft.Column(
        [
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text("▶ IPTV V4.4 PRO ANDROID", size=20, weight=ft.FontWeight.BOLD),
                        ft.ElevatedButton("🌐 Nazwa / Kod ID", on_click=open_remote_settings_dlg, bgcolor="#0d9488", color="white")
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    status_banner,
                    srv_tf,
                    usr_tf,
                    pas_tf,
                    m3u_tf,
                    ft.ElevatedButton("🚀 POŁĄCZ Z SERWEREM", on_click=do_login, bgcolor="#2563eb", color="white", height=45),
                ], spacing=10),
                bgcolor="#111625",
                padding=24,
                border_radius=12,
                width=520,
            )
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True
    )

    # Budowa ekranu głównego (2 kolumny: Lista po lewej + Wideo po prawej)
    left_panel = ft.Container(
        width=340,
        bgcolor="#111625",
        padding=10,
        border_radius=10,
        content=ft.Column([
            ft.Row([
                ft.ElevatedButton("📺 TV", on_click=lambda _: switch_tab("live"), bgcolor="#2563eb", color="white", expand=True),
                ft.ElevatedButton("🎬 Filmy", on_click=lambda _: switch_tab("vod"), bgcolor="#161d30", color="white", expand=True),
                ft.ElevatedButton("🎞 Seriale", on_click=lambda _: switch_tab("series"), bgcolor="#161d30", color="white", expand=True),
            ], spacing=4),
            ft.Row([search_tf]),
            list_view
        ], expand=True)
    )

    right_panel = ft.Container(
        expand=True,
        bgcolor="#111625",
        padding=10,
        border_radius=10,
        content=ft.Column([
            ft.Row([
                header_dev_lbl,
                ft.Row([
                    ft.ElevatedButton("🌐 Kod ID", on_click=open_remote_settings_dlg, bgcolor="#0d9488", color="white"),
                    ft.ElevatedButton("⇄ Zmień serwer", on_click=do_logout, bgcolor="#dc2626", color="white"),
                ], spacing=6)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(content=video_player, expand=True, bgcolor="black", border_radius=8),
            ft.Row([
                now_playing_txt,
                ft.Row([
                    ft.ElevatedButton("⏯ Play/Pauza", on_click=toggle_play_pause, bgcolor="#2563eb", color="white"),
                    ft.ElevatedButton("⏹ Stop", on_click=stop_playback, bgcolor="#4c1d24", color="#f87171"),
                    ft.ElevatedButton("ℹ TMDB", on_click=show_info_dialog, bgcolor="#161d30", color="#60a5fa"),
                    ft.ElevatedButton("VLC ↗", on_click=open_in_android_vlc, bgcolor="#161d30", color="white"),
                ], spacing=6)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        ], expand=True)
    )

    dashboard_view.content = ft.Row([left_panel, right_panel], expand=True, spacing=10)
    page.add(login_view, dashboard_view)

    # --- PĘTLA NASŁUCHU ZDALNYCH ROZKAZÓW Z PRZEGLĄDARKI WWW ---
    def handle_remote_command(payload):
        cmd = payload.get("cmd")
        if cmd == "ping_status":
            publish_status()
        elif cmd == "rename_device":
            new_n = (payload.get("device_name") or "").strip()
            if new_n:
                page.client_storage.set("device_name", new_n)
                cur_id = page.client_storage.get("device_id") or device_id
                header_dev_lbl.value = f"📺 {new_n} [{cur_id}]"
                status_banner.value = f"🟢 Zdalny Cloud — Odbiornik: {new_n} | Kod ID: {cur_id}"
                page.update()
                publish_status()
        elif cmd == "add_playlist":
            prof_name = payload.get("name") or "Zdalna Lista"
            new_p = {
                "name": prof_name,
                "mode": payload.get("mode", "xtream"),
                "server": (payload.get("server") or "").strip(),
                "username": (payload.get("username") or "").strip(),
                "password": payload.get("password") or "",
                "m3u_url": (payload.get("m3u_url") or "").strip(),
                "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            nonlocal profiles
            profiles = [p for p in profiles if p.get("name") != prof_name]
            profiles.insert(0, new_p)
            page.client_storage.set("profiles", profiles)

            if payload.get("auto_connect", True):
                state["active_profile_name"] = prof_name
                if new_p["mode"] == "xtream":
                    srv_tf.value = new_p["server"]
                    usr_tf.value = new_p["username"]
                    pas_tf.value = new_p["password"]
                    m3u_tf.value = ""
                else:
                    m3u_tf.value = new_p["m3u_url"]
                    usr_tf.value = ""
                    pas_tf.value = ""
                do_login()
            else:
                publish_status()
        elif cmd == "switch_profile":
            t_name = payload.get("name")
            for p in profiles:
                if p.get("name") == t_name:
                    state["active_profile_name"] = t_name
                    if p.get("mode") == "xtream":
                        srv_tf.value = p.get("server", "")
                        usr_tf.value = p.get("username", "")
                        pas_tf.value = p.get("password", "")
                        m3u_tf.value = ""
                    else:
                        m3u_tf.value = p.get("m3u_url", "")
                        usr_tf.value = ""
                    do_login()
                    break
        elif cmd == "delete_profile":
            t_name = payload.get("name")
            profiles = [p for p in profiles if p.get("name") != t_name]
            page.client_storage.set("profiles", profiles)
            publish_status()
        elif cmd == "remote_control":
            act = payload.get("action")
            if act == "stop":
                stop_playback()
            elif act == "play_pause":
                toggle_play_pause()
            elif act == "logout":
                do_logout()

    def cloud_poll_worker():
        poll_s = make_session()
        while True:
            try:
                cid = re.sub(r'[^a-zA-Z0-9_-]', '', page.client_storage.get("device_id") or device_id)
                url = f"https://ntfy.sh/iptv_v4_pro_{cid}/json?poll=1&since={start_ts}"
                r = poll_s.get(url, timeout=8)
                if r.status_code == 200 and r.text.strip():
                    for line in r.text.strip().splitlines():
                        try:
                            ev = json.loads(line)
                            if ev.get("event") == "message":
                                payload = json.loads(ev.get("message", ""))
                                uid = payload.get("msg_uid") or ev.get("id")
                                if uid not in processed_ids:
                                    processed_ids.add(uid)
                                    page.client_storage.set("processed_ids", list(processed_ids)[-50:])
                                    handle_remote_command(payload)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(3)

    threading.Thread(target=cloud_poll_worker, daemon=True).start()
    publish_status()


ft.app(target=main)
