(() => {
  'use strict';

  const commandStates = {
    prepare: {
      tabId: 'tab-prepare',
      mode: 'OFFLINE / PREPARE',
      command: 'cli-anything-meerk40t --json --machine sculpfun-s9 job prepare house_a3.svg --out-dir ./output --material kraft-350gsm --allow-estimated',
      output: `{
  "status": "prepared",
  "burn_ready": false,
  "settings": "estimated",
  "artifacts": [
    "house_a3_job.svg",
    "house_a3.gcode",
    "house_a3_manifest.json"
  ]
}`,
      tag: 'ESTIMATED',
      note: 'Useful for inspection and preflight. Calibrate on scrap before a real frame or burn.'
    },
    preflight: {
      tabId: 'tab-preflight',
      mode: 'OFFLINE / PREFLIGHT',
      command: 'cli-anything-meerk40t job preflight ./output/house_a3_manifest.json --allow-estimated',
      output: `{
  "status": "verified",
  "hashes_verified": true,
  "settings_fingerprint": "verified",
  "burn_ready": false,
  "operator_required": false
}`,
      tag: 'VERIFIED',
      note: 'The files still match the manifest. Estimated settings remain inspection-only.'
    },
    hardware: {
      tabId: 'tab-hardware',
      mode: 'OPERATOR / HARDWARE',
      command: 'cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10 device check',
      output: `{
  "status": "connected",
  "firmware": "GRBL 1.1h",
  "controller_state": "Idle",
  "profile": "sculpfun-s9",
  "operator_required": true
}`,
      tag: 'OPERATOR',
      note: 'Wear rated eye protection. Confirm focus, origin, ventilation and placement before framing.'
    }
  };

  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-command]'));
  const panel = document.querySelector('#command-panel');
  const mode = document.querySelector('[data-terminal-mode]');
  const command = document.querySelector('[data-terminal-command]');
  const output = document.querySelector('[data-terminal-output]');
  const note = document.querySelector('[data-terminal-note]');

  function selectCommand(name, focusTab = false) {
    const state = commandStates[name];
    if (!state || !panel || !mode || !command || !output || !note) return;

    tabs.forEach((tab) => {
      const selected = tab.dataset.command === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) tab.focus();
    });

    panel.setAttribute('aria-labelledby', state.tabId);
    panel.dataset.state = name;
    mode.textContent = state.mode;
    command.textContent = state.command;
    output.textContent = state.output;
    note.querySelector('span').textContent = state.tag;
    note.querySelector('p').textContent = state.note;
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectCommand(tab.dataset.command));
    tab.addEventListener('keydown', (event) => {
      let nextIndex = null;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = tabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      selectCommand(tabs[nextIndex].dataset.command, true);
    });
  });

  const toast = document.querySelector('.toast');
  let toastTimer;

  function showToast(message) {
    if (!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    requestAnimationFrame(() => toast.classList.add('is-visible'));
    toastTimer = window.setTimeout(() => {
      toast.classList.remove('is-visible');
      window.setTimeout(() => { toast.hidden = true; }, 180);
    }, 1800);
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('Copy unavailable');
  }

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const original = button.querySelector('span')?.textContent || button.textContent;
      try {
        await copyText(button.dataset.copy || '');
        const label = button.querySelector('span');
        if (label) label.textContent = 'Copied';
        else button.textContent = 'Copied';
        button.classList.add('is-copied');
        showToast('Install command copied');
        window.setTimeout(() => {
          if (label) label.textContent = original;
          else button.textContent = original;
          button.classList.remove('is-copied');
        }, 1600);
      } catch (error) {
        showToast('Copy failed — select the command manually');
      }
    });
  });

  const meter = document.querySelector('.scroll-meter span');
  let meterFrame = 0;

  function updateMeter() {
    meterFrame = 0;
    if (!meter) return;
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    const progress = scrollable > 0 ? Math.min(1, window.scrollY / scrollable) : 0;
    meter.style.transform = `scaleX(${progress})`;
  }

  window.addEventListener('scroll', () => {
    if (meterFrame) return;
    meterFrame = window.requestAnimationFrame(updateMeter);
  }, { passive: true });

  window.addEventListener('resize', updateMeter, { passive: true });
  updateMeter();
})();
