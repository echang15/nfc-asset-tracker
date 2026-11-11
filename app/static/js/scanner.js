/* NFC scanning helpers.
 *
 * Two kinds of USB readers are supported:
 *  1. Keyboard-emulation ("HID wedge") readers: they type the tag UID followed
 *     by Enter into whatever field has focus. We keep a scan field focused and
 *     also detect very fast "typing" anywhere on the page.
 *  2. PC/SC readers (ACR122U etc.) via reader_bridge.py, which POSTs reads to
 *     the server. The page polls /api/reader/poll for them.
 */
(function () {
  "use strict";

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function beep(ok) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = beep.ctx || (beep.ctx = new Ctx());
      const play = (freq, start, dur) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = freq;
        osc.type = ok ? "sine" : "square";
        gain.gain.value = 0.08;
        osc.connect(gain).connect(ctx.destination);
        osc.start(ctx.currentTime + start);
        osc.stop(ctx.currentTime + start + dur);
      };
      if (ok) { play(880, 0, 0.09); play(1320, 0.1, 0.12); }
      else { play(220, 0, 0.25); }
    } catch (e) { /* audio is optional */ }
  }

  /* Poll the PC/SC bridge for reads. Calls onUid(uid) for each new read. */
  function startReaderPolling(onUid, opts) {
    opts = opts || {};
    // -1 asks the server for the current cursor only, so stale reads are skipped.
    let cursor = opts.cursor || -1;
    let stopped = false;
    const until = opts.durationMs ? Date.now() + opts.durationMs : Infinity;
    async function tick() {
      if (stopped || Date.now() > until) return;
      try {
        const r = await fetch(`/api/reader/poll?after=${cursor}`, { credentials: "same-origin" });
        if (r.ok) {
          const data = await r.json();
          cursor = data.cursor;
          (data.events || data.uids.map((uid) => ({ uid }))).forEach((e) => onUid(e.uid, e.text));
        } else if (r.status === 401 || r.redirected) {
          stopped = true;
        }
      } catch (e) { /* server restarting, keep trying */ }
      setTimeout(tick, opts.intervalMs || 700);
    }
    tick();
    return () => { stopped = true; };
  }

  /* Detect HID-wedge bursts typed while focus is NOT in a text field. */
  function listenForWedge(onUid) {
    let buf = "";
    let last = 0;
    document.addEventListener("keydown", (ev) => {
      const t = ev.target;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                           t.tagName === "SELECT" || t.isContentEditable);
      if (typing) return;
      const now = performance.now();
      if (now - last > 80) buf = "";
      last = now;
      if (ev.key === "Enter") {
        if (buf.length >= 4) { ev.preventDefault(); onUid(buf); }
        buf = "";
      } else if (ev.key.length === 1) {
        buf += ev.key;
      }
    });
  }

  /* Form fields with data-nfc-field: Enter from the reader must not submit
   * the form, and a "Scan" button can fill the field from the PC/SC bridge. */
  function initNfcFields() {
    document.querySelectorAll("input[data-nfc-field]").forEach((input) => {
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          input.dispatchEvent(new Event("change"));
          input.classList.add("flash");
          setTimeout(() => input.classList.remove("flash"), 600);
        }
      });
    });
    document.querySelectorAll("[data-scan-into]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const input = document.querySelector(btn.dataset.scanInto);
        if (!input) return;
        input.value = "";
        input.focus();
        const label = btn.textContent;
        btn.textContent = "Waiting for tag…";
        btn.disabled = true;
        let stop = null;
        const done = () => { btn.textContent = label; btn.disabled = false; if (stop) stop(); };
        input.addEventListener("change", done, { once: true });
        stop = startReaderPolling((uid, text) => {
          input.value = uid; beep(true); done();
          const out = btn.dataset.scanText && document.querySelector(btn.dataset.scanText);
          if (out) out.textContent = text ? `Programmed text: \u201c${text}\u201d` : "No programmed text on this tag.";
        }, { durationMs: 15000 });
        setTimeout(done, 15000);
      });
    });
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
    if (r.redirected || r.status === 401) { window.location = "/login"; return null; }
    return r.json();
  }

  /* ---- Programming tags ------------------------------------------------
   * programTag(kind, id, onUpdate) creates a write job and resolves with the
   * final job ({status: done|failed|cancelled, message, uid, text}).
   * The returned promise has a .cancel() method. */
  function programTag(kind, id, onUpdate) {
    let jobId = null, cancelled = false, cancelResolve = null;
    const promise = new Promise(async (resolve) => {
      cancelResolve = resolve;
      const job = await postJSON("/admin/tags/program", { kind, id });
      if (!job) return resolve({ status: "failed", message: "Not signed in." });
      jobId = job.id;
      onUpdate && onUpdate(job);
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, 600));
        let cur;
        try {
          const r = await fetch(`/admin/tags/jobs/${jobId}`, { credentials: "same-origin" });
          cur = await r.json();
        } catch (e) { continue; }
        onUpdate && onUpdate(cur);
        if (["done", "failed", "cancelled"].includes(cur.status)) return resolve(cur);
      }
    });
    promise.cancel = async () => {
      cancelled = true;
      if (jobId) await postJSON(`/admin/tags/jobs/${jobId}/cancel`, {});
      cancelResolve && cancelResolve({ status: "cancelled", message: "Cancelled." });
    };
    return promise;
  }

  /* Modal dialog used by [data-program-tag] buttons. */
  function openProgramDialog(kind, id, label) {
    const dlg = document.getElementById("program-dialog");
    const title = dlg.querySelector("[data-title]");
    const msg = dlg.querySelector("[data-msg]");
    const icon = dlg.querySelector("[data-icon]");
    const closeBtn = dlg.querySelector("[data-close]");
    const cancelBtn = dlg.querySelector("[data-cancel]");
    title.textContent = `Program NFC tag for ${label}`;
    icon.textContent = "\uD83D\uDCF6";
    msg.innerHTML = "Preparing\u2026";
    dlg.className = "modal show";
    closeBtn.hidden = true; cancelBtn.hidden = false;
    const p = programTag(kind, id, (job) => {
      if (job.status === "pending") {
        msg.innerHTML = `<strong>Place a blank NFC tag on the reader now.</strong><br>` +
          `It will be written with <code>${escapeHtml(job.text)}</code> and linked automatically.` +
          (job.bridge ? "" : `<div class="flash-msg warning" style="margin-top:.75rem">The reader bridge has not been seen recently. ` +
           `Start the app with <code>--pcsc</code> (the start scripts do this) and make sure the reader is plugged in.</div>`);
      } else if (job.status === "writing") {
        msg.innerHTML = "<strong>Writing\u2026 keep the tag on the reader.</strong>";
      }
    });
    cancelBtn.onclick = () => p.cancel();
    const finish = () => { dlg.className = "modal"; };
    p.then((job) => {
      cancelBtn.hidden = true; closeBtn.hidden = false;
      if (job.status === "done") {
        beep(true); icon.textContent = "\u2705"; dlg.classList.add("ok");
        msg.textContent = job.message + " You can remove the tag.";
        closeBtn.onclick = () => window.location.reload();
      } else if (job.status === "cancelled") {
        finish();
      } else {
        beep(false); icon.textContent = "\u26A0\uFE0F"; dlg.classList.add("err");
        msg.textContent = job.message || "Programming failed.";
        closeBtn.onclick = finish;
      }
    });
  }

  function initProgramButtons() {
    document.querySelectorAll("[data-program-tag]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        openProgramDialog(btn.dataset.programTag, btn.dataset.id, btn.dataset.label || "");
      });
    });
  }

  window.NFC = { beep, startReaderPolling, listenForWedge, postJSON, csrf, programTag, escapeHtml };
  document.addEventListener("DOMContentLoaded", () => { initNfcFields(); initProgramButtons(); });
})();
