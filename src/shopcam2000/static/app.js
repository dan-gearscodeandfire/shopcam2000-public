/* SHOPCAM 2000 CONTROLLER — client
 *
 * The page owns no truth. It renders whatever the server pushes over SSE and
 * sends intents back. The only number it invents is the sub-second tick between
 * polls, and even that is re-synchronised to Blue Iris on every update.
 */
'use strict';

const $ = (sel) => document.querySelector(sel);

const el = {
  biStatus: $('#bi-status'),
  cameras: $('#cameras'),
  camerasEmpty: $('#cameras-empty'),
  armSummary: $('#arm-summary'),
  armAll: $('#btn-arm-all'),
  armNone: $('#btn-arm-none'),
  record: $('#btn-record'),
  recordFill: $('.record-fill'),
  recordLabel: $('.record-label'),
  recordHint: $('.record-hint'),
  twab: $('#btn-twab'),
  twabHint: $('#twab-hint'),
  timer: $('#timer'),
  status: $('#transport-status'),
  alert: $('#alert'),
  clipsSection: $('#clips-section'),
  clips: $('#clips'),
  disk: $('#disk'),
  diskArc: $('#disk-arc'),
  diskText: $('#disk-text'),
  bridgesSection: $('#bridges-section'),
  bridgeSummary: $('#bridge-summary'),
  bridgesOn: $('#btn-bridges-on'),
  bridgesOff: $('#btn-bridges-off'),
  settings: $('#settings'),
  settingsBtn: $('#btn-settings'),
  settingsClose: $('#btn-settings-close'),
  fullscreen: $('#btn-fullscreen'),
  about: $('#about'),
  setTheme: $('#set-theme'),
  setScale: $('#set-scale'),
  setAwake: $('#set-awake'),
  setAudio: $('#set-audio'),
  setHide: $('#set-hide'),
  setThumb: $('#set-thumb'),
  setHold: $('#set-hold'),
};

let snapshot = null;
let busy = false;

/* ---- timer -------------------------------------------------------------- *
 * A local stopwatch keeps the display smooth between polls, but every server
 * update overwrites its origin with Blue Iris's own ManRecElapsed. Refresh the
 * page mid-take, or open it on a second device, and the timer is still right,
 * because the number was never really ours.
 */
const clock = { origin: 0, live: false };

function syncClock(take) {
  clock.live = !!take.active;
  clock.origin = clock.live ? performance.now() - (take.elapsedMs || 0) : 0;
}

function formatDuration(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function tick() {
  if (clock.live) {
    el.timer.textContent = formatDuration(performance.now() - clock.origin);
    el.timer.classList.add('is-live');
  } else {
    el.timer.classList.remove('is-live');
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

/* ---- rendering ---------------------------------------------------------- */

function render(state) {
  snapshot = state;
  document.documentElement.dataset.theme = state.settings.theme || 'cogitator';

  renderBlueIris(state);
  renderCameras(state);
  renderBridges(state);
  renderTransport(state);
  renderTwab(state);
  renderTwabResult(state);   // after renderTwab: a receipt outranks the idle repaint
  renderTwabVerify(state);   // after the receipt: a decode failure outranks a receipt
  renderClips(state);
  renderDisk(state);
  renderSettings(state);
}

function renderBlueIris(state) {
  const { online, error, version, host } = state.bi;
  el.biStatus.className = 'pill ' + (online ? 'pill--online' : 'pill--offline');
  el.biStatus.querySelector('.pill-text').textContent = online ? 'BI ONLINE' : 'BI OFFLINE';
  el.biStatus.title = online
    ? `Blue Iris ${version || ''} at ${host}`
    : `Blue Iris unreachable at ${host}\n${error || ''}`;

  if (!online) {
    // Overrides a held TWAB receipt on purpose: if the link is down, that is
    // the thing to know, and it also means the receipt may no longer be true.
    showAlert(
      `Blue Iris is not answering at ${host}. Polling has backed off; ` +
      `the Controller will reconnect on its own.`, 'error');
    return;
  }
  // Everything below is standing state rather than news, so it waits its turn
  // behind a message the operator has not had time to read yet.
  if (performance.now() < alertHoldUntil) return;

  if (state.take.adopted && state.take.active) {
    showAlert('This recording was started outside the Controller. It has been ' +
      'adopted, so STOP will end it.', 'warn');
  } else {
    const failed = Object.keys(state.take.failures || {});
    if (failed.length) {
      showAlert(`Did not start: ${failed.join(', ')}. Everything else is recording.`, 'error');
    } else {
      hideAlert();
    }
  }
}

/* The poller pushes state every few seconds and clears the banner each time it
 * finds nothing wrong. A TWAB result is not a state — it is the answer to a
 * press that already happened — so it would be wiped within one poll. `hold`
 * keeps it on screen for a set time. A *new* message still replaces it
 * immediately: if Blue Iris drops mid-hold, that outranks the receipt.
 */
let alertHoldUntil = 0;

function showAlert(text, kind, hold) {
  el.alert.textContent = text;
  el.alert.className = 'alert' + (kind === 'warn' ? ' alert--warn'
                                : kind === 'ok' ? ' alert--ok' : '');
  el.alert.hidden = false;
  alertHoldUntil = hold ? performance.now() + hold : 0;
}

/* Same, for a message with a list in it. Every fragment is escaped at the call
 * site — camera names and Blue Iris error strings both reach here. */
function showAlertHtml(html, kind, hold) {
  el.alert.innerHTML = html;
  el.alert.className = 'alert' + (kind === 'warn' ? ' alert--warn'
                                : kind === 'ok' ? ' alert--ok' : '');
  el.alert.hidden = false;
  alertHoldUntil = hold ? performance.now() + hold : 0;
}

function hideAlert() {
  if (performance.now() < alertHoldUntil) return;
  el.alert.hidden = true;
}

function cameraStatus(cam, bridge) {
  // Before every other reading, because all of them are true and all of them
  // are misleading: an encoder that is off makes its camera look broken, and
  // the one thing the operator needs to know is that they did it on purpose.
  // Measured: ~20 s to record, ~80 s to a full minute of pre-roll, and Blue
  // Iris calls it "offline" for most of the first stretch.
  if (bridge && bridge.pending) {
    return bridge.enabled
      ? { cls: 'dot--warn', text: 'Encoder starting' }
      : { cls: 'dot--warn', text: 'Encoder stopping' };
  }
  if (cam.bridgeOff) return { cls: 'dot--off', text: 'Encoder off' };
  if (!cam.enabled) return { cls: 'dot--off', text: 'Disabled' };
  if (!cam.online) return { cls: 'dot--err', text: 'Offline' };
  if (cam.noSignal) return { cls: 'dot--warn', text: 'No signal' };
  if (cam.paused) return { cls: 'dot--warn', text: 'Paused' };
  return { cls: 'dot--ok', text: `${cam.fps} fps` };
}

function renderCameras(state) {
  const hide = state.settings.hide_unavailable;
  // An encoder that is off makes its camera unavailable, so "hide unavailable"
  // would hide the only switch that can bring it back. Never hide a camera the
  // operator switched off themselves.
  const list = state.cameras.filter((c) => !hide || c.available || c.bridgeOff);
  const locked = state.take.active;
  const bridges = new Map((state.bridges || []).map((b) => [b.camera, b]));

  el.camerasEmpty.hidden = list.length > 0;
  el.cameras.innerHTML = '';

  for (const cam of list) {
    const bridge = bridges.get(cam.name);
    const status = cameraStatus(cam, bridge);
    const node = document.createElement('article');
    node.className = 'cam'
      + (cam.armed ? ' cam--armed' : '')
      + (cam.overwatch ? ' cam--overwatch' : '')
      + (cam.recording ? ' cam--recording' : '')
      + (cam.available ? '' : ' cam--down')
      + (cam.bridgeOff ? ' cam--encoff' : '');
    node.setAttribute('role', 'listitem');

    const tally = (cam.recording
      ? '<span class="cam-tally cam-tally--rec"><span class="dot"></span>REC</span>'
      : (cam.armed ? '<span class="cam-tally cam-tally--armed"><span class="dot"></span>Armed</span>' : ''))
      + (cam.overwatch ? '<span class="cam-tally cam-tally--ow"><span class="dot"></span>Watch</span>' : '')
      // Permanent, not state-dependent. The picture on this card is a waveform
      // and the card is otherwise indistinguishable from a camera, which is the
      // whole problem the badge exists to fix.
      + (cam.audioOnly ? '<span class="cam-tally cam-tally--audio">Audio</span>' : '');

    const thumb = cam.hasThumb
      ? `<img src="/api/thumb/${encodeURIComponent(cam.name)}?t=${Math.floor(state.ts)}" alt="" loading="lazy">`
      : '<span class="no-thumb">no image</span>';

    const failure = cam.failure
      ? `<span class="cam-fail">FAILED</span>`
      : '';

    /* ---- the third slot -------------------------------------------------
     * Three cases, and each card gets a slot whichever one it is, so the ENC
     * column stays a column you can scan down.
     *
     *   a bridge      a real switch
     *   audio-only    a lit plate. MIC1's encoder is deliberately always on and
     *                 has no switch at all: arming a camera wakes THAT camera's
     *                 encoder, and arming a camera has no reason to wake a
     *                 microphone, so the "on is implied, off is only ever
     *                 explicit" rule that protects the others cannot protect
     *                 this one. An empty slot would read as OFF, which is the
     *                 one thing this must never say.
     *   neither       a dash. This camera reaches Blue Iris directly; there is
     *                 no encoder on this machine to switch.
     */
    const encSlot = bridge
      ? `<button class="seg seg--enc" type="button" aria-pressed="${bridge.enabled}"
                 aria-label="Turn the ${escapeHtml(cam.name)} encoder ${bridge.enabled ? 'off' : 'on'}"
                 title="This camera's ffmpeg encoder on the shop PC. Off saves power and takes the camera offline in Blue Iris; back in ~20s, ~80s for a full minute of pre-roll.">Enc</button>`
      : cam.audioOnly
        ? `<span class="seg seg--enc seg--fixed"
                 title="This is a microphone. Its encoder is deliberately always on and has no switch — arming a camera has no reason to wake a mic, so nothing else would turn it back on.">Always</span>`
        : `<span class="seg seg--none" aria-hidden="true"
                 title="${escapeHtml(cam.name)} reaches Blue Iris directly. There is no encoder on this machine to switch.">&mdash;</span>`;

    node.innerHTML = `
      <div class="cam-thumb">${tally}${thumb}</div>
      <div class="cam-body">
        <div class="cam-name">${escapeHtml(cam.display)}</div>
        <div class="cam-state">
          <span class="dot ${status.cls}"></span>${escapeHtml(status.text)} ${failure}
        </div>
      </div>
      <div class="cam-controls" role="group" aria-label="${escapeHtml(cam.name)} controls">
        <button class="seg seg--rec" type="button" aria-pressed="${cam.armed}"
                aria-label="${cam.armed ? 'Disarm' : 'Arm'} ${escapeHtml(cam.name)}"
                ${locked ? 'disabled' : ''}
                title="${locked ? 'Arming is locked while recording' : 'Arm: this camera records when you press Record'}">Arm</button>
        <button class="seg seg--ow" type="button" aria-pressed="${cam.overwatch}"
                aria-label="${cam.overwatch ? 'Remove' : 'Add'} ${escapeHtml(cam.name)} ${cam.overwatch ? 'from' : 'to'} overwatch"
                title="Overwatch: this camera's rolling pre-trigger buffer is saved by the That Was Awesome button">Watch</button>
        ${encSlot}
      </div>`;

    node.querySelector('.seg--rec').addEventListener('click', () => {
      setArmed(cam.name, !cam.armed);
    });
    node.querySelector('.seg--ow').addEventListener('click', () => {
      setOverwatch(cam.name, !cam.overwatch);
    });
    if (bridge) {
      node.querySelector('button.seg--enc').addEventListener('click', () => {
        setBridge(bridge.id, !bridge.enabled, cam.name, state);
      });
    }
    el.cameras.appendChild(node);
  }

  const armed = state.armed.length;
  const ow = (state.overwatch || []).length;
  el.armSummary.textContent = (armed
    ? `${armed} armed of ${state.cameras.length}`
    : `none armed of ${state.cameras.length}`)
    + (ow ? ` · ${ow} overwatch` : '');
  el.armAll.disabled = locked;
  el.armNone.disabled = locked || armed === 0;
}

/* ---- encoders ----------------------------------------------------------- *
 * Four ffmpeg processes, four NVENC sessions, running 24/7 whether or not
 * anyone is filming.
 *
 * 🔑 The per-encoder switch is rendered ON THE CAMERA CARD, in renderCameras —
 * not here. It was a separate list once, and a list is the wrong shape for
 * this: to switch off CAM5 you had to leave CAM5, find the row that says CAM5
 * and trust the label. Every control for a camera belongs on that camera.
 *
 * What is left here is only what is genuinely fleet-wide: the count, the two
 * bulk buttons, and what off costs.
 *
 * Note there are two truths on screen at once — what was asked for and what
 * Blue Iris reports — and for a few seconds after a tap they disagree. That is
 * what `pending` is: not a spinner on a timer, but an honest "asked, not agreed
 * yet". Measured: ~20 s to recording, ~80 s to a full minute of pre-roll.
 */
function renderBridges(state) {
  const list = state.bridges || [];
  el.bridgesSection.hidden = list.length === 0;
  if (!list.length) return;

  const on = list.filter((b) => b.enabled).length;
  const busy = list.filter((b) => b.pending).length;
  el.bridgeSummary.textContent = busy
    ? `${on} of ${list.length} on · ${busy} changing`
    : on
      ? `${on} of ${list.length} on`
      : 'all off — no power being burned';
  el.bridgesOn.disabled = on === list.length;
  el.bridgesOff.disabled = on === 0;
}

function renderTransport(state) {
  const take = state.take;
  const armed = state.armed.length;
  const recording = take.recordingCount || 0;
  const failed = Object.keys(take.failures || {}).length;

  syncClock(take);

  if (take.active) {
    const partial = failed > 0 || (take.cameras.length && recording < take.cameras.length);
    el.record.dataset.state = partial ? 'partial' : 'recording';
    el.recordLabel.textContent = partial
      ? `Recording ${recording}/${take.cameras.length || recording}`
      : 'Recording';
    el.recordHint.textContent = holdMs() > 0 ? 'hold to stop' : 'tap to stop';
    el.record.setAttribute('aria-label', 'Stop recording');
    el.record.disabled = false;
    el.status.textContent = take.adopted
      ? `Adopted — ${take.started.join(', ')}`
      : `${recording} camera${recording === 1 ? '' : 's'} rolling`;
  } else {
    el.record.dataset.state = 'idle';
    el.recordLabel.textContent = 'Record';
    el.recordHint.textContent = '';
    el.record.setAttribute('aria-label', 'Start recording');
    el.record.disabled = armed === 0 || !state.bi.online;
    el.timer.textContent = '00:00';
    el.status.textContent = !state.bi.online
      ? 'Blue Iris offline'
      : (armed ? `${armed} armed, ready` : 'Nothing armed');
  }
}

/* ---- That Was Awesome --------------------------------------------------- *
 * The button is only meaningful when at least one camera is on Watch, and the
 * hint says which — pressing it and learning afterwards that nothing was
 * selected is the exact failure the device exists to prevent.
 *
 * `twabLock` holds the button's own result state against the poller, which
 * would otherwise repaint it back to idle a second after the press.
 */
let twabLock = 0;

function renderTwab(state) {
  const watching = (state.overwatch || []).length;
  el.twab.disabled = watching === 0 || !state.bi.online;

  if (performance.now() < twabLock) return;

  // A Watch camera whose encoder is off will not save. Saying "save 9 cameras"
  // when four of them cannot is the exact lie this button exists to not tell.
  const dark = (state.cameras || []).filter((c) => c.overwatch && c.bridgeOff).length;

  el.twab.dataset.state = 'idle';
  el.twabHint.textContent = !state.bi.online
    ? 'Blue Iris offline'
    : watching
      ? (dark
          ? `save ${watching - dark} of ${watching} — ${dark} encoder${dark === 1 ? '' : 's'} off`
          : `save ${watching} camera${watching === 1 ? '' : 's'}`)
      : 'nothing on watch';
  el.twab.title = watching
    ? `Saves the rolling buffer on ${(state.overwatch || []).join(', ')}`
    : 'Tick Watch on a camera to arm this button';
}

function setTwabState(name, hint, holdMs) {
  el.twab.dataset.state = name;
  el.twabHint.textContent = hint;
  twabLock = performance.now() + holdMs;
}

let renderedClipsKey = null;

function renderClips(state) {
  const clips = state.take.clips || [];
  const collecting = state.take.collecting;

  const hidden = state.take.active || (!clips.length && !collecting);
  el.clipsSection.hidden = hidden;

  /* Rebuilding this list throws away the <video> elements and whatever is
   * playing in them. The poller broadcasts a fresh snapshot every few seconds,
   * so an unconditional rebuild restarts a clip you are watching on every poll
   * — it plays a moment, then snaps back to the start. Only rebuild when the
   * clips (or the waiting/hidden state) have actually changed. */
  const key = JSON.stringify({
    hidden,
    waiting: collecting && !clips.length,
    clips: clips.map((c) => c.url),
  });
  if (key === renderedClipsKey) return;
  renderedClipsKey = key;

  if (hidden) { el.clips.innerHTML = ''; return; }

  el.clips.innerHTML = '';

  if (collecting && !clips.length) {
    const waiting = document.createElement('p');
    waiting.className = 'empty';
    waiting.textContent = 'Waiting for Blue Iris to finish writing the clips…';
    el.clips.appendChild(waiting);
    return;
  }
  for (const clip of clips) {
    const node = document.createElement('div');
    node.className = 'clip';
    const when = clip.date ? new Date(clip.date * 1000).toLocaleTimeString() : '';
    const codec = clip.codec ? ` &middot; ${escapeHtml(clip.codec)}` : '';
    /* This clip starts earlier than the take by more than any pre-roll can
     * account for, so the take is somewhere inside a longer recording rather
     * than at its start. Say where the footage is — that is the actionable
     * part — without asserting a cause. The obvious suspect, Blue Iris clip
     * grouping, is measurably NOT it: moviegroup is 0 everywhere on this rig.
     * Every take here should start a new file, so this note firing at all is
     * itself worth knowing about. */
    const spans = clip.spans
      ? `<p class="clip-note">This file starts at ${escapeHtml(when)}, well before
           the take did — your footage is somewhere inside it, not at the start.
           Scrub forward. If Blue Iris still has the file open it may not play
           here or in your editor yet.</p>`
      : '';
    node.innerHTML = `
      <div class="clip-head">
        <span class="clip-name">${escapeHtml(clip.file || '')}</span>
        <span class="clip-meta">${escapeHtml(clip.camera || '')} &middot; ${escapeHtml(clip.filesize || '')}${codec} &middot; ${escapeHtml(when)}</span>
      </div>
      ${spans}
      <video controls preload="metadata" playsinline src="${clip.url}"></video>`;

    /* A <video> that fails gives you one undifferentiated 'error' event, but
     * there are two very different causes here and telling the operator the
     * wrong one wastes their time mid-shoot:
     *
     *   503  Blue Iris has the file locked because its group is still open.
     *        Nothing is wrong with the footage and nothing needs installing —
     *        it just is not readable yet, here or in an editor.
     *   ok   The bytes are being served fine, so the browser cannot decode
     *        them: Blue Iris writes H.265 inside .mp4, which Chrome and Firefox
     *        on Windows only play with the OS HEVC codec present.
     *
     * So ask the server which it is before saying anything. */
    const video = node.querySelector('video');
    video.addEventListener('error', async () => {
      let locked = false;
      try {
        /* Range-limited: enough to learn the status without pulling the clip.
         * Not HEAD — Blue Iris answers 503 to HEAD for every clip. */
        const probe = await fetch(clip.url, { headers: { Range: 'bytes=0-1' } });
        locked = probe.status === 503;
      } catch (err) {
        /* Controller unreachable; fall through to the codec explanation. */
      }
      const fallback = document.createElement('div');
      fallback.className = 'clip-fallback';
      fallback.innerHTML = locked
        ? `Blue Iris still has this file open &mdash; it groups consecutive ` +
          `recordings from this camera into one file and locks it until the ` +
          `group closes. The footage is safe, but nothing can read it yet. ` +
          `Try again later, or turn grouping off for this camera in Blue Iris ` +
          `if you want every take available the moment you stop.`
        : `This browser cannot decode the clip &mdash; Blue Iris records H.265 in an ` +
          `.mp4 container, which Chrome and Firefox on Windows only play with the ` +
          `OS HEVC codec installed. ` +
          `<a href="${clip.url}" download>Download the file</a> and open it locally, ` +
          `or view it on iOS/Safari.`;
      video.replaceWith(fallback);
    }, { once: true });

    el.clips.appendChild(node);
  }
}

/* ---- storage ------------------------------------------------------------- *
 * 🔴 The ring shows the VOLUME, not Blue Iris's clip allocation.
 *
 * This used to be a full-width bar reading "49% of the Blue Iris clip
 * allocation" — a number that looks like a warning and is not one. The
 * allocation is a ring buffer: Blue Iris fills it and then recycles the oldest
 * clips, so 100% is its normal working state, not a problem. Meanwhile the
 * actual constraint, 539 GB of real free space, was a footnote.
 *
 * So the ring fills with the one number that can stop a recording — how full
 * the disk is — and the label is how much room is left. Everything else moved
 * into the tooltip, including how long the current arm set can run, which is
 * the only form of "how much space" anyone has ever actually wanted mid-shoot.
 *
 * ⚠️ Blue Iris reports BPS in BYTES per second, not bits. Verified against the
 * independently measured 21.7 GB/hr for all nine cameras: the byte reading
 * gives 21.56, the bit reading 2.69. Do not "fix" the missing /8.
 */
function renderDisk(state) {
  const disk = state.disk;
  if (!disk) {
    el.diskText.textContent = '—';
    el.diskArc.setAttribute('stroke-dasharray', '0 100');
    el.disk.title = 'Blue Iris is not reporting disk status.';
    return;
  }

  const total = disk.totalGb || (disk.usedGb + disk.freeGb);
  const usedPct = total ? Math.round(100 * (1 - disk.freeGb / total)) : 0;

  el.diskArc.setAttribute('stroke-dasharray',
    `${Math.min(100, Math.max(0, usedPct))} 100`);
  el.diskArc.setAttribute('class', 'disk-ring-arc'
    + (usedPct >= 92 ? ' is-critical' : usedPct >= 80 ? ' is-high' : ''));
  el.diskText.textContent = `${Math.round(disk.freeGb)} GB`;

  /* Hours left, at the bitrate the cameras are actually producing right now.
   * Charged against whatever is armed; with nothing armed it answers for what
   * "Arm all" would cost, because that is the question you are asking when you
   * look at this before a shoot. */
  const armedSet = state.armed || [];
  const charged = (state.cameras || []).filter((c) =>
    armedSet.length ? c.armed : c.available);
  const bytesPerSec = charged.reduce((n, c) => n + (c.bps || 0), 0);
  const gbPerHour = bytesPerSec * 3600 / 1e9;
  const hours = gbPerHour > 0.05 ? disk.freeGb / gbPerHour : null;

  el.disk.title =
    `${disk.freeGb} GB free of ${total} GB on ${disk.disk} — ${usedPct}% used.\n`
    + `Blue Iris clip allocation: ${disk.usedGb} of ${disk.allocatedGb} GB. `
    + `That one is a ring buffer, so full is normal and not a warning.\n`
    + (hours
        ? `About ${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hours left `
          + `at ${gbPerHour.toFixed(1)} GB/hr — ${charged.length} camera`
          + `${charged.length === 1 ? '' : 's'} `
          + `${armedSet.length ? 'armed' : 'available, if you armed them all'}.`
        : 'No cameras producing a bitrate, so no estimate.');
}

/* ---- interface scale ----------------------------------------------------- *
 * One number drives every dimension in the stylesheet (see its header). The
 * server holds it, like every other setting, so changing it on the phone
 * changes it on the shop monitor too — which is the point of server-side
 * settings on a rig you walk around.
 */
function applyScale(percent) {
  const pct = Math.min(150, Math.max(75, Number(percent) || 100));
  document.documentElement.style.setProperty('--ui-scale', String(pct / 100));
}

let settingsDirty = false;
function renderSettings(state) {
  applyScale(state.settings.ui_scale);   // outside the dirty guard: it is live feedback
  syncWakeLock(state.settings.keep_awake ?? true);
  if (settingsDirty) return;   // don't fight the user mid-edit
  const s = state.settings;
  el.setTheme.value = s.theme;
  el.setScale.value = String(s.ui_scale ?? 100);
  el.setAwake.checked = !!s.keep_awake;
  el.setAudio.checked = !!s.audio_matters;
  el.setHide.checked = !!s.hide_unavailable;
  el.setThumb.value = s.thumbnail_interval;
  el.setHold.value = s.stop_hold_ms;

  renderAbout(state);
}

/* The viewport readout is not trivia. Every layout decision in styles.css is
 * keyed off CSS pixels and the root font size, and neither is guessable from
 * the outside — a "1600x720" phone reports 800x360 here. When a layout is
 * wrong on a screen nobody is holding, this is the line that says why. */
function renderAbout(state) {
  const hooks = state.hooks;
  const root = Math.round(
    parseFloat(getComputedStyle(document.documentElement).fontSize) * 10) / 10;
  const install = window.matchMedia('(display-mode: standalone), (display-mode: fullscreen)').matches
    ? 'installed'
    : (window.isSecureContext ? 'browser tab, installable' : 'browser tab — insecure origin, cannot install');

  el.about.innerHTML =
    `Blue Iris ${escapeHtml(state.bi.version || '?')} at <code>${escapeHtml(state.bi.host || '')}</code>` +
    (state.bi.cpu != null ? ` · CPU ${state.bi.cpu}% · ${escapeHtml(state.bi.mem || '')}` : '') +
    `<br>Hooks: ${hooks.loaded ? escapeHtml(hooks.functions.join(', ') || 'none defined') : 'no hooks.py'}` +
    `<br>Audio switch is passed to your hooks; it changes nothing in Blue Iris.` +
    `<br>Viewport <code>${window.innerWidth}×${window.innerHeight}</code> CSS px ` +
    `(${window.devicePixelRatio}× dpr, ~${Math.round(window.innerWidth * window.devicePixelRatio)}×${Math.round(window.innerHeight * window.devicePixelRatio)} device px) ` +
    `· root <code>${root}px</code> · ${escapeHtml(install)}` +
    `<br>Screen wake lock: ${escapeHtml(wakeLockStatus())}`;
}

/* Keep the readout honest while the window is being dragged — this is the one
 * place someone resizing on purpose looks to see what they got. */
window.addEventListener('resize', () => {
  if (snapshot && !el.settings.hidden) renderAbout(snapshot);
});

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
}

/* ---- actions ------------------------------------------------------------ */

async function request(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* empty body is fine */ }
  return { status: response.status, ok: response.ok, payload };
}

async function post(path, body) {
  const { ok, status, payload } = await request(path, body);
  if (!ok) {
    showAlert(payload.detail || payload.error || `Request failed (${status})`, 'error');
  }
  return payload;
}

/* Arming or watching a camera turns its encoder on — the server does it, the
 * page only says so. Worth saying: the camera does not come back instantly, and
 * a green Armed switch over a camera that cannot record for another twenty
 * seconds is the kind of quiet lie this rig keeps getting caught by. */
function warnIfWakingEncoder(camera) {
  const bridge = ((snapshot && snapshot.bridges) || [])
    .find((b) => b.camera === camera);
  if (bridge && !bridge.enabled) {
    showAlert(`${camera}'s encoder was off. Starting it — about 20 seconds ` +
      `until Blue Iris can record it, 80 for a full minute of pre-roll.`,
      'warn', 6000);
  }
}

async function setArmed(camera, armed) {
  if (armed) warnIfWakingEncoder(camera);
  await post('/api/arm', { camera, armed });
}

async function setOverwatch(camera, overwatch) {
  if (overwatch) warnIfWakingEncoder(camera);
  await post('/api/overwatch', { camera, overwatch });
}

/* Turning an encoder off is the one control here that can silently cost you a
 * moment: the camera goes offline in Blue Iris, so That Was Awesome stops
 * saving it. Warn, then do it. A veto would only push the operator to the
 * console, where nothing is logged and nothing is on screen. */
async function setBridge(bridge, on, camera, state) {
  if (!on) {
    const watching = (state.overwatch || []).includes(camera);
    if (watching) {
      showAlert(`${camera} is on Watch and its encoder is now off — ` +
        `That Was Awesome will not save it until you turn it back on.`, 'warn', 8000);
    }
  }
  await post('/api/bridges', { bridge, on });
}

async function setAllBridges(on) {
  await post('/api/bridges', { all: true, on });
}

/* ---- That Was Awesome press --------------------------------------------- *
 * No hold guard, unlike STOP. A stray press costs one clip of the last minute;
 * a press that arrives a second too late costs the moment. The asymmetry is the
 * whole point of the device, so this is a plain tap.
 *
 * The three outcomes are told apart by status, not by guessing from the body:
 *   200  at least one camera saved — but `failures` may still name others, and
 *        those cameras did NOT save, so they are listed rather than summarised.
 *   409  nothing is on Watch. A configuration mistake, not a fault.
 *   502  cameras were selected and none saved. This is the bad one.
 */
const TWAB_HOLD = 12000;

function failureList(failures) {
  return '<ul>' + Object.entries(failures).map(([camera, why]) =>
    `<li><strong>${escapeHtml(camera)}</strong> — ${escapeHtml(why)}</li>`).join('') + '</ul>';
}

/* The one place a press outcome becomes pixels. Both routes in — this page's
 * own button, and a press from the physical one arriving over the state stream
 * — end up here, so the same event cannot look like two different events
 * depending on which button someone pushed.
 *
 * `who` is only ever a prefix on the banner: the button state and the hold are
 * identical either way, because what matters is whether the moment was saved,
 * not who asked. */
/* How much lead-in the press caught, in words, or '' when it caught a full one.
 *
 * A press soon after another one cannot have a full minute behind it — the first
 * press emptied the buffer and it refills in real time. The button used to report
 * both cases as an identical green "saved", which is true and useless: the moment
 * is on disk either way, but with 1.2 s in front of it instead of 60. Saying so is
 * the difference between an operator who waits a few seconds before pressing again
 * and one who finds out in the edit. */
function leadPhrase(result) {
  if (!result || result.prerollSec == null || result.fullLead) return '';
  const s = result.prerollSec;
  return s < 1 ? 'almost no lead-in' : `only ${s < 10 ? s.toFixed(1) : Math.round(s)}s of lead-in`;
}

function showTwabResult({ status, triggered, failures, who, prerollSec, fullLead, extended }) {
  const saved = triggered || [];
  const failed = Object.keys(failures || {});
  const from = who ? `${who}: ` : '';
  const lead = leadPhrase({ prerollSec, fullLead });

  if (status === 200 && !failed.length && extended) {
    // Not a second clip at all — Blue Iris was still recording, so this press
    // lengthened the clip already being written. Calling that "saved" a second
    // time would have the operator hunting for a file that does not exist.
    setTwabState('saved', `held ${saved.length}`, TWAB_HOLD);
    showAlert(`${from}Still recording — kept the camera rolling on ${saved.join(', ')}. `
      + `This lengthens the clip already being written; it does not start a new one.`,
      'ok', TWAB_HOLD);
  } else if (status === 200 && !failed.length && lead) {
    setTwabState('partial', `saved ${saved.length} · ${Math.round(prerollSec)}s`, TWAB_HOLD);
    showAlert(`${from}Saved on ${saved.join(', ')}, but with ${lead} — the previous press `
      + `used up the buffer. Leave about a minute between presses for a full one.`,
      'warn', TWAB_HOLD);
  } else if (status === 200 && !failed.length) {
    setTwabState('saved', `saved ${saved.length}`, TWAB_HOLD);
    showAlert(`${from}Saved the last minute on ${saved.join(', ')}. `
      + `The clip appears in Blue Iris once the post-roll finishes.`, 'ok', TWAB_HOLD);
  } else if (status === 200) {
    setTwabState('partial', `${saved.length} of ${saved.length + failed.length}`, TWAB_HOLD);
    showAlertHtml(`${escapeHtml(from)}Saved on ${escapeHtml(saved.join(', '))}`
      + (lead ? ` (${escapeHtml(lead)})` : '') + `. `
      + `<strong>Not saved</strong> on:` + failureList(failures), 'warn', TWAB_HOLD);
  } else if (status === 409) {
    setTwabState('failed', 'nothing on watch', TWAB_HOLD);
    showAlert(`${from}Nothing is on Watch, so nothing was saved. `
      + 'Tick Watch on the cameras this button should rescue.', 'warn', TWAB_HOLD);
  } else {
    setTwabState('failed', 'nothing saved', TWAB_HOLD);
    showAlertHtml(`${escapeHtml(from)}<strong>Nothing was saved.</strong>`
      + (failed.length ? failureList(failures) : ''), 'error', TWAB_HOLD);
  }
}

/* A press from the physical button reaches this page only as a new state, so it
 * is recognised by `twab.seq` changing. A press this page fired has already
 * been drawn by `doTwab`, and the broadcast can land either side of that fetch
 * resolving — so the seq is claimed on the way out rather than raced for. */
let renderedTwabSeq = 0;
let twabBaselined = false;
let twabLocalPending = false;
/* Verification lands ~16 s after the press, on the SAME seq, so it cannot be
 * recognised by the seq changing the way a press is. Tracked separately. */
let renderedVerifySeq = 0;

/* The late verdict: the clips this press wrote have been decoded. Silent when
 * they are all fine — the green receipt already said so and repeating it teaches
 * people to ignore the banner. Loud, and held long, when they are not, because
 * this is the only moment anyone finds out that a saved moment is not really
 * there while it might still be repeatable. */
function renderTwabVerify(state) {
  const result = state.twab;
  if (!result || !result.verify) return;
  if (result.seq === renderedVerifySeq) return;
  renderedVerifySeq = result.seq;

  const bad = result.verify.bad || {};
  const names = Object.keys(bad);
  if (!names.length) return;

  setTwabState('failed', `${names.length} not saved`, TWAB_HOLD * 2);
  showAlertHtml(
    `<strong>Checked the footage: ${escapeHtml(names.join(', '))} saved nothing playable.</strong> `
    + `The press reported success and the file exists, but it will not decode.`
    + failureList(bad)
    + `If the moment can be repeated, do it now.`,
    'error', TWAB_HOLD * 2);
}

function renderTwabResult(state) {
  const result = state.twab;

  /* The first state a page receives sets the baseline and draws nothing —
   * otherwise opening the page would replay whatever receipt the rig happened
   * to be holding. It has to be taken from that state rather than assumed to be
   * seq 0, because "no press yet" and "never looked" are both 0: keying off the
   * seq alone swallowed the first press after every page load, which is the one
   * press most likely to be someone's only press. */
  if (!twabBaselined) {
    twabBaselined = true;
    renderedTwabSeq = result ? result.seq : 0;
    // Baseline the verdict too, or opening the page would replay an old
    // "saved nothing playable" alarm for a press someone already dealt with.
    renderedVerifySeq = result ? result.seq : 0;
    return;
  }

  if (!result || result.seq === renderedTwabSeq) return;
  renderedTwabSeq = result.seq;
  if (twabLocalPending) { twabLocalPending = false; return; }
  // Backstop for a receipt that reaches this page long after the press — a
  // phone waking from sleep, say. Aged against the snapshot's own clock, not
  // this device's, so a few seconds of skew cannot suppress a live one.
  if ((state.ts || 0) - (result.at || 0) > TWAB_HOLD / 1000) return;
  showTwabResult({
    status: result.status,
    triggered: result.triggered,
    failures: result.failures,
    prerollSec: result.prerollSec,
    fullLead: result.fullLead,
    extended: result.extended,
    who: result.source === 'ui' ? 'Another screen' : 'Button',
  });
}

/* Its own lock, not the transport's `busy`. TWAB must be pressable at any
 * moment, including while a start or stop is still in flight. */
let twabBusy = false;

async function doTwab() {
  if (twabBusy || el.twab.disabled) return;
  twabBusy = true;
  twabLocalPending = true;
  setTwabState('busy', 'saving…', 4000);
  try {
    const { status, payload } = await request('/api/twab', { source: 'ui' });
    showTwabResult({
      status, triggered: payload.triggered, failures: payload.failures,
      prerollSec: payload.prerollSec, fullLead: payload.fullLead,
      extended: payload.extended,
    });
  } catch (err) {
    // Never reached the Controller, so it never recorded a press and no
    // broadcast is coming. Release the claim or the next real one is swallowed.
    twabLocalPending = false;
    setTwabState('failed', 'no link', TWAB_HOLD);
    showAlert('Could not reach the Controller, so nothing was saved.', 'error', TWAB_HOLD);
  } finally {
    twabBusy = false;
  }
}

el.twab.addEventListener('click', doTwab);

el.armAll.addEventListener('click', () => {
  if (!snapshot) return;
  // Cameras whose encoder is off are included on purpose. They are unavailable
  // only because they were switched off, arming them turns the encoder back on,
  // and "Arm all" that silently skipped four of nine would be the worst
  // possible answer — it looks like it worked.
  const wake = snapshot.cameras.filter((c) => c.bridgeOff).map((c) => c.name);
  const names = snapshot.cameras
    .filter((c) => c.available || c.bridgeOff).map((c) => c.name);
  if (wake.length) {
    showAlert(`Starting the encoder${wake.length === 1 ? '' : 's'} for ` +
      `${wake.join(', ')} — about 20 seconds until Blue Iris can record ` +
      `${wake.length === 1 ? 'it' : 'them'}, 80 for a full minute of pre-roll.`,
      'warn', 6000);
  }
  post('/api/arm/bulk', { cameras: names });
});
el.armNone.addEventListener('click', () => post('/api/arm/bulk', { cameras: [] }));

el.bridgesOn.addEventListener('click', () => setAllBridges(true));
el.bridgesOff.addEventListener('click', () => {
  const watching = ((snapshot && snapshot.bridges) || [])
    .filter((b) => b.enabled && (snapshot.overwatch || []).includes(b.camera))
    .map((b) => b.camera);
  if (watching.length) {
    showAlert(`${watching.join(', ')} ${watching.length === 1 ? 'is' : 'are'} on Watch. ` +
      `With the encoders off, That Was Awesome cannot save ` +
      `${watching.length === 1 ? 'it' : 'them'}.`, 'warn', 8000);
  }
  setAllBridges(false);
});

/* ---- record button: tap to start, hold to stop -------------------------- *
 * Pointer events cover mouse, touch and pen with one code path. The hold guard
 * only applies to stopping — starting should never be slower than it has to be,
 * but ending a take by brushing a phone in your pocket is a lost shot.
 */
function holdMs() {
  return snapshot ? (snapshot.settings.stop_hold_ms ?? 1000) : 1000;
}

let holdTimer = null;
let holdStart = 0;
let holdFrame = null;

function beginHold() {
  const duration = holdMs();
  holdStart = performance.now();
  const paint = () => {
    const progress = Math.min(1, (performance.now() - holdStart) / duration);
    el.recordFill.style.transform = `scaleX(${progress})`;
    if (progress < 1) holdFrame = requestAnimationFrame(paint);
  };
  holdFrame = requestAnimationFrame(paint);
  holdTimer = setTimeout(() => { cancelHold(); doStop(); }, duration);
}

function cancelHold() {
  if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
  if (holdFrame) { cancelAnimationFrame(holdFrame); holdFrame = null; }
  el.recordFill.style.transform = 'scaleX(0)';
}

async function doStart() {
  if (busy) return;
  busy = true;
  el.record.disabled = true;
  try { await post('/api/record', { action: 'start' }); }
  finally { busy = false; }
}

async function doStop() {
  if (busy) return;
  busy = true;
  try { await post('/api/record', { action: 'stop' }); }
  finally { busy = false; }
}

// A hold gesture captures the pointer, so releasing it always produces a
// trailing `click` targeted at the button — even if the pointer was released
// off it. This flag lets the click handler tell that click apart from a
// deliberate tap and swallow it.
let holdEngaged = false;

el.record.addEventListener('pointerdown', (event) => {
  holdEngaged = false;
  if (el.record.disabled || !snapshot) return;
  if (snapshot.take.active && holdMs() > 0) {
    el.record.setPointerCapture?.(event.pointerId);
    holdEngaged = true;
    beginHold();
  }
});
['pointerup', 'pointercancel', 'pointerleave'].forEach((type) => {
  el.record.addEventListener(type, () => cancelHold());
});
el.record.addEventListener('click', () => {
  if (!snapshot || el.record.disabled) return;
  // Swallow the click that ends a hold-to-stop gesture. Without this, holding
  // to stop a take and then lifting off the button flips take.active to false
  // and the trailing click immediately starts a brand new take.
  if (holdEngaged) { holdEngaged = false; return; }
  if (snapshot.take.active) {
    if (holdMs() === 0) doStop();     // hold guard disabled: a tap stops
  } else {
    doStart();
  }
});
// Keyboard users get an unambiguous path that does not require a long press.
el.record.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  if (!snapshot) return;
  if (snapshot.take.active) doStop(); else doStart();
});

/* ---- settings ----------------------------------------------------------- */

function openSettings() {
  el.settings.hidden = false;
  el.settingsBtn.setAttribute('aria-expanded', 'true');
}
function closeSettings() {
  el.settings.hidden = true;
  el.settingsBtn.setAttribute('aria-expanded', 'false');
  settingsDirty = false;
  if (snapshot) renderSettings(snapshot);
}
el.settingsBtn.addEventListener('click', openSettings);
el.settingsClose.addEventListener('click', closeSettings);
el.settings.addEventListener('click', (event) => {
  if (event.target === el.settings) closeSettings();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !el.settings.hidden) closeSettings();
});

function pushSetting(key, value) {
  settingsDirty = true;
  post('/api/settings', { [key]: value }).finally(() => { settingsDirty = false; });
}
el.setTheme.addEventListener('change', () => {
  // Apply instantly so the change is visible before the round trip lands.
  document.documentElement.dataset.theme = el.setTheme.value;
  pushSetting('theme', el.setTheme.value);
});
el.setScale.addEventListener('change', () => {
  // Apply instantly, like the theme: the operator is judging the result, and a
  // round trip's worth of delay makes a scale picker feel broken.
  applyScale(el.setScale.value);
  pushSetting('ui_scale', Number(el.setScale.value));
  if (snapshot) renderAbout(snapshot);
});
el.setAwake.addEventListener('change', () => {
  pushSetting('keep_awake', el.setAwake.checked);
  syncWakeLock(el.setAwake.checked);
});
el.setAudio.addEventListener('change', () => pushSetting('audio_matters', el.setAudio.checked));
el.setHide.addEventListener('change', () => pushSetting('hide_unavailable', el.setHide.checked));
el.setThumb.addEventListener('change', () => pushSetting('thumbnail_interval', Number(el.setThumb.value)));
el.setHold.addEventListener('change', () => pushSetting('stop_hold_ms', Number(el.setHold.value)));

/* ---- screen wake lock ---------------------------------------------------- *
 * A control panel that blanks is a control panel you have to wake up before you
 * can press the one button that rescues a moment already happening. So while
 * this page is visible and the setting is on, the screen stays lit.
 *
 * 🔴 Requires a SECURE CONTEXT. Served over plain http:// on a LAN address this
 * API does not exist at all — same reason the service worker below cannot
 * register and Chrome never offers to install the app. The fix is not code:
 * either put the origin in chrome://flags/#unsafely-treat-insecure-origin-as-secure
 * on the device, or serve TLS. `wakeLockStatus()` says which of those is the
 * case rather than failing silently, because "the screen keeps going off" is
 * otherwise an unfalsifiable complaint.
 *
 * The lock is dropped by the browser on every tab switch and screen-off, so it
 * is re-taken on visibilitychange rather than held once and assumed.
 */
let wakeLock = null;
let wakeWanted = true;
let wakeError = '';

function wakeLockStatus() {
  if (!('wakeLock' in navigator)) {
    return window.isSecureContext
      ? 'not supported by this browser'
      : 'unavailable — this origin is not a secure context';
  }
  if (!wakeWanted) return 'off';
  if (wakeError) return `failed: ${wakeError}`;
  return wakeLock ? 'held' : 'idle';
}

async function syncWakeLock(wanted) {
  if (wanted !== undefined) wakeWanted = !!wanted;
  if (!('wakeLock' in navigator)) return;

  const shouldHold = wakeWanted && document.visibilityState === 'visible';
  if (shouldHold && !wakeLock) {
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeError = '';
      // Released by the system, not by us — clear the handle so the next
      // visibility change re-takes it instead of believing it still holds one.
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch (err) {
      wakeLock = null;
      wakeError = err && err.message ? err.message : 'refused';
    }
  } else if (!shouldHold && wakeLock) {
    try { await wakeLock.release(); } catch { /* already gone */ }
    wakeLock = null;
  }
}

/* ---- fullscreen ---------------------------------------------------------- *
 * The shop monitor is a dedicated screen for this page; browser chrome on it is
 * wasted desk. Kept as an explicit control rather than something the page tries
 * on load, because a page that grabs the whole screen uninvited is the kind of
 * thing you have to fight rather than use.
 */
function fullscreenActive() {
  return !!document.fullscreenElement;
}

function renderFullscreen() {
  const on = fullscreenActive();
  el.fullscreen.setAttribute('aria-pressed', String(on));
  el.fullscreen.setAttribute('aria-label', on ? 'Leave fullscreen' : 'Fullscreen');
  el.fullscreen.title = on ? 'Leave fullscreen (F or Esc)' : 'Fullscreen (F)';
}

async function toggleFullscreen() {
  try {
    if (fullscreenActive()) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
  } catch (err) {
    showAlert('This browser would not go fullscreen: ' +
      (err && err.message ? err.message : 'refused'), 'warn', 5000);
  }
}

el.fullscreen.addEventListener('click', toggleFullscreen);
document.addEventListener('fullscreenchange', renderFullscreen);
renderFullscreen();

/* F for fullscreen. Ignored while typing in the settings sheet, and while any
 * modifier is down, so it cannot collide with a browser shortcut. */
document.addEventListener('keydown', (event) => {
  if (event.key !== 'f' && event.key !== 'F') return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const tag = (event.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
  event.preventDefault();
  toggleFullscreen();
});

/* ---- state stream ------------------------------------------------------- *
 * EventSource reconnects on its own, but only while the page believes the
 * connection died. A phone that slept through a network change can end up with
 * a stream that is open and silent, so the server sends a keepalive and the
 * client re-syncs whenever the tab is shown again.
 */
let source = null;

function connect() {
  if (source) source.close();
  source = new EventSource('/api/events');
  source.onmessage = (event) => {
    try { render(JSON.parse(event.data)); }
    catch (err) { console.error('bad state payload', err); }
  };
  source.onerror = () => {
    el.biStatus.className = 'pill pill--offline';
    el.biStatus.querySelector('.pill-text').textContent = 'NO LINK';
  };
}

document.addEventListener('visibilitychange', () => {
  // The lock is dropped by the system on every screen-off, so it has to be
  // re-taken here rather than held once at load.
  syncWakeLock();
  if (document.visibilityState === 'visible') {
    fetch('/api/state').then((r) => r.json()).then(render).catch(() => {});
    if (!source || source.readyState === EventSource.CLOSED) connect();
  }
});

connect();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* PWA install is optional */ });
}
