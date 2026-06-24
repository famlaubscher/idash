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

## Pit-Kalibrierung (Circle of Doom)

`app/pit_calibrator.py` ist eine State-Machine, die pro Slow-Tick mit dem
Spieler-Kontext (`SessionTime`, `OnPitRoad`, `Speed`, `FuelLevel`,
`LapDistPct`, Referenzrunde sowie den **deterministischen** Pit-Service-Signalen
`PitstopActive` und `PitSvFlags`) gefüttert wird
(`telemetry_server._handle_calibration`). Sie MISST die Pit-Parameter aus der
Telemetrie (statt sie anzunehmen) und speichert sie pro
**Auto|Strecke|Layout|Serie** in `app/pit_cache.json`. Die gelernten Werte
überschreiben die Defaults für den „Circle of Doom".

Bedienung: Im Overlay-Manager **„Pit-Kalibrierung starten"** → sendet ein
Kommando (`pit_cal_cmd` in `overlay_layout.json`, per Nonce entprellt) und
blendet das Circle-Overlay mit dem Kalibrier-Panel ein. Läuft nur **live**
(echtes iRacing, am besten Practice), nicht im Replay.

**Drei Schritte — beliebige Reihenfolge, je ein Slot:** Jeder Boxenbesuch
(Einfahrt→Ausfahrt) füllt höchstens einen Slot. Sind alle drei gemessen, wird
automatisch gespeichert (`is_complete()`).

| Schritt | Erkennung | Slot |
|---|---|---|
| Boxen-Durchfahrt | Auto stand **nie** (`speed` nie < `_STOP_SPEED`) | `pit_lane_loss_sec` (+ Pit-Marken) |
| Tankstopp | gestanden **und** FuelFill-Bit/Spritanstieg | `fuel_rate_lps` |
| Reifen-Stopp | gestanden **und** Reifen-Bits (`PitSvFlags & 0x0F`), kein Sprit | `tire_change_sec` |

**Deterministisch über die Blackbox:** „Was gemacht wurde" kommt
AUSSCHLIESSLICH aus `PitSvFlags` (Reifen-Bits `0x0F`, FuelFill `0x10`); das
Service-Fenster aus `PitstopActive` (Fallback: Stillstand via `_STOP_SPEED`).
Wichtig: Ohne erkennbare Reifen-Bits wird **NICHT** „4 Reifen" geraten — sonst
würde jeder spritlose Stopp (Meatball, Reparatur, Stehenbleiben, Spawn in der
Box) fälschlich als Reifenwechsel gemessen. Ein Stopp ohne Reifen/Sprit oder
ohne Blackbox-Telemetrie füllt keinen Slot (Status meldet das).

**Reifen 0–4 / Hochrechnung:** Beim Reifen-Stopp wird die Service-Dauer über die
gemeldete Reifenanzahl auf alle 4 normiert: `tire_change_sec = dauer × 4 / anzahl`.
Im Pit-Zeitmodell ergibt sich die Standzeit für n Reifen als
`tire_change_sec × n/4`. Der Server legt die Live-Blackbox-Auswahl
(`tiresSelected`, `fuelSelected`, `fuelAmount` aus `PitSvFuel`) in `pit_config`;
`circle.html` zeigt sie als „Gewählt: n Reifen · X L → Stoppzeit" (bzw.
„Blackbox n/a", wenn die Felder nicht lesbar sind — dient als Diagnose).

**Pit-Marken deterministisch:** Bei jedem Besuch werden die exakten
Boxen-Ein-/Ausfahrt-Positionen (`pit_entry_pct`/`pit_exit_pct`, LapDistPct)
festgehalten, in `pit_cache.json` gespeichert und vom Server in den
Circle-Payload geschrieben — sie ersetzen die All-Cars-Heuristik
(`circle_builder._learn_pit_marks`), die nur noch als Fallback dient.

**Dauer-Übersicht im Circle-Zentrum:** `circle.html` zeigt dauerhaft den
Zeitverlust „Drive-Thru" (= `pitLaneLossSec`) und „Stop voll" (=
`pitLaneLossSec + max(Tankvolumen/Rate, tire_change_sec)`).

**Gate (Arming):** Die Kalibrierung wird erst „scharf" (`_armed=True`), wenn
das Auto auf der Strecke ist (Box mindestens einmal verlassen) UND eine gültige
Referenzrunde (`LapLastLapTime`/`LapBestLapTime` > 0,5 s) vorliegt. Davor wird
nichts gemessen — verhindert, dass ein beim Start bereits laufender Boxenbesuch
(Auto steht schon in der Box) fälschlich als Reifen-/Tankstopp zählt.

**Referenzrunde für den Pit-Verlust:** Pit-Verlust = Transit −
(Streckenanteil × Referenzrunde). Als Referenz dient die **schnellste SAUBERE
(grüne) Runde** des Kalibrierlaufs — der Kalibrator sammelt die offiziellen
Rundenzeiten (`LapLastLapTime`) und markiert jede Runde mit Boxen-Kontakt
(In-/Out-/Durchfahrtsrunde, `_lap_had_pit`) als unsauber. Liegt beim Messen der
Durchfahrt noch keine saubere Runde vor, werden die Rohdaten (`_dt_raw` =
Transit + Streckenanteil) gespeichert und der Verlust **automatisch
nachgerechnet** (`_recompute_pit_loss`), sobald eine — oder eine schnellere —
saubere Runde abgeschlossen wird. `status()` liefert `armed` und `ref_lap` (=
schnellste saubere Runde, stabil); das Circle-Overlay zeigt sie im
Kalibrier-Panel als „Referenz (schnellste saubere Runde)" an.

Relevante Dateien: `app/pit_calibrator.py` (Messung/Persistenz),
`app/telemetry_server.py` (`_handle_calibration`, Kommando-Polling),
`app/overlay_manager.py` (`toggle_pit_calibration`, `_send_pit_cmd`),
`overlays/circle.html` (Kalibrier-Panel).
