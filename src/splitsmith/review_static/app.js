// Audit-only review SPA: load fixture + audio (+ optional video), show waveform
// with markers, let the user toggle keep / drag time / add markers, save back
// to the fixture JSON. Vanilla JS, no build step.

(() => {
    "use strict";

    const ZOOM_DEFAULT = 50; // wavesurfer minPxPerSec at "fit" zoom
    const ZOOM_IN_FACTOR = 2.5;
    const ZOOM_OUT_FACTOR = 0.4;
    const SYNC_DRIFT_THRESHOLD_S = 0.05;
    const UNDO_LIMIT = 100;

    // ----- State -----
    /** @type {{
     *   id: string,
     *   source: "detected"|"manual",
     *   candidate_number: number|null,
     *   time: number,
     *   peak: number|null,
     *   confidence: number|null,
     *   keep: boolean,
     * }[]} */
    let markers = [];
    let beepTime = 0;
    let stageWindowEnd = 0;
    let fixture = null;
    let meta = null;
    let wavesurfer = null;
    let video = null;
    let videoOffset = 0;
    let dirty = false;
    let manualCounter = 0;
    let activeMarkerId = null;
    /** @type {Array<typeof markers>} */
    const undoStack = [];
    /** Snapshot of markers at last save (or boot) -- compared to current to compute dirty. */
    let savedSignature = "";
    let totalContentWidth = 0; // duration * minPxPerSec; updated on ready/zoom

    // ----- DOM refs -----
    const els = {
        title: document.getElementById("stage-title"),
        meta: document.getElementById("stage-meta"),
        save: document.getElementById("save-btn"),
        saveStatus: document.getElementById("save-status"),
        videoSection: document.getElementById("video-section"),
        video: document.getElementById("video"),
        noVideo: document.getElementById("no-video-msg"),
        play: document.getElementById("play-btn"),
        time: document.getElementById("time-display"),
        zoomDisplay: document.getElementById("zoom-display"),
        dragInfo: document.getElementById("drag-info"),
        waveform: document.getElementById("waveform"),
        markersOverlay: document.getElementById("markers-overlay"),
        timeRuler: document.getElementById("time-ruler"),
        listBody: document.querySelector("#marker-list tbody"),
    };

    // ----- Boot -----
    init().catch((err) => {
        console.error(err);
        els.title.textContent = "Failed to load: " + err.message;
    });

    async function init() {
        const [metaResp, fixtureResp] = await Promise.all([
            fetch("/api/meta"),
            fetch("/api/fixture"),
        ]);
        meta = await metaResp.json();
        fixture = await fixtureResp.json();
        videoOffset = meta.video_offset_seconds || 0;

        renderHeader();
        setupVideo();
        markers = buildInitialMarkers(fixture);
        beepTime = fixture.beep_time;
        stageWindowEnd =
            fixture.stage_window_end_in_fixture ?? beepTime + (fixture.stage_time_seconds || 0);

        await setupWaveform();
        bindKeyboard();
        bindPlay();
        bindSave();
        savedSignature = markersSignature();
        renderMarkers();
        renderList();
    }

    function markersSignature() {
        // Stable serialization of marker state (ignoring transient ID assignments)
        // for dirty comparison.
        return JSON.stringify(
            markers.map((m) => ({
                src: m.source,
                cn: m.candidate_number,
                t: Math.round(m.time * 10000) / 10000,
                k: !!m.keep,
            }))
        );
    }

    function recomputeDirty() {
        dirty = markersSignature() !== savedSignature;
        els.save.classList.toggle("dirty", dirty);
        els.save.textContent = dirty ? "Save * (Cmd+S)" : "Save (Cmd+S)";
        els.saveStatus.textContent = dirty ? "unsaved changes" : "";
    }

    function renderHeader() {
        const stageNum = fixture.stage_number ?? "?";
        const stageName = fixture.stage_name ?? "(unnamed stage)";
        els.title.textContent = `Stage ${stageNum} -- ${stageName}`;
        const candCount = fixture._candidates_pending_audit?.candidates?.length ?? 0;
        els.meta.textContent =
            `${candCount} candidates  |  beep ${fixture.beep_time?.toFixed(3)}s  |  ` +
            `stage_time ${fixture.stage_time_seconds}s  |  ${meta.fixture_path}`;
    }

    function setupVideo() {
        if (!meta.has_video) {
            els.video.classList.add("hidden");
            els.noVideo.classList.remove("hidden");
            video = null;
            return;
        }
        video = els.video;
        video.src = "/api/video";
        video.muted = true;
        video.preload = "auto";
    }

    function buildInitialMarkers(fix) {
        // Existing audited shots set keep=true on those candidate_numbers and
        // override the detector time with the user's prior drag (if any).
        const auditedByNum = new Map();
        for (const s of fix.shots || []) {
            if (s.source !== "manual" && s.candidate_number != null) {
                auditedByNum.set(s.candidate_number, s);
            }
        }
        const detected = (fix._candidates_pending_audit?.candidates || []).map((c) => {
            const audited = auditedByNum.get(c.candidate_number);
            return {
                id: `cand-${c.candidate_number}`,
                source: "detected",
                candidate_number: c.candidate_number,
                time: audited ? audited.time : c.time,
                original_time: c.time, // for double-click reset
                peak: c.peak_amplitude ?? null,
                confidence: c.confidence ?? null,
                keep: audited != null,
            };
        });
        const manual = (fix.shots || [])
            .filter((s) => s.source === "manual")
            .map((s) => ({
                id: `manual-${++manualCounter}`,
                source: "manual",
                candidate_number: null,
                time: s.time,
                original_time: s.time, // manual markers' "original" is where they were created
                peak: null,
                confidence: null,
                keep: true,
            }));
        return [...detected, ...manual].sort((a, b) => a.time - b.time);
    }

    // ----- Undo -----

    function snapshot() {
        // Deep clone of `markers`. Cheap (a few hundred small objects).
        return markers.map((m) => ({ ...m }));
    }

    function pushUndo() {
        undoStack.push(snapshot());
        if (undoStack.length > UNDO_LIMIT) undoStack.shift();
    }

    function undo() {
        if (!undoStack.length) {
            els.saveStatus.textContent = "nothing to undo";
            return;
        }
        markers = undoStack.pop();
        // Reset activeMarker if it no longer exists.
        if (activeMarkerId && !markers.some((m) => m.id === activeMarkerId)) {
            activeMarkerId = null;
        }
        recomputeDirty();
        renderMarkers();
        renderList();
    }

    // ----- Waveform -----

    async function setupWaveform() {
        wavesurfer = WaveSurfer.create({
            container: els.waveform,
            url: "/api/audio",
            waveColor: "#48484a",
            progressColor: "#0a84ff",
            cursorColor: "#ffd60a",
            cursorWidth: 1,
            height: 140,
            barWidth: 1,
            barGap: 1,
            barRadius: 0,
            normalize: true,
            minPxPerSec: ZOOM_DEFAULT,
            autoCenter: false,
        });

        await new Promise((resolve, reject) => {
            wavesurfer.on("ready", resolve);
            wavesurfer.on("error", reject);
        });

        // The overlay stays as a sibling of #waveform inside #waveform-section
        // (overflow:hidden). We mirror wavesurfer's internal scroll position via
        // its 'scroll' event, and resize the overlay to the zoomed content
        // width on 'zoom'/'ready'. This is more robust than poking around
        // wavesurfer's DOM.
        els.waveform.addEventListener("dblclick", onWaveformDoubleClick);

        wavesurfer.on("audioprocess", onAudioProcess);
        wavesurfer.on("timeupdate", onAudioProcess);
        wavesurfer.on("seeking", onSeek);
        wavesurfer.on("interaction", onSeek);
        wavesurfer.on("zoom", () => {
            updateContentWidth();
            renderMarkers();
            updateZoomDisplay();
        });
        wavesurfer.on("scroll", (_visStart, _visEnd, scrollLeft) => {
            const t = `translateX(${-scrollLeft}px)`;
            els.markersOverlay.style.transform = t;
            els.timeRuler.style.transform = t;
        });
        wavesurfer.on("redraw", () => {
            // Wavesurfer re-renders on window resize when fillParent is on; keep
            // our overlay width and marker positions in sync.
            updateContentWidth();
            renderMarkers();
        });
        window.addEventListener("resize", () => {
            updateContentWidth();
            renderMarkers();
        });
        wavesurfer.on("play", () => {
            if (video) video.play().catch(() => {});
        });
        wavesurfer.on("pause", () => {
            if (video) video.pause();
        });
        wavesurfer.on("finish", () => {
            if (video) video.pause();
        });
        updateContentWidth();
        updateZoomDisplay();
        startSyncLoop();
    }

    function updateContentWidth() {
        // wavesurfer's `fillParent` stretches the canvas to the container at
        // low zoom, so `duration * minPxPerSec` underestimates the rendered
        // width. Read the wrapper's actual offsetWidth instead.
        const wrapper = wavesurfer.getWrapper();
        const wrapperWidth = wrapper && wrapper.offsetWidth ? wrapper.offsetWidth : 0;
        const dur = wavesurfer.getDuration();
        const px = wavesurfer.options.minPxPerSec || ZOOM_DEFAULT;
        totalContentWidth = Math.max(wrapperWidth, dur * px);
        els.markersOverlay.style.width = totalContentWidth + "px";
        els.timeRuler.style.width = totalContentWidth + "px";
        renderTimeRuler();
    }

    // Pick a tick interval that gives ~80 px between major ticks at the
    // current zoom. Returns { major, minor } in seconds.
    function chooseTickInterval() {
        const dur = wavesurfer.getDuration() || 1;
        const pxPerSec = totalContentWidth / dur;
        const candidates = [
            { major: 0.05, minor: 0.01 },
            { major: 0.1, minor: 0.025 },
            { major: 0.25, minor: 0.05 },
            { major: 0.5, minor: 0.1 },
            { major: 1, minor: 0.25 },
            { major: 2, minor: 0.5 },
            { major: 5, minor: 1 },
            { major: 10, minor: 2 },
            { major: 30, minor: 5 },
            { major: 60, minor: 10 },
        ];
        for (const c of candidates) {
            if (c.major * pxPerSec >= 80) return c;
        }
        return candidates[candidates.length - 1];
    }

    function formatTime(t) {
        // seconds with 3 decimals; no leading minutes for short fixtures
        if (t < 60) return `${t.toFixed(3)}s`;
        const m = Math.floor(t / 60);
        const s = t - m * 60;
        return `${m}:${s.toFixed(3).padStart(6, "0")}`;
    }

    function renderTimeRuler() {
        const dur = wavesurfer.getDuration();
        if (!dur || !totalContentWidth) return;
        const { major, minor } = chooseTickInterval();
        els.timeRuler.innerHTML = "";
        for (let t = 0; t <= dur + 1e-6; t += minor) {
            const tick = document.createElement("div");
            const isMajor = Math.abs(Math.round(t / major) * major - t) < minor / 2;
            tick.className = `time-tick${isMajor ? " major" : ""}`;
            tick.style.left = (t / dur) * totalContentWidth + "px";
            if (isMajor) tick.textContent = formatTime(t);
            els.timeRuler.appendChild(tick);
        }
    }

    function getScrollLeft() {
        const m = /translateX\((-?\d+(?:\.\d+)?)px\)/.exec(
            els.markersOverlay.style.transform || ""
        );
        return m ? -parseFloat(m[1]) : 0;
    }

    function onWaveformDoubleClick(e) {
        if (e.target.closest(".marker")) return;
        const rect = els.waveform.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const dur = wavesurfer.getDuration();
        const t = ((x + getScrollLeft()) / totalContentWidth) * dur;
        if (t >= 0 && t <= dur) {
            pushUndo();
            addManualMarker(t);
        }
    }

    function onAudioProcess() {
        const t = wavesurfer.getCurrentTime();
        const dur = wavesurfer.getDuration();
        els.time.textContent = `${t.toFixed(3)} / ${dur.toFixed(3)}s`;
        // Video sync runs in syncLoop() via requestAnimationFrame for live drag
        // tracking; this callback only updates the readout.
    }

    function onSeek() {
        // Kept for cache-priming the time display on a hard seek; the RAF loop
        // handles actual video sync.
        const t = wavesurfer.getCurrentTime();
        if (video && Math.abs(video.currentTime - (t + videoOffset)) > SYNC_DRIFT_THRESHOLD_S) {
            video.currentTime = t + videoOffset;
        }
    }

    function startSyncLoop() {
        // Continuously mirror wavesurfer's currentTime into the video element.
        // wavesurfer fires only one 'seeking' event when the user drags the
        // playhead, so without this loop the video lags behind drag scrubs.
        function tick() {
            if (video && wavesurfer) {
                const target = wavesurfer.getCurrentTime() + videoOffset;
                if (Math.abs(video.currentTime - target) > SYNC_DRIFT_THRESHOLD_S) {
                    video.currentTime = target;
                }
            }
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    function updateZoomDisplay() {
        const px = wavesurfer.options.minPxPerSec || ZOOM_DEFAULT;
        const ratio = px / ZOOM_DEFAULT;
        els.zoomDisplay.textContent = `zoom ${ratio.toFixed(1)}x`;
    }

    function setZoom(minPxPerSec) {
        wavesurfer.zoom(minPxPerSec);
        updateZoomDisplay();
    }

    function addManualMarker(time) {
        markers.push({
            id: `manual-${++manualCounter}`,
            source: "manual",
            candidate_number: null,
            time,
            peak: null,
            confidence: null,
            keep: true,
        });
        markers.sort((a, b) => a.time - b.time);
        recomputeDirty();
        renderMarkers();
        renderList();
    }

    // ----- Marker rendering + interaction -----

    function renderMarkers() {
        const dur = wavesurfer.getDuration();
        if (!dur) return;
        els.markersOverlay.innerHTML = "";

        // Beep marker (always present, not editable).
        els.markersOverlay.appendChild(
            renderMarkerEl({
                time: beepTime,
                classes: ["marker", "beep"],
                label: "BEEP",
                duration: dur,
            })
        );

        for (const m of markers) {
            const cls = ["marker", m.keep ? "keep" : "reject"];
            if (m.source === "manual") cls.push("manual");
            const label = m.source === "manual" ? "+" : `${m.candidate_number}`;
            const el = renderMarkerEl({
                id: m.id,
                time: m.time,
                classes: cls,
                label,
                tooltip: tooltipFor(m),
                duration: dur,
            });
            attachMarkerHandlers(el, m, dur);
            if (m.id === activeMarkerId) {
                el.querySelector(".marker-pin").style.outline = "2px solid #ffffff";
            }
            els.markersOverlay.appendChild(el);
        }
    }

    function renderMarkerEl({ id, time, classes, label, tooltip, duration }) {
        const el = document.createElement("div");
        el.className = classes.join(" ");
        if (id) el.dataset.id = id;
        // Pixel-based positioning relative to the overlay (whose width matches
        // wavesurfer's zoomed content width).
        el.style.left = `${(time / duration) * totalContentWidth}px`;
        const pin = document.createElement("div");
        pin.className = "marker-pin";
        if (tooltip) pin.title = tooltip;
        el.appendChild(pin);
        if (label) {
            const lab = document.createElement("div");
            lab.className = "marker-label";
            lab.textContent = label;
            el.appendChild(lab);
        }
        return el;
    }

    function tooltipFor(m) {
        const parts = [`t=${m.time.toFixed(4)}s`];
        if (m.peak != null) parts.push(`peak=${m.peak.toFixed(3)}`);
        if (m.confidence != null) parts.push(`conf=${m.confidence.toFixed(3)}`);
        parts.push(m.keep ? "KEEP" : "reject");
        return parts.join("  ");
    }

    function attachMarkerHandlers(el, m, dur) {
        let dragState = null;
        const pin = el.querySelector(".marker-pin");

        pin.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const undoSnapshot = snapshot();
            dragState = {
                startX: e.clientX,
                startPxLeft: parseFloat(el.style.left) || 0,
                startTime: m.time,
                moved: false,
                undoSnapshot,
            };
            const onMove = (ev) => {
                const dx = ev.clientX - dragState.startX;
                if (Math.abs(dx) > 2) dragState.moved = true;
                const newLeft = Math.max(
                    0,
                    Math.min(totalContentWidth, dragState.startPxLeft + dx)
                );
                el.style.left = `${newLeft}px`;
                if (dragState.moved) {
                    const dragTime = (newLeft / totalContentWidth) * dur;
                    const deltaMs = (dragTime - dragState.startTime) * 1000;
                    els.dragInfo.textContent =
                        `t=${dragTime.toFixed(4)}s  Δ ${deltaMs >= 0 ? "+" : ""}${deltaMs.toFixed(1)}ms`;
                }
            };
            const onUp = () => {
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
                els.dragInfo.textContent = "";
                if (dragState.moved) {
                    undoStack.push(dragState.undoSnapshot);
                    if (undoStack.length > UNDO_LIMIT) undoStack.shift();
                    const newLeft = parseFloat(el.style.left) || 0;
                    m.time = (newLeft / totalContentWidth) * dur;
                    markers.sort((a, b) => a.time - b.time);
                    recomputeDirty();
                    renderMarkers();
                    renderList();
                } else {
                    // Click without drag = toggle keep.
                    pushUndo();
                    m.keep = !m.keep;
                    recomputeDirty();
                    renderMarkers();
                    renderList();
                }
                dragState = null;
            };
            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
        });

        pin.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            // Right-click deletes a manual marker; for detected ones, just rejects.
            pushUndo();
            if (m.source === "manual") {
                markers = markers.filter((x) => x.id !== m.id);
            } else {
                m.keep = false;
            }
            recomputeDirty();
            renderMarkers();
            renderList();
        });

        pin.addEventListener("dblclick", (e) => {
            // Double-click resets the marker to its original (algorithmic) time.
            // For manual markers, original_time == creation time, so this is a no-op.
            e.preventDefault();
            e.stopPropagation();
            if (m.original_time != null && m.time !== m.original_time) {
                pushUndo();
                m.time = m.original_time;
                markers.sort((a, b) => a.time - b.time);
                recomputeDirty();
                renderMarkers();
                renderList();
            }
        });
    }

    // ----- Marker list -----

    function renderList() {
        els.listBody.innerHTML = "";
        let kept = 0;
        for (let i = 0; i < markers.length; i++) {
            const m = markers[i];
            if (m.keep) kept++;
            const tr = document.createElement("tr");
            tr.dataset.id = m.id;
            tr.classList.toggle("keep", m.keep);
            tr.classList.toggle("reject", !m.keep);
            if (m.source === "manual") tr.classList.add("manual");
            if (m.id === activeMarkerId) tr.classList.add("active");
            const split = i === 0 ? m.time - beepTime : m.time - markers[i - 1].time;
            tr.innerHTML = `
                <td>${m.keep ? kept : "-"}</td>
                <td>${m.time.toFixed(4)}</td>
                <td>${split.toFixed(3)}</td>
                <td>${m.peak != null ? m.peak.toFixed(3) : "-"}</td>
                <td>${m.confidence != null ? m.confidence.toFixed(3) : "-"}</td>
                <td>${m.source}</td>
                <td>${m.keep ? "[KEEP]" : "[reject]"}</td>
            `;
            tr.addEventListener("click", () => {
                wavesurfer.setTime(m.time);
                activeMarkerId = m.id;
                renderList();
                renderMarkers();
            });
            els.listBody.appendChild(tr);
        }
    }

    // ----- Save -----

    function bindSave() {
        els.save.addEventListener("click", save);
    }

    async function save() {
        const kept = markers
            .filter((m) => m.keep)
            .map((m, i) => ({
                shot_number: i + 1,
                candidate_number: m.candidate_number,
                time: round(m.time, 4),
                ms_after_beep: round((m.time - beepTime) * 1000, 0),
                source: m.source,
            }));
        const updated = { ...fixture, shots: kept };

        els.saveStatus.textContent = "saving...";
        const resp = await fetch("/api/fixture", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(updated, null, 2),
        });
        if (!resp.ok) {
            els.saveStatus.textContent = "save FAILED: " + (await resp.text());
            return;
        }
        fixture = updated;
        savedSignature = markersSignature();
        recomputeDirty();
        els.saveStatus.textContent = `saved ${kept.length} shots at ${new Date().toLocaleTimeString()}`;
    }

    function round(x, decimals) {
        const f = Math.pow(10, decimals);
        return Math.round(x * f) / f;
    }

    // ----- Transport / keyboard -----

    function bindPlay() {
        els.play.addEventListener("click", () => {
            if (wavesurfer.isPlaying()) wavesurfer.pause();
            else wavesurfer.play();
        });
    }

    function bindKeyboard() {
        document.addEventListener("keydown", (e) => {
            const isMod = e.metaKey || e.ctrlKey;
            // Don't intercept keys while typing in form fields.
            if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

            if (e.code === "Space") {
                e.preventDefault();
                if (wavesurfer.isPlaying()) wavesurfer.pause();
                else wavesurfer.play();
            } else if (isMod && e.key === "1") {
                e.preventDefault();
                setZoom((wavesurfer.options.minPxPerSec || ZOOM_DEFAULT) * ZOOM_IN_FACTOR);
            } else if (isMod && e.key === "2") {
                e.preventDefault();
                setZoom(ZOOM_DEFAULT);
            } else if (isMod && e.key === "3") {
                e.preventDefault();
                setZoom((wavesurfer.options.minPxPerSec || ZOOM_DEFAULT) * ZOOM_OUT_FACTOR);
            } else if (isMod && e.key.toLowerCase() === "s") {
                e.preventDefault();
                save();
            } else if (isMod && e.key.toLowerCase() === "z") {
                // Cmd+Z / Ctrl+Z = undo. Shift+Cmd+Z is conventionally redo,
                // not implemented yet -- we just no-op on it.
                e.preventDefault();
                if (!e.shiftKey) undo();
            } else if ((e.key === "M" && !isMod) || (e.key === "m" && e.shiftKey && !isMod)) {
                e.preventDefault();
                jumpMarker(-1);
            } else if (e.key === "m" && !e.shiftKey && !isMod) {
                e.preventDefault();
                jumpMarker(1);
            }
        });
    }

    function jumpMarker(direction) {
        const t = wavesurfer.getCurrentTime();
        let target = null;
        if (direction > 0) {
            target = markers.find((m) => m.time > t + 0.001);
        } else {
            for (let i = markers.length - 1; i >= 0; i--) {
                if (markers[i].time < t - 0.001) {
                    target = markers[i];
                    break;
                }
            }
        }
        if (target) {
            wavesurfer.setTime(target.time);
            activeMarkerId = target.id;
            renderList();
            renderMarkers();
        }
    }

    // Warn on close if dirty.
    window.addEventListener("beforeunload", (e) => {
        if (dirty) {
            e.preventDefault();
            e.returnValue = "";
        }
    });
})();
