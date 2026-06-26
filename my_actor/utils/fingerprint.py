from __future__ import annotations

import json
import random
import re
import subprocess
import time
import uuid

import requests
from crawlee.fingerprint_suite import (
    DefaultFingerprintGenerator,
    HeaderGeneratorOptions,
    ScreenOptions,
)


# ── Single shared generator ───────────────────────────────────────────────────
_generator = DefaultFingerprintGenerator(
    header_options=HeaderGeneratorOptions(
        browsers=["chrome"],           # Chrome-only — Edge/Firefox have different CDP tells
        devices=["desktop"],
        locales=["en-US", "en-GB", "de-DE", "fr-FR", "es-ES"],
    ),
    screen_options=ScreenOptions(
        min_width=1280,
        max_width=2560,
        min_height=720,
        max_height=1440,
    ),
)

# ── Realistic plugin definitions (matches real Chrome exactly) ────────────────
_CHROME_PLUGINS = [
    {
        "name": "PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [
            {"type": "application/pdf", "suffixes": "pdf"},
            {"type": "text/pdf",        "suffixes": "pdf"},
        ],
    },
    {
        "name": "Chrome PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [
            {"type": "application/pdf", "suffixes": "pdf"},
            {"type": "text/pdf",        "suffixes": "pdf"},
        ],
    },
    {
        "name": "Chromium PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [
            {"type": "application/pdf", "suffixes": "pdf"},
            {"type": "text/pdf",        "suffixes": "pdf"},
        ],
    },
    {
        "name": "Microsoft Edge PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [
            {"type": "application/pdf", "suffixes": "pdf"},
            {"type": "text/pdf",        "suffixes": "pdf"},
        ],
    },
    {
        "name": "WebKit built-in PDF",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [
            {"type": "application/pdf", "suffixes": "pdf"},
            {"type": "text/pdf",        "suffixes": "pdf"},
        ],
    },
]

# ── Realistic Windows Chrome UA builder ──────────────────────────────────────
_WIN_CHROME_VERSIONS = [
    ("124", "124.0.6367.82"),
    ("125", "125.0.6422.112"),
    ("126", "126.0.6478.56"),
    ("127", "127.0.6533.88"),
    ("128", "128.0.6613.120"),
    ("129", "129.0.6668.100"),
    ("130", "130.0.6723.91"),
    ("131", "131.0.6778.108"),
    ("132", "132.0.6834.83"),
    ("133", "133.0.6943.53"),
    ("134", "134.0.6998.89"),
]

def _make_windows_ua(major: str, full: str) -> str:
    """
    Build a Windows 10/11 Chrome UA that is consistent with Client Hints.
    Always Win32 platform — never Linux, never Mac.
    """
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{full} Safari/537.36"
    )


# ── Client Hints brand set ────────────────────────────────────────────────────
def _build_client_hints(major: str, full: str) -> dict:   # add full param
    return {
        "brands": [
            {"brand": "Google Chrome",  "version": major},
            {"brand": "Chromium",       "version": major},
            {"brand": "Not/A)Brand",    "version": "99"},
        ],
        "mobile":    False,
        "platform":  "Windows",
        # High-entropy values detectors actually request:
        "uaFullVersion":    full,
        "fullVersionList": [
            {"brand": "Google Chrome",  "version": full},
            {"brand": "Chromium",       "version": full},
            {"brand": "Not/A)Brand",    "version": "99.0.0.0"},
        ],
        "platformVersion": "10.0.0",
        "architecture":    "x86",
        "bitness":         "64",
        "model":           "",
        "wow64":           False,
    }

# ── Accept-Language ───────────────────────────────────────────────────────────
def _build_accept_language(languages: list[str]) -> str:
    parts = []
    for i, lang in enumerate(languages):
        if i == 0:
            parts.append(lang)
        else:
            q = round(1.0 - i * 0.1, 1)
            q = max(q, 0.1)
            parts.append(f"{lang};q={q}")
    return ",".join(parts)


# ── Viewport from screen ──────────────────────────────────────────────────────
def _viewport_from_screen(width: int, height: int) -> dict:
    taskbar = random.randint(40, 48)
    toolbar = random.randint(88, 104)
    outer_h = height - taskbar
    inner_h = outer_h - toolbar
    return {
        "outerWidth":  width,
        "outerHeight": outer_h,
        "innerWidth":  width,
        "innerHeight": max(inner_h, 400),
        "availWidth":  width,
        "availHeight": outer_h,
    }


# ── Fingerprint generator ─────────────────────────────────────────────────────
def generate() -> dict:
    """
    Generate a fully consistent Windows Chrome fingerprint.
    Platform is always Win32 regardless of what the crawlee generator returns —
    this is the #1 OS-mismatch fix.
    """
    fp = _generator.generate()

    # Always Windows — override whatever crawlee generated
    major, full = get_real_chrome_version()
    ua       = _make_windows_ua(major, full)
    platform = "Win32"                          # FIX: was leaking Linux/Mac
    language  = fp.navigator.language or "en-US"
    languages = list(fp.navigator.languages or [language])

    if languages[0] != language:
        languages.insert(0, language)
    languages = languages[:4]

    sw  = fp.screen.width  or 1920
    sh  = fp.screen.height or 1080
    dpr = fp.screen.devicePixelRatio or 1.0

    viewport = _viewport_from_screen(sw, sh)

    raw_headers = dict(fp.headers) if fp.headers else {}
    raw_headers["Accept-Language"] = _build_accept_language(languages)
    raw_headers["User-Agent"]      = ua
    # Sec-CH-UA headers must match UA exactly
    raw_headers["Sec-CH-UA"]                  = (
        f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not/A)Brand";v="99"'
    )
    raw_headers["Sec-CH-UA-Mobile"]           = "?0"
    raw_headers["Sec-CH-UA-Platform"]         = '"Windows"'
    raw_headers["Sec-CH-UA-Platform-Version"] = '"10.0.0"'
    raw_headers["Sec-CH-UA-Arch"]             = '"x86"'
    raw_headers["Sec-CH-UA-Bitness"]          = '"64"'

    for bad in ("x-forwarded-for", "x-real-ip", "via", "forwarded"):
        raw_headers.pop(bad, None)

    return {
        "fingerprint_id": uuid.uuid4().hex,
        "user_agent":           ua,
        "platform":             platform,
        "language":             language,
        "languages":            languages,
        "hardware_concurrency": fp.navigator.hardwareConcurrency or random.choice([4, 8, 12, 16]),
        "device_memory":        fp.navigator.deviceMemory or random.choice([8, 16]),
        "max_touch_points":     0,              # desktop Windows = 0
        "vendor":               "Google Inc.",
        "product_sub":          "20030107",
        "user_agent_data":      _build_client_hints(major, full),
        "chrome_major":         major,
        "screen_width":         sw,
        "screen_height":        sh,
        "avail_width":          viewport["availWidth"],
        "avail_height":         viewport["availHeight"],
        "inner_width":          viewport["innerWidth"],
        "inner_height":         viewport["innerHeight"],
        "outer_width":          viewport["outerWidth"],
        "outer_height":         viewport["outerHeight"],
        "device_pixel_ratio":   dpr,
        "color_depth":          fp.screen.colorDepth or 24,
        "webgl_vendor":         fp.videoCard.vendor   if fp.videoCard else "Intel Inc.",
        "webgl_renderer":       fp.videoCard.renderer if fp.videoCard else "Intel Iris OpenGL Engine",
        "plugins":              _CHROME_PLUGINS,
        "headers":              raw_headers,
    }


# ── JS injection script ───────────────────────────────────────────────────────
def build_js_script(fingerprint: dict) -> str:
    """
    Spoof every fingerprint surface reachable from JS.

    Fixes vs previous version:
    - Canvas noise via overdraw (not getImageData mutation) → no before/after delta
    - OffscreenCanvas patched
    - Audio: getChannelData + copyFromChannel + createAnalyser all patched
    - navigator.webdriver = false (belt-and-suspenders; CDP args do the real work)
    - All Sec-CH-UA / Client Hints headers consistent with Windows UA
    - Platform always Win32
    - window.chrome more complete
    - Automation artefact cleanup expanded
    """
    langs        = json.dumps(fingerprint["languages"])
    plugins_json = json.dumps(fingerprint["plugins"])
    ua_data_json = json.dumps(fingerprint["user_agent_data"])

    # Micro-noise values baked in at generation time — unique per session
    canvas_r = random.randint(-2, 2)
    canvas_g = random.randint(-2, 2)
    canvas_b = random.randint(-2, 2)
    audio_noise = random.uniform(-0.00003, 0.00003)

    return f"""
(function () {{
  'use strict';

  // ── Safe property override ────────────────────────────────────────────────
  const _ov = (obj, prop, val) => {{
    try {{
      Object.defineProperty(obj, prop, {{
        get: () => val,
        configurable: true,
        enumerable: true,
      }});
    }} catch (_) {{}}
  }};
  

  // ── navigator — core ──────────────────────────────────────────────────────
  _ov(navigator, 'platform',            '{fingerprint["platform"]}');
  _ov(navigator, 'hardwareConcurrency', {fingerprint["hardware_concurrency"]});
  _ov(navigator, 'deviceMemory',        {fingerprint["device_memory"]});
  _ov(navigator, 'maxTouchPoints',      {fingerprint["max_touch_points"]});
  _ov(navigator, 'vendor',              '{fingerprint["vendor"]}');
  _ov(navigator, 'productSub',          '{fingerprint["product_sub"]}');


  // ── navigator.languages ───────────────────────────────────────────────────
  _ov(navigator, 'language',  {json.dumps(fingerprint["language"])});
  _ov(navigator, 'languages', {langs});

  // ── navigator.plugins — full PluginArray mock ─────────────────────────────
  const _pluginDefs = {plugins_json};
  const _makeMime = (m, plugin) => {{
    const mime = Object.create(MimeType.prototype);
    _ov(mime, 'type',          m.type);
    _ov(mime, 'suffixes',      m.suffixes);
    _ov(mime, 'description',   m.description || '');
    _ov(mime, 'enabledPlugin', plugin);
    return mime;
  }};
  const _makePlugin = (def) => {{
    const plugin = Object.create(Plugin.prototype);
    _ov(plugin, 'name',        def.name);
    _ov(plugin, 'filename',    def.filename);
    _ov(plugin, 'description', def.description);
    const mimes = def.mimeTypes.map(m => _makeMime(m, plugin));
    mimes.forEach((m, i) => {{ plugin[i] = m; }});
    _ov(plugin, 'length', mimes.length);
    plugin[Symbol.iterator] = function* () {{ yield* mimes; }};
    return plugin;
  }};
  const _plugins = _pluginDefs.map(_makePlugin);
  const _pluginArray = Object.create(PluginArray.prototype);
  _plugins.forEach((p, i) => {{ _pluginArray[i] = p; }});
  _ov(_pluginArray, 'length', _plugins.length);
  _pluginArray[Symbol.iterator] = function* () {{ yield* _plugins; }};
  _pluginArray.item      = (i)    => _plugins[i] || null;
  _pluginArray.namedItem = (name) => _plugins.find(p => p.name === name) || null;
  _pluginArray.refresh   = () => {{}};
  _ov(navigator, 'plugins',   _pluginArray);
  _ov(navigator, 'mimeTypes', new MimeTypeArray());

  // ── screen ────────────────────────────────────────────────────────────────
  _ov(screen, 'width',       {fingerprint["screen_width"]});
  _ov(screen, 'height',      {fingerprint["screen_height"]});
  _ov(screen, 'availWidth',  {fingerprint["avail_width"]});
  _ov(screen, 'availHeight', {fingerprint["avail_height"]});
  _ov(screen, 'colorDepth',  {fingerprint["color_depth"]});
  _ov(screen, 'pixelDepth',  {fingerprint["color_depth"]});
  _ov(window, 'devicePixelRatio', {fingerprint["device_pixel_ratio"]});

  // ── viewport (non-zero — headless tell) ───────────────────────────────────
  _ov(window, 'innerWidth',  {fingerprint["inner_width"]});
  _ov(window, 'innerHeight', {fingerprint["inner_height"]});
  _ov(window, 'outerWidth',  {fingerprint["outer_width"]});
  _ov(window, 'outerHeight', {fingerprint["outer_height"]});

  // ── WebGL ─────────────────────────────────────────────────────────────────
  const _patchWebGL = (ctx) => {{
    if (!ctx) return;
    const _gp = ctx.prototype.getParameter;
    ctx.prototype.getParameter = function (p) {{
      if (p === 37445) return '{fingerprint["webgl_vendor"]}';
      if (p === 37446) return '{fingerprint["webgl_renderer"]}';
      return _gp.call(this, p);
    }};
  }};
  _patchWebGL(WebGLRenderingContext);
  if (typeof WebGL2RenderingContext !== 'undefined') _patchWebGL(WebGL2RenderingContext);

  // ── Canvas noise — overdraw method (no getImageData delta) ───────────────
  //
  // FIX: Old approach read pixels out via getImageData, mutated them, wrote
  // back — detectors compare before/after and see the mutation delta.
  // New approach: overdraw a near-invisible 1×1 rect. No read-back, no delta.
  //
  const _addCanvasNoise = (ctx2d) => {{
    if (!ctx2d || ctx2d.__noised) return;
    ctx2d.__noised = true;
    const prev = ctx2d.globalAlpha;
    const prevOp = ctx2d.globalCompositeOperation;
    ctx2d.globalAlpha = 0.004;
    ctx2d.globalCompositeOperation = 'source-over';
    ctx2d.fillStyle = `rgb(${{128 + {canvas_r}}},${{128 + {canvas_g}}},${{128 + {canvas_b}}})`;
    ctx2d.fillRect(0, 0, 1, 1);
    ctx2d.globalAlpha = prev;
    ctx2d.globalCompositeOperation = prevOp;
    ctx2d.__noised = false;
  }};

  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (...args) {{
    if (this.width > 0 && this.height > 0) _addCanvasNoise(this.getContext('2d'));
    return _toDataURL.apply(this, args);
  }};

  const _toBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function (cb, ...args) {{
    if (this.width > 0 && this.height > 0) _addCanvasNoise(this.getContext('2d'));
    return _toBlob.call(this, cb, ...args);
  }};

  // OffscreenCanvas — FIX: was completely unpatched before
  if (typeof OffscreenCanvas !== 'undefined') {{
    const _ocBlob = OffscreenCanvas.prototype.convertToBlob;
    OffscreenCanvas.prototype.convertToBlob = function (...args) {{
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0) {{
        const prev = ctx.globalAlpha;
        ctx.globalAlpha = 0.003;
        ctx.fillStyle = `rgb(${{128 + {canvas_r}}},${{128 + {canvas_g}}},${{128 + {canvas_b}}})`;
        ctx.fillRect(0, 0, 1, 1);
        ctx.globalAlpha = prev;
      }}
      return _ocBlob.apply(this, args);
    }};
  }}

  // ── Audio fingerprint noise — full surface ────────────────────────────────
  //
  // FIX: Previous version only patched getChannelData. Detectors also probe
  // copyFromChannel and AnalyserNode.getFloatFrequencyData.
  //
  const _audioNoise = {audio_noise};

  const _getChannelData = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function (...args) {{
    const data = _getChannelData.apply(this, args);
    if (data.length > 0) data[0] = Math.max(-1, Math.min(1, data[0] + _audioNoise));
    return data;
  }};

  // FIX: patch copyFromChannel
  const _copyFromChannel = AudioBuffer.prototype.copyFromChannel;
  AudioBuffer.prototype.copyFromChannel = function (dest, channelNum, ...rest) {{
    _copyFromChannel.call(this, dest, channelNum, ...rest);
    if (dest && dest.length > 0) dest[0] = Math.max(-1, Math.min(1, dest[0] + _audioNoise));
  }};

  // FIX: patch AnalyserNode frequency data
  if (typeof AudioContext !== 'undefined') {{
    const _createAnalyser = AudioContext.prototype.createAnalyser;
    AudioContext.prototype.createAnalyser = function () {{
      const node = _createAnalyser.apply(this, arguments);
      const _gffd = node.getFloatFrequencyData.bind(node);
      node.getFloatFrequencyData = function (arr) {{
        _gffd(arr);
        if (arr && arr.length > 0) arr[0] += _audioNoise * 1000; // dB scale
      }};
      return node;
    }};
  }}

  // ── WebRTC — strip STUN/TURN to prevent IP leaks ─────────────────────────
  const _RTCPeer = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (_RTCPeer) {{
    const _Orig = _RTCPeer;
    function _SafeRTC(config, ...rest) {{
      if (config && config.iceServers) config.iceServers = [];
      return new _Orig(config, ...rest);
    }}
    _SafeRTC.prototype = _Orig.prototype;
    Object.defineProperty(_SafeRTC, 'name', {{ value: 'RTCPeerConnection' }});
    window.RTCPeerConnection       = _SafeRTC;
    window.webkitRTCPeerConnection = _SafeRTC;
  }}

  // ── Permissions API — avoid "denied" tell ─────────────────────────────────
  if (navigator.permissions && navigator.permissions.query) {{
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {{
      const granted = ['geolocation', 'notifications', 'camera', 'microphone', 'clipboard-read', 'clipboard-write'];
      if (params && granted.includes(params.name)) {{
        return Promise.resolve({{ state: 'prompt', onchange: null }});
      }}
      return _origQuery(params);
    }};
  }}

  // ── window.chrome — complete mock ─────────────────────────────────────────
  window.chrome = {{
    app: {{
      InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
      RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
      isInstalled: false,
      getDetails:      () => null,
      getIsInstalled:  () => false,
      runningState:    () => 'cannot_run',
    }},
    runtime: {{
      OnInstalledReason: {{
        CHROME_UPDATE: 'chrome_update', INSTALL: 'install',
        SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update',
      }},
      OnRestartRequiredReason: {{
        APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic',
      }},
      PlatformArch: {{
        ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64',
        X86_32: 'x86-32', X86_64: 'x86-64',
      }},
      PlatformNaclArch: {{
        ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64',
      }},
      PlatformOs: {{
        ANDROID: 'android', CROS: 'cros', LINUX: 'linux',
        MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win',
      }},
      RequestUpdateCheckStatus: {{
        NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available',
      }},
      id:          undefined,
      connect:     () => {{}},
      sendMessage: () => {{}},
    }},
    csi:       () => {{}},
    loadTimes: () => ({{
      requestTime:             performance.timeOrigin / 1000,
      startLoadTime:           performance.timeOrigin / 1000,
      commitLoadTime:          performance.timeOrigin / 1000 + 0.1,
      finishDocumentLoadTime:  performance.timeOrigin / 1000 + 0.3,
      finishLoadTime:          performance.timeOrigin / 1000 + 0.5,
      firstPaintTime:          performance.timeOrigin / 1000 + 0.2,
      firstPaintAfterLoadTime: 0,
      navigationType:          'Other',
      wasFetchedViaSpdy:       true,
      wasNpnNegotiated:        true,
      npnNegotiatedProtocol:   'h2',
      wasAlternateProtocolAvailable: false,
      connectionInfo:          'h2',
    }}),
  }};

  // ── Automation artefact cleanup ───────────────────────────────────────────
  const _badKeys = Object.keys(window).filter(k =>
    k.startsWith('cdc_')           ||
    k.startsWith('__webdriver')    ||
    k.startsWith('__driver')       ||
    k.startsWith('__selenium')     ||
    k.startsWith('__nightmare')    ||
    k.startsWith('__puppeteer')    ||
    k === '_Selenium_IDE_Recorder' ||
    k === '__lastWatirAlert'       ||
    k === '__lastWatirConfirm'     ||
    k === '__lastWatirPrompt'      ||
    k === 'domAutomation'          ||
    k === 'domAutomationController'
  );
  _badKeys.forEach(k => {{ try {{ delete window[k]; }} catch (_) {{}} }});

  // ── Error stack trace — hide patchright internals ─────────────────────────
  const _origPrepare = Error.prepareStackTrace;
  if (_origPrepare) {{
    Error.prepareStackTrace = (err, stack) => {{
      const filtered = stack.filter(f => {{
        const src = f.getFileName() || '';
        return !src.includes('patchright') && !src.includes('playwright');
      }});
      return _origPrepare(err, filtered);
    }};
  }}

}})();
"""


# ── Proxy / network helpers ───────────────────────────────────────────────────

def get_timezone_from_ip(ip: str | None = None) -> str:
    try:
        url = f"http://ip-api.com/json/{ip}" if ip else "http://ip-api.com/json"
        data = requests.get(url, timeout=5).json()
        if data.get("status") == "success":
            tz = data.get("timezone", "UTC")
            print(f"[INFO] Timezone: {tz}")
            return tz
    except Exception as e:
        print(f"[WARN] Could not fetch timezone: {e}")
    return "UTC"


def get_proxy_public_ip(ip: str, port: str, user: str, pwd: str) -> str:
    try:
        r = requests.get(
            "https://api.ipify.org",
            proxies={"https": f"http://{user}:{pwd}@{ip}:{port}"},
            timeout=8,
        )
        addr = r.text.strip()
        print(f"[INFO] Proxy public IP: {addr}")
        return addr
    except Exception as e:
        print(f"[WARN] Could not get proxy public IP: {e}")
    return ip


def start_gost_tunnel(
    remote_ip: str, remote_port: str, user: str, pwd: str, local_port: int
) -> subprocess.Popen:
    """
    Spin up a local SOCKS5 tunnel via gost.

    FIX: Use socks5h:// (not http://) for the upstream so DNS is resolved
    remotely — eliminates the DNS leak.  If your proxy only speaks HTTP,
    change the scheme back to http:// and separately fix DNS at the OS level
    (e.g. dnscrypt-proxy or systemd-resolved pointing at a DoH server).
    """
    subprocess.run(
        ["pkill", "-f", f"socks5://127.0.0.1:{local_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    # socks5h:// = remote DNS resolution → no DNS leak
    cmd = [
        "gost",
        "-L", f"socks5://127.0.0.1:{local_port}",
        "-F", f"http://{user}:{pwd}@{remote_ip}:{remote_port}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[INFO] gost tunnel: 127.0.0.1:{local_port} → {remote_ip}:{remote_port} (remote DNS)")
    return proc


# ── WebRTC IP spoof script (proxy mode only) ──────────────────────────────────
def _webrtc_ip_spoof_script(proxy_public_ip: str) -> str:
    return f"""
(() => {{
  const FAKE_IP = "{proxy_public_ip}";
  const OrigRTC = window.RTCPeerConnection;
  if (!OrigRTC) return;

  window.RTCPeerConnection = function (config, constraints) {{
    if (config && config.iceServers) config.iceServers = [];
    const pc = new OrigRTC(config, constraints);
    const _add = pc.addEventListener.bind(pc);
    pc.addEventListener = function (type, handler, ...rest) {{
      if (type !== 'icecandidate') return _add(type, handler, ...rest);
      _add(type, (event) => {{
        if (!event.candidate || !event.candidate.candidate) {{
          handler && handler(event);
          return;
        }}
        const spoofed = event.candidate.candidate.replace(
          /\\b(?:\\d{{1,3}}\\.?){{4}}\\b/g, FAKE_IP
        );
        const fakeCandidate = Object.create(event.candidate);
        Object.defineProperty(fakeCandidate, 'candidate', {{ get: () => spoofed }});
        const fakeEvent = Object.create(event);
        Object.defineProperty(fakeEvent, 'candidate', {{ get: () => fakeCandidate }});
        handler && handler(fakeEvent);
      }}, ...rest);
    }};
    return pc;
  }};
  Object.assign(window.RTCPeerConnection, OrigRTC);
  window.RTCPeerConnection.prototype = OrigRTC.prototype;
  Object.defineProperty(window.RTCPeerConnection, 'name', {{ value: 'RTCPeerConnection' }});
}})();
"""

import subprocess, re
def get_real_chrome_version() -> tuple[str, str]:
    cmds = [
        ["google-chrome", "--version"],
        ["google-chrome-stable", "--version"],
        ["/usr/bin/google-chrome", "--version"],
    ]
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            # FIX: capture full 4-part version like 146.0.7680.164
            m = re.search(r"(\d+)\.(\d+\.\d+\.\d+)", out)
            if m:
                major = m.group(1)
                full  = m.group(1) + "." + m.group(2)
                print(f"[INFO] Real Chrome version: {full}")
                return major, full
        except Exception:
            continue
    return "146", "146.0.7680.164"  # update fallback to match your installed version