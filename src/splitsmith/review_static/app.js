// Audit-only review SPA: load fixture + audio (+ optional video), show waveform
// with markers, let the user toggle keep / drag time / add markers, save back
// to the fixture JSON. Vanilla JS, no build step.

(() => {
    "use strict";

    const ZOOM_DEFAULT = 50;          // wavesurfer minPxPerSec at "fit" zoom
    const ZOOM_IN_FACTOR = 2.5;
    const ZOOM_OUT_FACTOR = 0.4;
    const SYNC_DRIFT_THRESHOLD_S = 0.05;

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
        waveform: document.getElementById("waveform"),
        markersOverlay: document.getElementById("markers-overlay"),
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
            fixture.stage_window_end_in_fixture ??
            (beepTime + (fixture.stage_time_seconds || 0));

        await setupWaveform();
        bindKeyboard();
        bindPlay();
        bindSave();
        bindWaveformDoubleClick();
        renderMarkers();
        renderList();
    }

    function renderHeader() {
        const stageNum = fixture.stage_number ?? "?";
        const stageName = fixture.stage_name ?? "(unnamed stage)";
        els.title.textContent = `Stage ${stageNum} -- ${stageName}`;
        const candCount =
            fixture._candidates_pending_audit?.candidates?.length ?? 0;
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
        // Existing audited shots set keep=true on those candidate_numbers.
        const auditedNumbers = new Set(
            (fix.shots || [])
                .filter((s) => s.source !== "manual" && s.candidate_number != null)
                .map((s) => s.candidate_number)
        );
        const detected = (fix._candidates_pending_audit?.candidates || []).map((c) => ({
            id: `cand-${c.candidate_number}`,
            source: "detected",
            candidate_number: c.candidate_number,
            time: c.time,
            peak: c.peak_amplitude ?? null,
            confidence: c.confidence ?? null,
            keep: auditedNumbers.has(c.candidate_number),
        }));
        const manual = (fix.shots || [])
            .filter((s) => s.source === "manual")
            .map((s, i) => ({
                id: `manual-${++manualCounter}`,
                source: "manual",
                candidate_number: null,
                time: s.time,
                peak: null,
                confidence: null,
                keep: true,
            }));
        return [...detected, ...manual].sort((a, b) => a.time - b.time);
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
        });

        await new Promise((resolve, reject) => {
            wavesurfer.on("ready", resolve);
            wavesurfer.on("error", reject);
        });

        wavesurfer.on("audioprocess", onAudioProcess);
        wavesurfer.on("timeupdate", onAudioProcess);
        wavesurfer.on("seeking", onSeek);
        wavesurfer.on("interaction", onSeek);
        wavesurfer.on("zoom", () => {
            renderMarkers();
            updateZoomDisplay();
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
        updateZoomDisplay();

        // Re-render markers on horizontal scroll (overlay must follow).
        els.waveform.parentElement.addEventListener("scroll", renderMarkers);
    }

    function onAudioProcess() {
        const t = wavesurfer.getCurrentTime();
        const dur = wavesurfer.getDuration();
        els.time.textContent = `${t.toFixed(3)} / ${dur.toFixed(3)}s`;
        if (video) {
            const target = t + videoOffset;
            if (Math.abs(video.currentTime - target) > SYNC_DRIFT_THRESHOLD_S) {
                video.currentTime = target;
            }
        }
    }

    function onSeek() {
        const t = wavesurfer.getCurrentTime();
        if (video) video.currentTime = t + videoOffset;
    }

    function updateZoomDisplay() {
        const px = wavesurfer.options.minPxPerSec || ZOOM_DEFAULT;
        const ratio = px / ZOOM_DEFAULT;
        els.zoomDisplay.textContent = `zoom ${ratio.toFixed(1)}x`;
    }

    function setZoom(minPxPerSec) {
        wavesurfer.zoom(minPxPerSec);
        renderMarkers();
        updateZoomDisplay();
    }

    function bindWaveformDoubleClick() {
        els.waveform.addEventListener("dblclick", (e) => {
            // Double-click on the waveform (not on a marker) adds a manual shot.
            if (e.target.closest(".marker")) return;
            const rect = els.waveform.getBoundingClientRect();
            const dur = wavesurfer.getDuration();
            const scrollLeft = els.waveform.parentElement.scrollLeft || 0;
            const x = e.clientX - rect.left + scrollLeft;
            const totalWidth = els.waveform.scrollWidth || rect.width;
            const t = (x / totalWidth) * dur;
            addManualMarker(t);
        });
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
        markDirty();
        renderMarkers();
        renderList();
    }

    // ----- Marker rendering + interaction -----

    function renderMarkers() {
        const dur = wavesurfer.getDuration();
        if (!dur) return;
        const totalWidth = els.waveform.scrollWidth;
        els.markersOverlay.innerHTML = "";
        els.markersOverlay.style.width = totalWidth + "px";

        // Beep marker (always present, not editable)
        const beepEl = renderMarkerEl({
            id: "beep",
            time: beepTime,
            classes: ["marker", "beep"],
            label: "BEEP",
        }, totalWidth, dur);
        els.markersOverlay.appendChild(beepEl);

        for (const m of markers) {
            const cls = ["marker", m.keep ? "keep" : "reject"];
            if (m.source === "manual") cls.push("manual");
            const label = m.source === "manual"
                ? "+"
                : `${m.candidate_number}`;
            const el = renderMarkerEl({
                id: m.id,
                time: m.time,
                classes: cls,
                label,
                tooltip: tooltipFor(m),
            }, totalWidth, dur);
            attachMarkerHandlers(el, m, dur, totalWidth);
            if (m.id === activeMarkerId) el.querySelector(".marker-pin").style.outline =
                "2px solid #ffffff";
            els.markersOverlay.appendChild(el);
        }
    }

    function renderMarkerEl({ id, time, classes, label, tooltip }, totalWidth, dur) {
        const el = document.createElement("div");
        el.className = classes.join(" ");
        el.dataset.id = id;
        el.style.left = (time / dur) * totalWidth + "px";
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

    function attachMarkerHandlers(el, m, dur, totalWidth) {
        let dragState = null;
        const pin = el.querySelector(".marker-pin");

        pin.addEventListener("mousedown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dragState = {
                startX: e.clientX,
                startLeft: parseFloat(el.style.left),
                moved: false,
            };
            const onMove = (ev) => {
                const dx = ev.clientX - dragState.startX;
                if (Math.abs(dx) > 2) dragState.moved = true;
                const newLeft = Math.max(0, Math.min(totalWidth, dragState.startLeft + dx));
                el.style.left = newLeft + "px";
            };
            const onUp = (ev) => {
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
                if (dragState.moved) {
                    const newLeft = parseFloat(el.style.left);
                    m.time = (newLeft / totalWidth) * dur;
                    markers.sort((a, b) => a.time - b.time);
                    markDirty();
                    renderMarkers();
                    renderList();
                } else {
                    // Click without drag = toggle keep.
                    m.keep = !m.keep;
                    markDirty();
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
            if (m.source === "manual") {
                markers = markers.filter((x) => x.id !== m.id);
            } else {
                m.keep = false;
            }
            markDirty();
            renderMarkers();
            renderList();
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

    function markDirty() {
        dirty = true;
        els.save.classList.add("dirty");
        els.save.textContent = "Save * (Cmd+S)";
        els.saveStatus.textContent = "unsaved changes";
    }

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
        dirty = false;
        els.save.classList.remove("dirty");
        els.save.textContent = "Save (Cmd+S)";
        els.saveStatus.textContent =
            `saved ${kept.length} shots at ${new Date().toLocaleTimeString()}`;
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
            } else if (e.key === "M" || (e.key === "m" && e.shiftKey)) {
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
