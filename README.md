# iDash — iRacing Overlay Suite

PyQt5-Desktop-App, die aus iRacing-Telemetrie (irsdk) per WebSocket
HTML-Overlays für OBS speist. Architektur- und Feature-Details in
[`CLAUDE.md`](CLAUDE.md).

## Entwicklung

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
cd app && python overlay_manager.py
```

Testen ohne iRacing über Record/Replay — siehe `CLAUDE.md` (Abschnitt
„Testen ohne iRacing").

## Build (Windows)

```bat
pip install -r requirements-build.txt
build_all.bat
```

Erzeugt den portablen Ordner `dist\iDash\` (PyInstaller `--onedir`). Ist das
Velopack-CLI `vpk` installiert (`dotnet tool install -g vpk`), wird zusätzlich
ein Update-Paket in `releases\` gebaut.

## Release / Auto-Update

iDash nutzt [Velopack](https://velopack.io) für Self-Updates. Die Version ist
in `app/_version.py` definiert; ein Release entsteht durch Pushen eines
Git-Tags `vX.Y.Z` — die GitLab-CI (`.gitlab-ci.yml`) baut, packt und
veröffentlicht das Release. In der App: **„Auf Updates prüfen"** im Overlay
Manager. Der Update-Feed wird über `IDASH_UPDATE_FEED` bzw.
`DEFAULT_FEED_URL` in `app/updater.py` gesetzt.
