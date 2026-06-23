# iDash — iRacing Overlay Suite

## Architektur (Kurz)

`app/telemetry_server.py` ist der Kern: ein Tick-Loop (~60 Hz) baut über die
Builder (`hud`, `relative`, `standings`, `session_info`, `wind`, `env`,
`circle`, `strategy`) aus iRacing-Daten (`irsdk`) ein `payload`-dict und
broadcastet es per WebSocket (`:8765`) an die Overlay-HTML-Seiten. Ein
HTTP-Server (`:8080`, konfigurierbar) liefert die Overlays für OBS aus.

## Testen ohne iRacing — Record/Replay

Damit man nicht ständig iRacing starten muss, gibt es Record/Replay auf zwei
Ebenen. Beide Ebenen landen in EINER `.jsonl`-Aufnahme (eine Zeile pro Tick:
`payload` = fertiger Broadcast, `ir` = rohe gelesene irsdk-Werte).

**Aufzeichnen** (einziges Mal mit laufendem iRacing):
```
cd app
python telemetry_server.py --record           # -> app/dumps/recording_<ts>.jsonl
# oder: --record <pfad>  /  ENV IDASH_RECORD=<pfad>
```
Mit `Ctrl+C` beenden → Datei wird gespeichert.

Aufzeichnen **während das volle iDash läuft** (`overlay_manager.py` startet den
Server im selben Prozess, übergibt aber kein `--record`) → ENV nutzen:
```powershell
$env:IDASH_RECORD = "1"     # Auto-Name in dumps/; oder Ordner/Datei-Pfad
python overlay_manager.py   # Overlays/OBS/UI laufen normal weiter
```
ENV-Werte: `1`/`true`/`yes` oder ein Verzeichnis → Auto-Timestamp-Name; sonst
wörtlicher Dateipfad. NICHT zusätzlich einen zweiten Server starten
(Port-Kollision 8765/8080).

**Abspielen** (kein iRacing nötig; gleiche URLs/Ports wie live):
```
cd app
python replay_server.py                       # nimmt die NEUESTE Aufnahme in dumps/
python replay_server.py dumps/xy.jsonl        # bestimmte Datei
python replay_server.py --mode irsdk          # Builder rechnen neu
# Flags: --speed 2.0   --no-loop
```
Ohne Datei-Argument wird automatisch die neueste `*.jsonl` aus `app/dumps/`
genommen.

- **`--mode payload`** — sendet die aufgezeichneten Payloads 1:1. Schnell &
  robust. Nutzen, wenn man an HTML/CSS/JS der Overlays iteriert.
- **`--mode irsdk`** — füttert ein Fake-IRSDK (`ReplayIRSDK`) mit den rohen
  Werten; die ECHTEN Builder laufen erneut. Nutzen, wenn man Builder-Logik
  ändert (Relative/Standings/Strategy/Circle).

Relevante Dateien: `app/replay_common.py` (`RecordingIRSDK`, `ReplayIRSDK`,
`Recorder`), `app/replay_server.py`. Die Aufnahme-Hooks im Server sind optional
und ändern den Live-Betrieb nicht.

PyCharm Run Configurations (`.idea/runConfigurations/`) — alle starten
`overlay_manager.py` (also MIT GUI/Overlays), unterscheiden sich nur per ENV:
- **iDash** — normaler Start, ohne Record.
- **iDash (Record)** — `IDASH_RECORD=1` → Aufnahme in `dumps/`.
- **iDash (Replay)** — `IDASH_REPLAY=1`: der Manager startet statt des
  Live-Servers den Replay-Server (Payload-Modus, neueste Aufnahme). GUI/Overlays
  funktionieren identisch, nur ohne iRacing.
- **iDash (Replay irsdk)** — zusätzlich `IDASH_REPLAY_MODE=irsdk` (Builder
  rechnen neu).

Wichtig: Der Replay muss über `overlay_manager.py` (GUI) laufen, sonst sieht man
nichts — `replay_server.py` allein liefert nur die Daten, keine Fenster. Direkt
auf der Konsole geht aber `python replay_server.py` für reine Daten-/OBS-Tests.
Replay-ENV (vom Manager via `replay_server.run_from_env()` gelesen):
`IDASH_REPLAY`, `IDASH_REPLAY_MODE`, `IDASH_REPLAY_SPEED`, `IDASH_REPLAY_LOOP`,
`IDASH_REPLAY_FILE`.

Einschränkung: Beim `irsdk`-Replay ist das Session-YAML der Snapshot der
Aufnahme (ändert sich beim Abspielen nicht weiter). Für Overlay- und
Builder-Tests reicht das praktisch immer; echte Session-Wechsel
(Practice→Race) separat aufzeichnen.
