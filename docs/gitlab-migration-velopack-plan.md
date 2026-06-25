# iDash → GitLab-Migration + Velopack Self-Update

> Geparkter Plan (noch nicht umgesetzt). Stand: 2026-06-25.

## Context

iDash liegt aktuell auf GitHub (`famlaubscher/idash`), wird manuell per
`build_all.bat` (PyInstaller `--onefile`) gebaut und hat **kein Versioning,
keine CI und keinen Auto-Update-Mechanismus**. Ziel: Projekt zum übrigen
GitLab-Kontext (gitlab.com) konsolidieren, um dieselben Lösungsmuster wie bei
den RFA-Projekten wiederzuverwenden, und ein **Velopack-Self-Update**
integrieren. Velopack hat offiziellen Python-Support (`pip install velopack`,
`velopack.App().run()`-Bootstrap, `vpk pack`), verlangt aber zwingend einen
**PyInstaller `--onedir`-Build** statt `--onefile`.

Entscheidungen: gitlab.com (SaaS) · **voll umziehen** (GitLab wird einziger
Remote, GitHub stilllegen) · vorhandenen self-hosted **Windows-Runner
wiederverwenden** · Velopack-Pakete als **GitLab Releases/Packages** hosten.

---

## Teil A — GitLab-Migration (voll umziehen)

1. **Projekt anlegen** auf gitlab.com unter deinem Namespace (privat). Kein
   Import-Wizard nötig — direkt pushen.
2. **Alle Branches/Tags pushen** und Remote umstellen:
   - `git remote rename origin github`
   - `git remote add origin git@gitlab.com:<namespace>/idash.git`
   - `git push origin --all` und `git push origin --tags`
   - Default-Branch in GitLab auf `main` setzen.
3. **GitHub stilllegen**: nach erfolgreichem Push archivieren (oder
   `git remote remove github`). Repo ist klein — `.venv/`, `dumps/`, `dist/`
   sind in `.gitignore`; einziges größeres getracktes Asset ist
   `overlays/iDash_marketing_2048.png` (5.6 MB, unkritisch).
4. **Repo-Hygiene vor Migration** (klein, ermöglicht reproduzierbare CI):
   - `requirements.txt` ergänzen (fehlt heute): `PyQt5`, `PyQtWebEngine`,
     `pyirsdk`, `websockets`, `PyYAML` (+ Build: `pyinstaller`, `velopack`).
   - Kurzes `README.md` (Run/Build/Release in 10 Zeilen).
   - `CLAUDE.md`-Hinweis auf `/ultrareview`/GitHub-PRs ist GitHub-spezifisch —
     unkritisch, kann bleiben.

## Teil B — Windows-Runner für mehrere Projekte/Accounts

Offene Frage geklärt: **ein** Runner-Dienst kann mehrere GitLab-Projekte/Accounts
bedienen — kein zweiter Dienst nötig.

- Ein installierter `gitlab-runner`-**Windows-Dienst** liest **eine**
  `config.toml` mit beliebig vielen `[[runners]]`-Einträgen, jeder mit eigenem
  Registrierungs-Token. Funktioniert auch über **verschiedene gitlab.com-Accounts**
  hinweg.
- Vorgehen: `gitlab-runner register` erneut mit dem iDash-Projekt-Token →
  neuer `[[runners]]`-Block. `concurrent = N` global erlaubt Parallelläufe.
- **Routing über Tags**: iDash-Runner-Eintrag z. B. Tag `win-idash`, Jobs in
  `.gitlab-ci.yml` mit `tags: [win-idash]`. Kollidiert nicht mit RFA-Jobs.
- **Executor**: `shell` (PowerShell) — native Windows-Builds (PyInstaller +
  vpk + .NET). Voraussetzung auf dem Host: Python 3.12, .NET SDK (für `vpk`;
  bei RFA vermutlich schon da), `dotnet tool install -g vpk`.

## Teil C — Versioning (eine Quelle der Wahrheit)

Heute keine Version. Einführen:
- Datei `app/_version.py` mit `__version__ = "0.1.0"`, importiert in
  `overlay_manager.py` (Fenstertitel optional).
- **CI leitet die Version aus dem Git-Tag ab** (`vX.Y.Z` → `packVersion`);
  lokaler Fallback auf `_version.py`. Release = Tag pushen.

## Teil D — Velopack-Integration im Code

**Kritisch: Build-Modus auf `--onedir` umstellen.** Velopack aktualisiert ein
Verzeichnis, `--onefile` ist inkompatibel. `resource_path()`
(`overlay_manager.py:33`) nutzt `sys._MEIPASS`, das auch im `--onedir`-Bundle
gesetzt ist → Ressourcen-Auflösung bleibt funktionsfähig.

1. **`requirements.txt`** um `velopack` ergänzen.
2. **Bootstrap** ganz am Anfang von `main()` (`overlay_manager.py:1405`),
   **vor** `QApplication(sys.argv)`:
   ```python
   import velopack
   velopack.App().run()   # verarbeitet Install/Update/Restart-Hooks, danach Exit
   ```
3. **Update-Funktion** + manueller Trigger (Manager hat bereits eine
   Button-Leiste):
   ```python
   def check_for_updates():
       mgr = velopack.UpdateManager(FEED_URL)
       info = mgr.check_for_updates()
       if not info:
           return
       mgr.download_updates(info)
       mgr.apply_updates_and_restart(info)
   ```
   Empfehlung: Button **„Nach Updates suchen"** (manuell) + optionaler stiller
   Check beim Start in einem Thread (GUI nicht blockieren).
4. **`build_all.bat` umstellen**: `--onefile` entfernen (onedir ist Default),
   Output bleibt `dist\iDash\`. Danach lokaler Pack-Schritt:
   ```
   vpk pack --packId ApexOverlay.iDash --packVersion <ver> ^
            --packDir dist\iDash --mainExe iDash.exe --packTitle iDash
   ```
   `--packId` global eindeutig (z. B. `ApexOverlay.iDash`).

## Teil E — CI-Pipeline (`.gitlab-ci.yml`)

Neue Datei im Repo-Root, Jobs `tags: [win-idash]`, nur auf Tags
(`rules: if: $CI_COMMIT_TAG`):

```yaml
stages: [build, release]

variables:
  PACK_ID: "ApexOverlay.iDash"

build_and_release:
  stage: release
  tags: [win-idash]
  rules:
    - if: $CI_COMMIT_TAG
  script:
    - py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
    - pip install -r requirements.txt pyinstaller velopack
    - $ver = $env:CI_COMMIT_TAG.TrimStart("v")
    - pyinstaller --noconfirm --clean --windowed --name iDash ... app\overlay_manager.py   # onedir
    # vorherige Releases ziehen (ermöglicht Delta-Updates):
    - vpk download <gitlab-source> --outputDir releases   # siehe Teil F
    - vpk pack --packId $env:PACK_ID --packVersion $ver --packDir dist\iDash --mainExe iDash.exe --outputDir releases
    - # Assets an GitLab-Release hängen (release-cli / glab / API)
  release:
    tag_name: $CI_COMMIT_TAG
    description: "iDash $CI_COMMIT_TAG"
    assets: { links: [ ... releases/*.nupkg, releases.win.json, Setup.exe ... ] }
```

Velopack-Assets (`releases.win.json`, `*-full.nupkg`, `*-delta.nupkg`,
`iDash-win-Setup.exe`) an das GitLab-Release hängen über Generic Package
Registry + `release-cli`/`glab` (stabile, von Velopack lesbare URLs). Auth via
`CI_JOB_TOKEN`.

## Teil F — Update-Feed-Quelle (offener Verifikationspunkt)

Velopack hat im C#-SDK eine `GitlabRelease`-Source. **Python-SDK-Stand
(`UpdateManager`) bei der Umsetzung verifizieren** — er nimmt sicher eine
**URL** (SimpleWeb-Source) an; GitLab-Source-Parität ist offen. Bevorzugte
Reihenfolge:

1. **Falls Python-SDK GitLab unterstützt** → `GitlabRelease`-Source mit
   Projekt-URL + (privat) Token.
2. **Sonst Generic Package Registry als statischer Feed**: alle Velopack-Files
   unter eine stabile Basis-URL (`…/packages/generic/idash/<channel>/`) →
   `UpdateManager(<diese-URL>)`.
3. **Fallback GitLab Pages**: vpk-Output-Ordner als statische Seite →
   `UpdateManager("https://<ns>.gitlab.io/idash/")`. Robust, ohne Token für
   öffentliche Projekte.

Empfehlung: Option 2 (passt zu „Releases/Packages", privat-tauglich via Token),
Option 3 als Fallback.

---

## Kritische Dateien

- `build_all.bat` — `--onefile` → `--onedir`, vpk-Pack-Schritt (Teil D).
- `app/overlay_manager.py` — `velopack.App().run()` in `main()` (Zeile 1405),
  `check_for_updates()` + Button; `app/_version.py` neu.
- `requirements.txt` — **neu** (Teil A/D).
- `.gitlab-ci.yml` — **neu** (Teil E).
- `README.md` — **neu**, kurz.

## Verifikation (End-to-End)

1. **Lokal — Build/Bootstrap**: `build_all.bat` (onedir) → `dist\iDash\iDash.exe`
   startet, GUI wie gehabt, Velopack-Bootstrap stört nichts.
2. **Lokal — Update-Zyklus**: `vpk pack` v0.1.0 in `releases\`; lokal hosten
   (`python -m http.server` im `releases`-Ordner), `UpdateManager` auf
   `http://localhost:8000`. Dann `_version.py` → 0.1.1, erneut packen, App
   „Nach Updates suchen" → Download + Restart auf 0.1.1 bestätigen.
3. **Runner**: zweiten `[[runners]]`-Eintrag registrieren, Test-Job mit
   `tags: [win-idash]` (`python --version`/`vpk --version`).
4. **CI-Release**: Tag `v0.1.0` pushen → Pipeline baut, GitLab-Release mit
   Velopack-Assets entsteht; installierte App findet das Release als Feed.
5. **Migration**: `git push origin --all/--tags` ohne Fehler, GitHub archiviert.

## Quellen

- Velopack Python: https://docs.velopack.io/getting-started/python
- Velopack Deploy-CLI: https://docs.velopack.io/distributing/deploy-cli
- Velopack GitlabRelease-Source: https://docs.velopack.io/reference/cs/Velopack/Sources/GitlabRelease
