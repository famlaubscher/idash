/**
 * overlay-persistence.js
 *
 * Speichert den Scale-Faktor (Zoom) eines Overlays in localStorage und
 * stellt ihn beim nächsten Öffnen wieder her.
 *
 * WICHTIG: Fensterposition und -größe werden NICHT hier verwaltet.
 * Die Overlays laufen als PyQt-QWebEngineView, dort sind window.moveTo()
 * und window.resizeTo() wirkungslos. Position/Größe werden ausschließlich
 * vom Overlay-Manager (overlay_manager.py → overlay_layout.json) verwaltet.
 *
 * Verwendung in jedem Overlay:
 *
 *   const persist = createOverlayPersistence('strategy');
 *
 *   // 1) Beim Start: gespeicherten Scale-Wert lesen und anwenden
 *   const savedScale = persist.restore();
 *   if (savedScale !== null) {
 *       myScale = savedScale;
 *       wrapper.style.transform = `scale(${myScale})`;
 *   }
 *
 *   // 2) Während Drag: aktuellen Scale merken (ohne sofort zu schreiben)
 *   persist.setScale(myScale);
 *
 *   // 3) Nach Drag-Ende: sofort speichern
 *   persist.saveScale(myScale);
 *
 *   // Der Scale wird außerdem automatisch bei beforeunload gespeichert.
 */
function createOverlayPersistence(overlayName) {
    const KEY = `overlay-scale-${overlayName}`;
    let _scale = 1;

    function save() {
        try {
            localStorage.setItem(KEY, JSON.stringify({ scale: _scale }));
        } catch (_) { /* localStorage nicht verfügbar */ }
    }

    function restore() {
        try {
            const raw = localStorage.getItem(KEY);
            if (!raw) return null;
            const s = JSON.parse(raw);
            if (Number.isFinite(s.scale) && s.scale > 0) {
                _scale = s.scale;
                return s.scale;
            }
        } catch (_) { /* JSON kaputt oder kein Eintrag */ }
        return null;
    }

    // Beim Schließen / Neuladen automatisch speichern
    window.addEventListener('beforeunload', save);

    return {
        /** Liest gespeicherten Scale. Gibt Scale zurück, oder null wenn nichts gespeichert. */
        restore,

        /** Aktuellen Scale merken OHNE sofort zu schreiben (während Drag). */
        setScale(scale) { _scale = scale; },

        /** Scale merken UND sofort in localStorage schreiben (nach Drag-Ende). */
        saveScale(scale) { _scale = scale; save(); },
    };
}

/**
 * Wendet card_bg_alpha aus dem WebSocket-Payload an.
 * Einmalig beim ersten Frame mit gültigem Wert, danach bei jeder Änderung.
 */
(function () {
    let _lastAlpha = null;
    window.__applyCardBgAlpha = function (alpha) {
        if (typeof alpha !== "number" || !isFinite(alpha)) return;
        alpha = Math.max(0, Math.min(1, alpha));
        if (alpha === _lastAlpha) return;
        _lastAlpha = alpha;
        document.documentElement.style.setProperty("--card-bg-alpha", alpha.toFixed(3));
    };
})();
