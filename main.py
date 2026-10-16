# -*- coding: utf-8 -*-
"""
IPTV Client V4.4 PRO - ANDROID TV EDITION (Safe Boot & VLC Intent)
---------------------------------------------------------------------------------------
"""

import datetime
import json
import re
import threading
import time
import traceback
from urllib.parse import parse_qs, urlparse
import uuid

import flet as ft
import requests

TMDB_API_KEY = "6e2d327ee657c8671b3eda952cd260a0"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
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
    clean = re.sub(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+", "", title)
    clean = re.sub(r"\[.*?\]", "", clean)
    clean = re.sub(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$", "", clean)
    clean = re.sub(r"\s*\(\d{4}\).*$", "", clean)
    clean = re.sub(
        r"\b(FHD|UHD|4K|HD|1080p|720p|HEVC|H265|LEKTOR|DUBBING|NAPISY|PL|EN|FR)\b.*$",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return clean.strip(" -|_:")


def fetch_tmdb_details(title):
    clean_title = clean_media_title(title)
    if not clean_title:
        return None
    try:
        url = (
            f"https://api.themoviedb.org/3/search/multi"
            f"?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_title)}&language=pl-PL"
        )
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                best = results[0]
                poster_path = best.get("poster_path")
                release = (
                    best.get("release_date") or best.get("first_air_date") or ""
                )
                return {
                    "poster": (
                        f"https://image.tmdb.org/t/p/w500{poster_path}"
                        if poster_path
                        else None
                    ),
                    "plot": best.get("overview") or "",
                    "rating": (
                        str(round(best.get("vote_average", 0), 1))
                        if best.get("vote_average")
                        else ""
                    ),
                    "year": release[:4] if release else "",
                }
    except Exception:
        pass
    return None


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
            name = (
                extinf_line.split(",", 1)[1].strip()
                if (extinf_line and "," in extinf_line)
                else f"Kanał {seq_id}"
            )
            group = (attrs.get("group-title") or "Bez kategorii").strip()
            logo = attrs.get("tvg-logo") or ""
            kind, ep_match = self._classify_entry(url, group, name)

            if group not in cat_sets[kind]:
                cat_id = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cat_id
                self.categories[kind].append(
                    {"category_id": cat_id, "category_name": group}
                )
                self.streams_by_cat[kind][cat_id] = []
            else:
                cat_id = cat_sets[kind][group]

            item_id = f"m3u_item_{seq_id}"
            seq_id += 1
            self.direct_urls[item_id] = url

            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cat_id].append({
                    "stream_id": item_id,
                    "name": name,
                    "stream_icon": logo,
                    "direct_url": url,
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
                    series_id = f"m3u_ser_{seq_id}"
                    series_groups[series_key] = series_id
                    self.streams_by_cat["series"][cat_id].append({
                        "series_id": series_id,
                        "name": base_title,
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

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        return self.direct_urls.get(stream_id, "")


def safe_storage_get(page, key, default=None):
    try:
        val = page.client_storage.get(key)
        return val if val is not None else default
    except Exception:
        return default


def safe_storage_set(page, key, val):
    try:
        page.client_storage.set(key, val)
    except Exception:
        pass


def main(page: ft.Page):
    try:
        page.title = "IPTV V4.4 PRO Android TV"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = "#0b0e17"
        page.padding = 10

        device_id = safe_storage_get(page, "device_id")
        if not device_id:
            device_id = f"android-{uuid.uuid4().hex[:6]}"
            safe_storage_set(page, "device_id", device_id)

        device_name = safe_storage_get(page, "device_name", "Android TV Box")
        profiles = safe_storage_get(page, "profiles", [])
        auto_vlc_saved = safe_storage_get(page, "auto_vlc", True)

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
            "auto_vlc": bool(auto_vlc_saved),
            "zone": "login",
            "tab_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "login_idx": 0,
            "page_offset": 0,
        }

        PAGE_SIZE = 60
        session = make_session()

        def publish_status():
            def _worker():
                try:
                    clean_id = re.sub(
                        r"[^a-zA-Z0-9_-]",
                        "",
                        safe_storage_get(page, "device_id", device_id),
                    )
                    status_topic = f"iptv_v4_pro_{clean_id}_status"
                    if state["is_playing"]:
                        st_lbl = "⏸ PAUZA" if state["paused"] else "▶ ODTWARZANIE"
                    else:
                        st_lbl = "⏹ ZATRZYMANO"

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
                            "host": (
                                p.get("server") or p.get("m3u_url") or ""
                            )[:30],
                            "added_at": p.get("added_at", ""),
                            "active": (
                                state["client"] is not None
                                and p.get("name") == state["active_profile_name"]
                            ),
                        })

                    snapshot = {
                        "type": "player_status",
                        "device_name": safe_storage_get(page, "device_name", device_name),
                        "device_id": clean_id,
                        "ts": int(time.time() * 1000),
                        "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
                        "state_label": st_lbl,
                        "now_playing": state["now_playing"],
                        "active_server": srv_txt,
                        "current_tab": (
                            "ulubione" if state["in_fav"] else state["kind"]
                        ),
                        "volume": state["volume"],
                        "muted": state["muted"],
                        "profiles": prof_summary,
                    }
                    session.post(
                        f"https://ntfy.sh/{status_topic}",
                        data=json.dumps(snapshot, ensure_ascii=False).encode(
                            "utf-8"
                        ),
                        timeout=6,
                    )
                except Exception:
                    pass

            threading.Thread(target=_worker, daemon=True).start()

        # Bezpieczny panel zastępujący wideo (bez ft.Icons, które mogło powodować crash)
        video_placeholder = ft.Container(
            content=ft.Column(
                [
                    ft.Text("📺", size=54),
                    ft.Text(
                        "Tryb odtwarzacza zewnętrznego (VLC)",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color="#38bdf8",
                    ),
                    ft.Text(
                        "Wybierz kanał lub film z listy po lewej stronie i naciśnij OK na pilocie,\naby uruchomić strumień bezpośrednio w aplikacji VLC.",
                        size=13,
                        color="#94a3b8",
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            expand=True,
            bgcolor="#0f172a",
            border_radius=8,
            border=ft.border.all(1, "#1e293b"),
            alignment=ft.alignment.center,
        )

        now_playing_txt = ft.Text(
            "▶ Pilot TV: ⬅️ ➡️ zmiana okna | ⬆️ ⬇️ góra/dół | OK zatwierdź",
            weight=ft.FontWeight.BOLD,
            size=14,
            color="#38bdf8",
        )
        header_dev_lbl = ft.Text(
            f"📺 {device_name} [{device_id}]",
            size=13,
            color="#10b981",
            weight=ft.FontWeight.BOLD,
        )
        list_view = ft.ListView(
            expand=True, spacing=6, padding=4, auto_scroll=False
        )
        search_tf = ft.TextField(
            hint_text="Szukaj...",
            height=40,
            text_size=13,
            content_padding=8,
            expand=True,
        )
        status_banner = ft.Text(
            f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}",
            color="#10b981",
            size=13,
        )

        srv_tf = ft.TextField(
            label="Adres serwera (http://serwer:8080)",
            value=safe_storage_get(page, "server", "http://"),
        )
        usr_tf = ft.TextField(
            label="Użytkownik", value=safe_storage_get(page, "username", "")
        )
        pas_tf = ft.TextField(
            label="Hasło",
            password=True,
            can_reveal_password=True,
            value=safe_storage_get(page, "password", ""),
        )
        m3u_tf = ft.TextField(
            label="Lub link do listy M3U / M3U8",
            value=safe_storage_get(page, "m3u_url", ""),
        )

        def open_remote_settings_dlg(_e=None):
            name_in = ft.TextField(
                label="Nazwa własna tego odbiornika",
                value=safe_storage_get(page, "device_name", device_name),
            )
            id_in = ft.TextField(
                label="Unikalny Kod ID",
                value=safe_storage_get(page, "device_id", device_id),
            )

            def _save(_):
                nn = name_in.value.strip() or "Android TV"
                ni = (
                    re.sub(r"[^a-zA-Z0-9_-]", "", id_in.value.strip()) or device_id
                )
                safe_storage_set(page, "device_name", nn)
                safe_storage_set(page, "device_id", ni)
                header_dev_lbl.value = f"📺 {nn} [{ni}]"
                dlg.open = False
                page.update()
                publish_status()

            dlg = ft.AlertDialog(
                title=ft.Text("🌐 Konfiguracja Zdalnego Odbiornika"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Wpisz ten Kod ID w panelu sterowania:",
                            size=12,
                        ),
                        name_in,
                        id_in,
                    ],
                    tight=True,
                    spacing=10,
                ),
                actions=[
                    ft.ElevatedButton(
                        "💾 Zapisz", on_click=_save, bgcolor="#2563eb", color="white"
                    )
                ],
            )
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        btn_remote_id = ft.ElevatedButton(
            "🌐 Nazwa / Kod ID",
            on_click=open_remote_settings_dlg,
            bgcolor="#0d9488",
            color="white",
        )
        btn_submit_login = ft.ElevatedButton(
            "🚀 POŁĄCZ Z SERWEREM",
            on_click=lambda _: do_login(),
            bgcolor="#2563eb",
            color="white",
            height=48,
        )

        login_items = [btn_remote_id, srv_tf, usr_tf, pas_tf, m3u_tf, btn_submit_login]

        def launch_vlc_intent(raw_url: str, title: str = ""):
            if not raw_url:
                return
            clean_u = raw_url.strip()
            now_playing_txt.value = (
                f"🚀 Otwieranie w VLC: {title or state['now_playing']}"
            )
            page.update()
            try:
                # Używamy schematu vlc://, aby Android przekierował strumień prosto do aplikacji VLC,
                # zamiast otwierać pustą kartę w przeglądarce systemowej
                vlc_url = clean_u if clean_u.startswith("vlc://") else f"vlc://{clean_u}"
                page.launch_url(vlc_url)
            except Exception as ex:
                now_playing_txt.value = f"❌ Błąd VLC: {ex}"
                page.update()

        def open_in_android_vlc(_e=None):
            launch_vlc_intent(state["playing_url"], state["now_playing"])

        def toggle_auto_vlc(_e=None):
            state["auto_vlc"] = not state["auto_vlc"]
            safe_storage_set(page, "auto_vlc", state["auto_vlc"])
            btn_auto_vlc.text = (
                f"Tryb VLC: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}"
            )
            update_focus_styles()

        def play_stream_url(url, title):
            state["playing_url"] = url
            state["now_playing"] = title
            state["is_playing"] = True
            state["paused"] = False
            now_playing_txt.value = f"▶ {title}"
            page.update()

            launch_vlc_intent(url, title)
            publish_status()

        def stop_playback(_e=None):
            state["is_playing"] = False
            state["paused"] = False
            state["now_playing"] = "Brak aktywnego strumienia"
            now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
            page.update()
            publish_status()

        def toggle_play_pause(_e=None):
            state["paused"] = not state["paused"]
            now_playing_txt.value = (
                "⏸ PAUZA" if state["paused"] else f"▶ {state['now_playing']}"
            )
            page.update()
            publish_status()

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

        btn_tab_live = ft.ElevatedButton(
            "📺 TV", on_click=lambda _: switch_tab("live", 0), expand=True
        )
        btn_tab_vod = ft.ElevatedButton(
            "🎬 Filmy", on_click=lambda _: switch_tab("vod", 1), expand=True
        )
        btn_tab_ser = ft.ElevatedButton(
            "🎞 Seriale", on_click=lambda _: switch_tab("series", 2), expand=True
        )
        tab_buttons = [btn_tab_live, btn_tab_vod, btn_tab_ser]

        btn_play_pause = ft.ElevatedButton(
            "⏯ Play/Pauza", on_click=toggle_play_pause
        )
        btn_stop = ft.ElevatedButton("⏹ Stop", on_click=stop_playback)
        btn_vlc = ft.ElevatedButton("🚀 Otwórz w VLC", on_click=open_in_android_vlc)
        btn_auto_vlc = ft.ElevatedButton(
            f"Tryb VLC: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}",
            on_click=toggle_auto_vlc,
        )
        btn_tmdb = ft.ElevatedButton("ℹ Opis TMDB", on_click=show_info_dialog)
        btn_cloud_id = ft.ElevatedButton(
            "🌐 Kod ID", on_click=lambda _: open_remote_settings_dlg()
        )
        btn_logout = ft.ElevatedButton("⇄ Zmień serwer", on_click=lambda _: do_logout())

        right_buttons = [
            btn_vlc,
            btn_auto_vlc,
            btn_play_pause,
            btn_stop,
            btn_tmdb,
            btn_cloud_id,
            btn_logout,
        ]
        list_item_buttons = []
        list_item_actions = []

        left_panel = ft.Container(
            width=380,
            bgcolor="#111625",
            padding=10,
            border_radius=10,
            border=ft.border.all(3, "#38bdf8"),
        )
        right_panel = ft.Container(
            expand=True,
            bgcolor="#111625",
            padding=10,
            border_radius=10,
            border=ft.border.all(1, "#1e293b"),
        )

        login_view = ft.Container(expand=True, visible=True)
        dashboard_view = ft.Container(expand=True, visible=False)

        def update_focus_styles():
            if login_view.visible:
                for i, item in enumerate(login_items):
                    is_focused = state["zone"] == "login" and state["login_idx"] == i
                    if isinstance(item, ft.TextField):
                        item.border_color = "#f59e0b" if is_focused else "#334155"
                        item.focused_border_color = "#f59e0b"
                    elif isinstance(item, ft.ElevatedButton):
                        item.bgcolor = (
                            "#f59e0b"
                            if is_focused
                            else ("#0d9488" if i == 0 else "#2563eb")
                        )
                        item.color = "black" if is_focused else "white"
                page.update()
                return

            if not dashboard_view.visible:
                return

            left_panel.border = ft.border.all(
                3 if state["zone"] in ("tabs", "list") else 1,
                "#38bdf8" if state["zone"] in ("tabs", "list") else "#1e293b",
            )
            right_panel.border = ft.border.all(
                3 if state["zone"] == "right" else 1,
                "#38bdf8" if state["zone"] == "right" else "#1e293b",
            )

            for i, b in enumerate(tab_buttons):
                is_focused = state["zone"] == "tabs" and state["tab_idx"] == i
                is_current_kind = (
                    (i == 0 and state["kind"] == "live")
                    or (i == 1 and state["kind"] == "vod")
                    or (i == 2 and state["kind"] == "series")
                )
                b.bgcolor = (
                    "#f59e0b"
                    if is_focused
                    else ("#2563eb" if is_current_kind else "#161d30")
                )
                b.color = "black" if is_focused else "white"

            for i, b in enumerate(list_item_buttons):
                is_focused = state["zone"] == "list" and state["list_idx"] == i
                b.bgcolor = "#0284c7" if is_focused else "#161d30"
                b.color = "white"

            for i, b in enumerate(right_buttons):
                is_focused = state["zone"] == "right" and state["right_idx"] == i
                b.bgcolor = "#f59e0b" if is_focused else "#1e293b"
                b.color = "black" if is_focused else "white"

            page.update()

            if state["zone"] == "list" and list_item_buttons:
                idx = max(0, min(state["list_idx"], len(list_item_buttons) - 1))
                try:
                    list_view.scroll_to(key=f"item_{idx}", duration=80)
                except Exception:
                    pass

        def render_list():
            list_view.controls.clear()
            list_item_buttons.clear()
            list_item_actions.clear()

            q = (search_tf.value or "").strip().lower()
            is_cat = state["view_mode"] == "categories" and not state["in_fav"]

            raw = (
                state["categories"].get(state["kind"], [])
                if is_cat
                else state["streams"]
            )
            filtered = []
            for it in raw:
                nm = (
                    it.get("category_name")
                    if is_cat
                    else (it.get("name") or it.get("title") or "")
                )
                if not q or q in (nm or "").lower():
                    filtered.append(it)
            state["filtered"] = filtered

            btn_idx = 0

            if not is_cat and not state["in_fav"]:
                b_back = ft.ElevatedButton(
                    key=f"item_{btn_idx}",
                    text="↩ Wróć do listy kategorii",
                    height=44,
                    on_click=lambda _: back_to_cats(),
                )
                list_item_buttons.append(b_back)
                list_item_actions.append(back_to_cats)
                list_view.controls.append(b_back)
                btn_idx += 1

            start = state["page_offset"]
            end = start + PAGE_SIZE
            slice_items = filtered[start:end]

            for local_i, item in enumerate(slice_items):
                real_num = start + local_i + 1
                if is_cat:
                    cat_name = item.get("category_name", "Kategoria")
                    action_fn = lambda c=item: open_category(c)
                    label = f"{real_num}. 📁 {cat_name}   ❯"
                else:
                    k = item.get("kind") or state["kind"]
                    icon = "📺" if k == "live" else ("🎬" if k == "vod" else "🎞")
                    nm = item.get("name") or item.get("title") or "Pozycja"
                    action_fn = lambda it=item: on_stream_click(it)
                    label = f"{real_num}. {icon} {nm}"

                b_item = ft.ElevatedButton(
                    key=f"item_{btn_idx}",
                    height=46,
                    content=ft.Row(
                        [
                            ft.Text(
                                label,
                                weight=ft.FontWeight.BOLD,
                                size=14,
                                expand=True,
                                no_wrap=True,
                            )
                        ]
                    ),
                    on_click=lambda _e, fn=action_fn: fn(),
                )
                list_item_buttons.append(b_item)
                list_item_actions.append(action_fn)
                list_view.controls.append(b_item)
                btn_idx += 1

            if state["list_idx"] >= len(list_item_buttons):
                state["list_idx"] = max(0, len(list_item_buttons) - 1)

            update_focus_styles()

        search_tf.on_change = lambda _: render_list()

        def back_to_cats():
            state["view_mode"] = "categories"
            state["active_cat"] = None
            state["page_offset"] = 0
            state["list_idx"] = 0
            if not state["categories"].get(state["kind"]):
                def _w():
                    try:
                        cats = state["client"].get_categories(state["kind"]) or []
                        state["categories"][state["kind"]] = [
                            {"category_id": "__ALL__", "category_name": "🔥 WSZYSTKIE"}
                        ] + cats
                        render_list()
                    except Exception:
                        pass
                threading.Thread(target=_w, daemon=True).start()
            else:
                render_list()

        def open_category(cat_obj):
            state["view_mode"] = "streams"
            state["active_cat"] = cat_obj
            state["page_offset"] = 0
            state["list_idx"] = 0
            list_view.controls.clear()
            list_view.controls.append(
                ft.Text("⏳ Pobieranie listy z serwera...", color="#60a5fa")
            )
            page.update()

            def _w():
                try:
                    items = (
                        state["client"].get_streams(
                            state["kind"], cat_obj.get("category_id")
                        )
                        or []
                    )
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
                        info = state["client"].get_series_info(
                            item.get("series_id")
                        )
                        eps_map = info.get("episodes") or {}
                        if eps_map:
                            first_s = sorted(eps_map.keys())[0]
                            first_ep = eps_map[first_s][0]
                            ext = (
                                first_ep.get("container_extension", "mp4") or "mp4"
                            )
                            url = state["client"].build_stream_url(
                                "series", first_ep.get("id"), ext, item=first_ep
                            )
                            play_stream_url(
                                url,
                                f"{title} - {first_ep.get('title', 'Odcinek 1')}",
                            )
                    except Exception:
                        pass
                threading.Thread(target=_w, daemon=True).start()
                return

            ext = item.get(
                "container_extension", "ts" if k == "live" else "mp4"
            ) or ("ts" if k == "live" else "mp4")
            url = state["client"].build_stream_url(
                k, item.get("stream_id"), ext, item=item
            )
            play_stream_url(url, title)

        def switch_tab(tab_key, tab_idx=0):
            search_tf.value = ""
            state["tab_idx"] = tab_idx
            state["page_offset"] = 0
            state["list_idx"] = 0
            state["in_fav"] = False
            state["kind"] = tab_key
            state["zone"] = "list"
            back_to_cats()
            publish_status()

        def on_key_event(e: ft.KeyboardEvent):
            key = e.key

            if login_view.visible:
                if key == "Arrow Down":
                    if state["login_idx"] < len(login_items) - 1:
                        state["login_idx"] += 1
                elif key == "Arrow Up":
                    if state["login_idx"] > 0:
                        state["login_idx"] -= 1
                elif key in ("Enter", "Select", " "):
                    idx = state["login_idx"]
                    if idx == 0:
                        open_remote_settings_dlg()
                    elif idx == len(login_items) - 1:
                        do_login()
                    else:
                        item = login_items[idx]
                        try:
                            item.focus()
                        except Exception:
                            pass
                update_focus_styles()
                return

            if not dashboard_view.visible:
                return

            if key == "Arrow Right":
                if state["zone"] == "tabs":
                    if state["tab_idx"] < len(tab_buttons) - 1:
                        state["tab_idx"] += 1
                    else:
                        state["zone"] = "right"
                        state["right_idx"] = 0
                elif state["zone"] == "list":
                    state["zone"] = "right"
                    state["right_idx"] = 0
                elif state["zone"] == "right":
                    if state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1

            elif key == "Arrow Left":
                if state["zone"] == "right":
                    if state["right_idx"] > 0:
                        state["right_idx"] -= 1
                    else:
                        state["zone"] = "list"
                elif state["zone"] == "tabs":
                    if state["tab_idx"] > 0:
                        state["tab_idx"] -= 1

            elif key == "Arrow Down":
                if state["zone"] == "tabs":
                    state["zone"] = "list"
                    state["list_idx"] = 0
                elif state["zone"] == "list":
                    if list_item_buttons and state["list_idx"] < len(list_item_buttons) - 1:
                        state["list_idx"] += 1
                elif state["zone"] == "right":
                    if right_buttons and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1

            elif key == "Arrow Up":
                if state["zone"] == "list":
                    if state["list_idx"] > 0:
                        state["list_idx"] -= 1
                    else:
                        state["zone"] = "tabs"
                elif state["zone"] == "right":
                    if state["right_idx"] > 0:
                        state["right_idx"] -= 1

            elif key in ("Enter", "Select", " ", "Media Play / Pause"):
                if state["zone"] == "tabs":
                    kinds = ["live", "vod", "series"]
                    if 0 <= state["tab_idx"] < len(kinds):
                        switch_tab(kinds[state["tab_idx"]], state["tab_idx"])
                elif state["zone"] == "list":
                    if list_item_actions and 0 <= state["list_idx"] < len(list_item_actions):
                        list_item_actions[state["list_idx"]]()
                elif state["zone"] == "right":
                    idx = state["right_idx"]
                    if idx == 0:
                        open_in_android_vlc()
                    elif idx == 1:
                        toggle_auto_vlc()
                    elif idx == 2:
                        toggle_play_pause()
                    elif idx == 3:
                        stop_playback()
                    elif idx == 4:
                        show_info_dialog()
                    elif idx == 5:
                        open_remote_settings_dlg()
                    elif idx == 6:
                        do_logout()

            elif key in ("Backspace", "Escape"):
                if state["view_mode"] == "streams" and not state["in_fav"]:
                    back_to_cats()

            update_focus_styles()

        page.on_keyboard_event = on_key_event

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
                        state["active_profile_name"] = (
                            f"{client.username} ({client.server})"
                        )

                    safe_storage_set(page, "server", srv_val)
                    safe_storage_set(page, "username", usr_val)
                    safe_storage_set(page, "password", pas_val)
                    safe_storage_set(page, "m3u_url", m3u_val)

                    login_view.visible = False
                    dashboard_view.visible = True
                    state["zone"] = "list"
                    switch_tab("live", 0)
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
            state["zone"] = "login"
            update_focus_styles()
            page.update()
            publish_status()

        login_view.content = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        "▶ IPTV V4.4 PRO ANDROID TV",
                                        size=20,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    btn_remote_id,
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            status_banner,
                            srv_tf,
                            usr_tf,
                            pas_tf,
                            m3u_tf,
                            btn_submit_login,
                        ],
                        spacing=10,
                    ),
                    bgcolor="#111625",
                    padding=24,
                    border_radius=12,
                    width=540,
                )
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

        left_panel.content = ft.Column(
            [
                ft.Row(tab_buttons, spacing=4),
                ft.Row([search_tf]),
                list_view,
            ],
            expand=True,
        )

        right_panel.content = ft.Column(
            [
                ft.Row(
                    [
                        header_dev_lbl,
                        ft.Row([btn_cloud_id, btn_logout], spacing=6),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                video_placeholder,
                now_playing_txt,
                ft.Row(
                    [
                        btn_vlc,
                        btn_auto_vlc,
                        btn_play_pause,
                        btn_stop,
                        btn_tmdb,
                    ],
                    spacing=6,
                    wrap=True,
                ),
            ],
            expand=True,
        )

        dashboard_view.content = ft.Row(
            [left_panel, right_panel], expand=True, spacing=10
        )
        page.add(login_view, dashboard_view)
        update_focus_styles()

    except Exception as e:
        # Diagnostyka na wypadek jakiegokolwiek błędu na Android TV (zamiast czarnego ekranu)
        err_msg = traceback.format_exc()
        page.add(
            ft.Container(
                content=ft.Column([
                    ft.Text("⚠️ Błąd krytyczny podczas startu:", color="red", size=20, weight=ft.FontWeight.BOLD),
                    ft.Text(str(e), color="yellow", size=16),
                    ft.Text(err_msg, color="white", size=11),
                ], scroll=ft.ScrollMode.ALWAYS),
                padding=20,
                bgcolor="#1e293b",
                expand=True
            )
        )
        page.update()


ft.app(target=main)
