# 📺 IPTV BP (Android TV & PC)

Odtwarzacz IPTV (Xtream Codes API oraz listy M3U/M3U8) zoptymalizowany pod **Android TV Box** (obsługa pilota) oraz system Windows.

## ✨ Główne funkcje
- **Pełnoekranowy OSD Live TV:**
  - Przełączanie kanałów klawiszami **Góra / Dół** (`CH+` / `CH-`) bez wychodzenia z odtwarzacza.
  - Boczna lista kanałów OSD otwierana klawiszem **OK** na pełnym ekranie.
  - **Przełączanie kategorii w OSD** klawiszami **Lewo / Prawo** (`◄` / `►`) podczas oglądania kanału.
  - Wybieranie numeru kanału bezpośrednio z klawiatury numerycznej pilota (`0–9`) oraz szybki powrót do poprzedniego kanału (`0`).
- **Wielowarstwowy silnik polskich opisów (VOD / Seriale / EPG):**
  - Pobieranie metadanych z **TMDB PL**, **serwera VOD Xtream**, **polskiej Wikipedii** oraz **TVMaze**.
  - Automatyczne tłumaczenie angielskich opisów filmów, seriali i EPG na język polski w locie.
- **Inteligentne sortowanie:**
  - Polskie kategorie oraz polskie kanały zawsze na górze listy.
  - Kategoria *Ostatnio dodane* dla filmów i seriali.
- **Wsparcie dla zewnętrznego odtwarzacza VLC** (tryby `vlc://`, `Android Intent`, `HTTP`).

## 🚀 Uruchomienie na PC (Windows / Linux)
```bash
pip install -r requirements.txt
python main.py
