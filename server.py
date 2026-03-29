"""
Combined TCP/UDP test server for L4 proxy route testing.
Works with any L4-capable proxy (e.g. Caddy L4, HAProxy, Envoy).

TCP port 11111  – accepts plain HTTP *or* Proxy Protocol v1/v2 prefixed connections.
                  Returns a styled HTML page with connection details and a live video player.
                  Also handles a small JSON API to retarget FFmpeg.
                  The /stream endpoint serves live MPEG-TS via HTTP chunked transfer.
UDP port 22222  – receives MPEG-TS video and broadcasts to HTTP stream clients.

Ports to test with an L4 proxy:  11111 (TCP)  and  22222 (UDP).

On startup, FFmpeg (if available) streams Big Buck Bunny to the local UDP port.
The embedded HTML page lets the user retarget FFmpeg to a proxied UDP port
so the video goes: FFmpeg → L4 UDP proxy → server UDP → HTTP stream → browser.
"""

__version__ = "0.1.0"

import html as html_mod
import json
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

try:
    TCP_PORT = int(os.environ.get("TCP_PORT", "11111"))
except ValueError:
    sys.exit("[error] TCP_PORT must be an integer")
try:
    UDP_PORT = int(os.environ.get("UDP_PORT", "22222"))
except ValueError:
    sys.exit("[error] UDP_PORT must be an integer")


def _runtime_base_dir():
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def _asset_path(name):
    base_dir = _runtime_base_dir()
    candidates = [
        os.path.join(base_dir, 'assets', name),
        os.path.join(base_dir, name),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]

# Big Buck Bunny (c) Blender Foundation | Creative Commons Attribution 3.0
VIDEO_URL = "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4"

PP2_SIG = b"\x0d\x0a\x0d\x0a\x00\x0d\x0a\x51\x55\x49\x54\x0a"

# ---------------------------------------------------------------------------
# Self-hosted JSMpeg library (loaded once at startup)
# ---------------------------------------------------------------------------
_JSMPEG_PATH = _asset_path("jsmpeg.min.js")
try:
    if os.path.exists(_JSMPEG_PATH):
        with open(_JSMPEG_PATH, "rb") as f:
            _JSMPEG_BYTES = f.read()
    else:
        _JSMPEG_BYTES = b""
except Exception as e:
    print(f"[warn] failed to load {_JSMPEG_PATH}: {e}", flush=True)
    _JSMPEG_BYTES = b""

# ---------------------------------------------------------------------------
# HTTP stream client tracking  /  UDP packet counter
# ---------------------------------------------------------------------------
stream_clients = set()
stream_lock = threading.Lock()
udp_pkt_count = 0
udp_pkt_lock = threading.Lock()
_broadcast_q = queue.Queue(maxsize=2000)
_ts_cc_lock = threading.Lock()
_ts_cc_errors = 0   # MPEG-TS continuity counter errors since last retarget
_ts_cc_last = {}    # PID -> last CC value
_dgram_sizes = {}   # size -> count of received datagrams with that size
_dgram_bad = 0      # count of datagrams not aligned to 188 bytes

# ---------------------------------------------------------------------------
# Shared HTML template
# ---------------------------------------------------------------------------

STYLE = """\
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; background: #0b1220; color: #e5eefc; }
  h1 { color: #00d4ff; margin-bottom: 4px; }
  h2 { color: #00d4ff; margin-top: 32px; margin-bottom: 4px; }
  .subtitle { color: #6b7fa3; font-size: 0.85em; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; }
  td, th { border: 1px solid #1e2d44; padding: 8px 14px; text-align: left; }
  th { background: #16213e; color: #00d4ff; width: 160px; }
  td { background: #0f1e36; }
  .tag { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 600; font-size: 0.85em; }
  .tag-none { background: #1a3a2a; color: #4ade80; }
  .tag-v1   { background: #1a2a3a; color: #60a5fa; }
  .tag-v2   { background: #2a1a3a; color: #c084fc; }
  .footer { margin-top: 20px; color: #4a5568; font-size: 0.8em; }
  .video-controls { display: flex; align-items: center; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
  .video-controls label { color: #6b7fa3; font-size: 0.9em; }
  .video-controls input { background: #0f1e36; border: 1px solid #1e2d44; color: #e5eefc;
    padding: 6px 10px; border-radius: 4px; width: 100px; font-size: 0.9em; }
  .video-controls button { background: #00d4ff; color: #0b1220; border: none;
    padding: 6px 16px; border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 0.9em; }
  .video-controls button:hover { background: #00b8e6; }
  .video-controls button:disabled { opacity: .5; cursor: default; }
  #video-status { padding: 6px 12px; border-radius: 4px; font-size: 0.85em; margin: 8px 0; }
  .st-idle { background: #1a2030; color: #6b7fa3; }
  .st-connecting { background: #1a2a3a; color: #60a5fa; }
  .st-connected { background: #1a3a2a; color: #4ade80; }
  .st-error { background: #3a1a1a; color: #f87171; }
    .section-sep { border: 0; border-top: 1px solid #1e2d44; margin: 10px 0 14px; }
    .tcp-subheader { margin: 0 0 4px; }
    .tcp-subheader-note { color: #6b7fa3; font-size: 0.8em; margin: 2px 0 8px; }
    .port-val { color: #00d4ff; font-weight: 700; }
  #video-canvas { width: 100%; aspect-ratio: 16/9; background: #000;
    border: 1px solid #1e2d44; border-radius: 4px; }
  .flow { background: #0f1e36; border: 1px solid #1e2d44; border-radius: 4px;
    padding: 10px 14px; font-family: monospace; font-size: 0.82em; color: #6b7fa3;
    margin: 8px 0; overflow-x: auto; white-space: pre; }
  .flow b { color: #00d4ff; font-weight: 600; }
  #pps-graph { width: 100%; height: 56px; background: #0a1525; border: 1px solid #1e2d44; border-radius: 4px; display: block; margin: 4px 0; }
  .diag-row { display: flex; gap: 20px; margin: 2px 0; font-size: 0.82em; color: #6b7fa3; }
  .diag-val { font-weight: 600; }
  .diag-ok  { color: #4ade80; }
  .diag-warn{ color: #f59e0b; }
  .diag-err { color: #f87171; }"""


def e(v):
    return html_mod.escape(str(v))


# ---------------------------------------------------------------------------
# Video player JS  (placeholders replaced when _VIDEO_JS is built)
# ---------------------------------------------------------------------------
_VIDEO_JS = """
<script src="/jsmpeg.min.js"></script>
<script>
(function() {
  var player       = null;
  var defaultUdp   = __UDP_PORT__;
  var statusEl     = document.getElementById('video-status');
  var canvasEl     = document.getElementById('video-canvas');
  var ipInput      = document.getElementById('udp-target-ip');
  var udpInput     = document.getElementById('udp-target-port');
  var btn          = document.getElementById('retarget-btn');
  var flowEl       = document.getElementById('flow-diagram');
  var pktEl        = document.getElementById('pkt-count');
  var viewerEl     = document.getElementById('viewer-count');
  var graphEl      = document.getElementById('pps-graph');
  var statPpsEl    = document.getElementById('stat-pps');
  var statCcEl     = document.getElementById('stat-cc');
  var statDgramEl  = document.getElementById('stat-dgram');
  var statSizesEl  = document.getElementById('stat-sizes');
  var statSizesRow = document.getElementById('stat-sizes-row');
  var pollTimer    = null;
  var isSending    = false;   // true while a retarget POST is in-flight
  var knownTarget  = null;    // last ffmpeg_target seen from server
  var ppsHistory   = [];      // rolling pps samples for sparkline

  function setStatus(cls, msg) {
    statusEl.className = 'st-' + cls;
    statusEl.textContent = msg;
  }

  function updateFlow(targetIp, targetPort) {
    if (!flowEl) return;
    if (targetPort == defaultUdp && targetIp === '127.0.0.1') {
      flowEl.innerHTML = 'FFmpeg \\u2192 <b>UDP :' + targetPort + '</b> (direct) \\u2192 server \\u2192 HTTP stream \\u2192 Browser';
    } else {
      flowEl.innerHTML = 'FFmpeg \\u2192 <b>UDP ' + targetIp + ':' + targetPort + '</b> \\u2192 L4 proxy \\u2192 UDP :' + defaultUdp + ' \\u2192 server \\u2192 HTTP stream \\u2192 Browser';
    }
  }

  function drawSparkline(hist) {
    if (!graphEl) return;
    var w = graphEl.offsetWidth || graphEl.clientWidth || 580;
    graphEl.width = w;
    var h = graphEl.height || 56;
    var ctx = graphEl.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    if (hist.length < 2) return;
    var max = Math.max.apply(null, hist);
    if (max === 0) return;
    var pad = 3;
    var ch = h - pad * 2;
    function pt(i) {
      return { x: (i / (hist.length - 1)) * w, y: pad + ch - (hist[i] / max) * ch };
    }
    // filled area
    ctx.beginPath();
    for (var i = 0; i < hist.length; i++) { var p = pt(i); if (i===0) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y); }
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = 'rgba(0,212,255,0.08)'; ctx.fill();
    // line
    ctx.beginPath();
    for (var i = 0; i < hist.length; i++) { var p = pt(i); if (i===0) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y); }
    ctx.strokeStyle = '#00d4ff'; ctx.lineWidth = 1.5; ctx.stroke();
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    var lastCount = -1;
    var lastPollMs = Date.now();
    var stallTicks = 0;
    var riseTicks = 0;
    pollTimer = setInterval(function() {
      var nowMs = Date.now();
      fetch('/api/status').then(function(r){ return r.json(); }).then(function(d) {
        // Sync input & flow if another session changed the FFmpeg target
        if (!isSending && knownTarget !== null && d.ffmpeg_target !== knownTarget) {
          var syncParts = d.ffmpeg_target.split(':');
          var syncIp   = syncParts[0];
          var syncPort = parseInt(syncParts[1], 10);
          ipInput.value  = syncIp;
          udpInput.value = syncPort;
          updateFlow(syncIp, syncPort);
          stallTicks = 0;
          riseTicks = 0;
          ppsHistory = [];
        }
        knownTarget = d.ffmpeg_target;

        // pps from packet-count delta
        var dt = (nowMs - lastPollMs) / 1000;
        lastPollMs = nowMs;
        if (lastCount >= 0 && dt > 0) {
          var delta = d.udp_packets - lastCount;
          if (delta >= 0) {
            ppsHistory.push(Math.round(delta / dt));
            if (ppsHistory.length > 60) ppsHistory.shift();
          }
        }
        drawSparkline(ppsHistory);
        if (statPpsEl) statPpsEl.textContent = ppsHistory.length ? ppsHistory[ppsHistory.length - 1] : '\\u2014';

        // CC error counter
        if (statCcEl) {
          var cc = d.ts_cc_errors || 0;
          statCcEl.textContent = cc;
          statCcEl.className = 'diag-val ' + (cc === 0 ? 'diag-ok' : cc < 10 ? 'diag-warn' : 'diag-err');
        }

        // Datagram size check — non-multiples of 188 mean the proxy broke the boundaries
        if (statDgramEl) {
          var bad = d.dgram_bad || 0;
          statDgramEl.textContent = bad;
          statDgramEl.className = 'diag-val ' + (bad === 0 ? 'diag-ok' : 'diag-err');
        }
        if (statSizesEl && d.dgram_sizes && d.dgram_sizes.length) {
          statSizesEl.textContent = d.dgram_sizes.map(function(p) { return p[0] + 'B\\u00d7' + p[1]; }).join('  ');
          if (statSizesRow) statSizesRow.style.display = '';
        }

        // Viewer count badge
        if (viewerEl) {
          if (d.stream_clients > 1) {
            viewerEl.textContent = d.stream_clients + ' viewers sharing this stream \\u2014 FFmpeg target is shared';
            viewerEl.style.display = '';
          } else {
            viewerEl.style.display = 'none';
          }
        }

        var isDirect = d.ffmpeg_target === '127.0.0.1:' + defaultUdp;
        pktEl.textContent = d.udp_packets + ' UDP packets received';
        if (d.udp_packets > 0 && d.udp_packets !== lastCount) {
          stallTicks = 0;
          riseTicks++;

          // Packets flowing but this browser isn't connected to /stream → reconnect player
          if (d.stream_clients === 0) {
            connectPlayer();
          }

          if (isDirect) {
            setStatus('connected', 'Streaming \\u2014 direct (FFmpeg \\u2192 UDP :' + defaultUdp + ')');
          } else if (riseTicks >= 2) {
            setStatus('connected', 'Streaming \\u2014 via UDP :' + d.ffmpeg_target.split(':')[1] + ' (proxy \\u2192 :' + defaultUdp + ')');
          } else {
            setStatus('connecting', 'Checking for proxied packets on :' + d.ffmpeg_target.split(':')[1] + ' \\u2026');
          }
                } else {
                    riseTicks = 0;
                    stallTicks++;
                    if (stallTicks >= 2) {
                        if (!d.ffmpeg_running) {
                            setStatus('error', 'No UDP packets: FFmpeg is not running. Click Send to restart.');
                        } else if (isDirect) {
                            setStatus('error', 'No UDP packets arriving on :' + defaultUdp + '. Click Send to restart stream.');
                        } else {
                            setStatus('error', 'No UDP packets arriving \u2014 is your proxy forwarding UDP :' + d.ffmpeg_target.split(':')[1] + ' \u2192 :' + defaultUdp + '?');
                        }
                    }
        }
        lastCount = d.udp_packets;
      }).catch(function(){});
    }, 2000);
  }

  // Inline Fetch streaming source (JSMpeg CDN build lacks Source.Fetch)
  function FetchStreamSource(url, options) {
    this.url = url;
    this.destination = null;
    this.streaming = true;
    this.completed = false;
    this.established = false;
    this.progress = 0;
    this.onEstablishedCallback = options.onSourceEstablished || null;
    this.onCompletedCallback = options.onSourceCompleted || null;
    this.abortController = null;
  }
  FetchStreamSource.prototype.connect = function(dest) { this.destination = dest; };
  FetchStreamSource.prototype.start = function() {
    var self = this;
    this.abortController = new AbortController();
    fetch(this.url, {signal: this.abortController.signal}).then(function(resp) {
      if (!resp.ok || !resp.body) { self.completed = true; return; }
      self.established = true;
      if (self.onEstablishedCallback) self.onEstablishedCallback(self);
      var reader = resp.body.getReader();
      (function pump() {
        reader.read().then(function(r) {
          if (r.done) { self.completed = true; if (self.onCompletedCallback) self.onCompletedCallback(self); return; }
          if (self.destination) self.destination.write(r.value);
          pump();
        }).catch(function() { self.completed = true; if (self.onCompletedCallback) self.onCompletedCallback(self); });
      })();
    }).catch(function() { self.completed = true; if (self.onCompletedCallback) self.onCompletedCallback(self); });
  };
  FetchStreamSource.prototype.resume = function() {};
  FetchStreamSource.prototype.destroy = function() {
    if (this.abortController) this.abortController.abort();
  };

  function connectPlayer() {
    if (player) { try { player.destroy(); } catch(x){} player = null; }
    if (typeof JSMpeg === 'undefined') {
            setStatus('error', 'JSMpeg failed to load from the bundled asset.');
      return;
    }
    var streamUrl = location.origin + '/stream';
    player = new JSMpeg.Player(streamUrl, {
      source: FetchStreamSource,
      canvas: canvasEl, autoplay: true, audio: false,
      videoBufferSize: 1024 * 1024
    });
    setStatus('connecting', 'Buffering video stream\\u2026');
  }

  window.retargetFFmpeg = function() {
    var ip   = (ipInput ? ipInput.value.trim() : '') || '127.0.0.1';
    var port = parseInt(udpInput.value, 10);
    if (!port || port < 1 || port > 65535) { setStatus('error', 'Invalid port number.'); return; }
    btn.disabled = true;
    isSending = true;
    setStatus('connecting', 'Sending FFmpeg to UDP ' + ip + ':' + port + ' \\u2026');
    updateFlow(ip, port);
    fetch('/api/ffmpeg-target', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ip: ip, port: port})
    })
    .then(function(r){ return r.json(); })
    .then(function(d) {
      btn.disabled = false;
      isSending = false;
      if (d.ok) {
        knownTarget = d.target;  // sync so next poll won't treat this as an external change
        setStatus('connecting', 'FFmpeg retargeted to UDP ' + ip + ':' + port + '. Waiting for packets \\u2026');
        startPolling();
        connectPlayer();
      } else {
        setStatus('error', d.error || 'Failed to retarget FFmpeg.');
      }
    })
    .catch(function(err) {
      btn.disabled = false;
      isSending = false;
      setStatus('error', 'API call failed: ' + err);
    });
  };

  // Init — sync input/flow from server state before first poll
  setStatus('connecting', 'Connecting to video stream \\u2026');
  fetch('/api/status').then(function(r){ return r.json(); }).then(function(d) {
    var parts      = d.ffmpeg_target.split(':');
    var serverIp   = parts[0];
    var serverPort = parseInt(parts[1], 10);
    if (ipInput) ipInput.value = serverIp;
    udpInput.value = serverPort;
    knownTarget = d.ffmpeg_target;
    updateFlow(serverIp, serverPort);
    connectPlayer();
    startPolling();
  }).catch(function() {
    updateFlow('127.0.0.1', defaultUdp);
    connectPlayer();
    startPolling();
  });
})();
</script>
""".replace("__UDP_PORT__", str(UDP_PORT))


def build_page(title, tag_class, tag_text, rows, footer=""):
    row_html = "\n".join(f'  <tr><th>{e(k)}</th><td>{e(v)}</td></tr>' for k, v in rows)
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{STYLE}</style></head><body>
<h1>{e(title)}</h1>
<hr class="section-sep">
<h2 class="tcp-subheader">TCP Protocol</h2>
<p class="tcp-subheader-note">Add Proxy Protocol (PP1 or PP2) on your TCP route to see parsed proxy details instead of the basic non-proxy view.</p>
<p class="subtitle">Listening on TCP port <span class="port-val">{TCP_PORT}</span></p>
<p><span class="tag {e(tag_class)}">{e(tag_text)}</span></p>
<table>
{row_html}
</table>
<p class="footer">{e(footer)}</p>
<h2>UDP Video Stream Demo</h2>
<p class="subtitle">Big Buck Bunny &copy; Blender Foundation &mdash; CC-BY-3.0</p>
<div class="video-controls">
  <label for="udp-target-ip">IP:</label>
  <input type="text" id="udp-target-ip" value="127.0.0.1" placeholder="127.0.0.1" style="width:130px">
  <label for="udp-target-port">Port:</label>
  <input type="number" id="udp-target-port" value="{UDP_PORT}" min="1" max="65535">
  <button id="retarget-btn" onclick="retargetFFmpeg()">Send</button>
</div>
<p class="subtitle" style="margin:4px 0">
    Default <span class="port-val">127.0.0.1:{UDP_PORT}</span> = direct (no proxy).  Enter a proxied
  IP&nbsp;&amp;&nbsp;port to test L4 routing.
</p>
<div id="video-status" class="st-idle">Initializing&hellip;</div>
<div id="flow-diagram" class="flow"></div>
<p id="pkt-count" class="subtitle"></p>
<canvas id="pps-graph" height="56"></canvas>
<div class="diag-row">
  <span>Rate:&nbsp;<span id="stat-pps" class="diag-val">&#x2014;</span>&nbsp;pkt/s</span>
  <span>CC errors:&nbsp;<span id="stat-cc" class="diag-val diag-ok">&#x2014;</span></span>
  <span>Bad dgram:&nbsp;<span id="stat-dgram" class="diag-val diag-ok">&#x2014;</span></span>
</div>
<div class="diag-row" id="stat-sizes-row" style="display:none">
  <span>Datagram sizes:&nbsp;<span id="stat-sizes" class="diag-val" style="color:#e5eefc"></span></span>
</div>
<p id="viewer-count" class="subtitle" style="display:none;color:#f59e0b"></p>
<canvas id="video-canvas"></canvas>
{_VIDEO_JS}
</body></html>"""
    return body.encode()


# ---------------------------------------------------------------------------
# Proxy Protocol parsing
# ---------------------------------------------------------------------------

def parse_v2(buf):
    ver_cmd = buf[12]
    version = (ver_cmd >> 4) & 0x0F
    command = ver_cmd & 0x0F
    fam_proto = buf[13]
    family = (fam_proto >> 4) & 0x0F
    transport = fam_proto & 0x0F
    addr_len = struct.unpack("!H", buf[14:16])[0]
    header_len = 16 + addr_len

    src_addr = dst_addr = ""
    src_port = dst_port = 0
    if family == 1:
        src_addr = socket.inet_ntoa(buf[16:20])
        dst_addr = socket.inet_ntoa(buf[20:24])
        src_port, dst_port = struct.unpack("!HH", buf[24:28])
    elif family == 2:
        src_addr = socket.inet_ntop(socket.AF_INET6, buf[16:32])
        dst_addr = socket.inet_ntop(socket.AF_INET6, buf[32:48])
        src_port, dst_port = struct.unpack("!HH", buf[48:52])

    return {
        "version": f"v2 ({version})",
        "command": "LOCAL" if command == 0 else "PROXY",
        "family": {1: "AF_INET", 2: "AF_INET6"}.get(family, f"unknown({family})"),
        "transport": {1: "STREAM (TCP)", 2: "DGRAM (UDP)"}.get(transport, f"unknown({transport})"),
        "src_addr": src_addr, "dst_addr": dst_addr,
        "src_port": src_port, "dst_port": dst_port,
        "header_len": header_len,
    }


def parse_v1(line):
    parts = line.strip().split(" ")
    return {
        "version": "v1",
        "command": "PROXY",
        "family": parts[1] if len(parts) > 1 else "UNKNOWN",
        "transport": "STREAM (TCP)",
        "src_addr": parts[2] if len(parts) > 2 else "",
        "dst_addr": parts[3] if len(parts) > 3 else "",
        "src_port": int(parts[4]) if len(parts) > 4 else 0,
        "dst_port": int(parts[5]) if len(parts) > 5 else 0,
        "header_len": len(line),
    }


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def http_wrap(body, content_type="text/html; charset=utf-8", status="200 OK"):
    return (
        f"HTTP/1.1 {status}\r\n".encode()
        + f"Content-Type: {content_type}\r\n".encode()
        + b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        + b"Access-Control-Allow-Origin: *\r\n"
        + b"Cache-Control: no-store\r\n"
        + b"Connection: close\r\n\r\n"
    ) + body


def json_response(obj, status="200 OK"):
    body = json.dumps(obj).encode()
    return http_wrap(body, content_type="application/json", status=status)


def response_no_pp(remote):
    rows = [
        ("Proxy Protocol", "None"),
        ("Remote Address", remote),
    ]
    return http_wrap(build_page("TCP/UDP L4 Proxy Test Server", "tag-none", "No Proxy Protocol", rows))


def response_pp(info, remote, extra=""):
    tag_class = "tag-v1" if info["version"] == "v1" else "tag-v2"
    rows = [
        ("Proxy Protocol", info["version"]),
        ("Command", info["command"]),
        ("Address Family", info["family"]),
        ("Transport", info["transport"]),
        ("Source", f"{info['src_addr']}:{info['src_port']}"),
        ("Destination", f"{info['dst_addr']}:{info['dst_port']}"),
        ("Header Size", f"{info['header_len']} bytes"),
        ("Raw Connection", remote),
    ]
    return http_wrap(build_page("TCP/UDP L4 Proxy Test Server", tag_class, f"Proxy Protocol {info['version']}", rows, extra))


# ---------------------------------------------------------------------------
# TCP handler (detects PP v1, v2, or plain HTTP; routes API calls)
# ---------------------------------------------------------------------------

def parse_http_request(data):
    """Return (method, path, body) from raw HTTP data, or None."""
    try:
        text = data.decode(errors="replace")
        m = re.match(r"(GET|POST|PUT|DELETE|OPTIONS)\s+(\S+)\s+HTTP/", text)
        if not m:
            return None
        method, path = m.group(1), m.group(2)
        body = ""
        idx = text.find("\r\n\r\n")
        if idx != -1:
            body = text[idx + 4:]
        return method, path, body
    except Exception:
        return None


def handle_static(conn, method, path):
    """Serve self-hosted static assets. Returns True if handled."""
    if path == "/jsmpeg.min.js" and method == "GET":
        conn.sendall(http_wrap(_JSMPEG_BYTES, content_type="application/javascript"))
        return True
    return False


def handle_api(conn, method, path, body):
    """Handle API endpoints. Returns True if handled."""
    if path == "/api/ffmpeg-target" and method == "POST":
        try:
            obj = json.loads(body)
            port = int(obj.get("port", 0))
            ip = obj.get("ip", None)
            if port < 1 or port > 65535:
                conn.sendall(json_response({"ok": False, "error": "Invalid port"}, "400 Bad Request"))
                return True
            if ip is not None:
                # Validate: allow IPv4 dotted-decimal or plain hostname (letters, digits, dots, hyphens)
                if not re.match(r'^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$', ip):
                    conn.sendall(json_response({"ok": False, "error": "Invalid IP/host"}, "400 Bad Request"))
                    return True
            ok, msg = ffmpeg_retarget(port, ip)
            conn.sendall(json_response({"ok": ok, "target": msg}))
        except Exception as exc:
            conn.sendall(json_response({"ok": False, "error": str(exc)}, "400 Bad Request"))
        return True

    if path == "/api/status" and method == "GET":
        with udp_pkt_lock:
            count = udp_pkt_count
        with stream_lock:
            clients = len(stream_clients)
        with _ts_cc_lock:
            cc_errors = _ts_cc_errors
            dgram_sizes = dict(_dgram_sizes)
            dgram_bad = _dgram_bad
        # summarise: top 3 sizes by frequency
        top_sizes = sorted(dgram_sizes.items(), key=lambda kv: -kv[1])[:3]
        conn.sendall(json_response({
            "udp_packets": count,
            "ffmpeg_target": ffmpeg_mgr.target,
            "ffmpeg_running": ffmpeg_mgr.proc is not None and ffmpeg_mgr.proc.poll() is None,
            "stream_clients": clients,
            "ts_cc_errors": cc_errors,
            "dgram_bad": dgram_bad,
            "dgram_sizes": [[s, c] for s, c in top_sizes],
        }))
        return True

    return False


def handle_client(conn, addr):
    remote = f"{addr[0]}:{addr[1]}"
    stream_handoff = False
    try:
        conn.settimeout(10)
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk

            # -- Proxy Protocol v2 (binary) --
            if len(data) >= 16 and data[:12] == PP2_SIG:
                addr_len = struct.unpack("!H", data[14:16])[0]
                needed = 16 + addr_len
                if len(data) < needed:
                    continue
                info = parse_v2(data)
                remaining_data = data[needed:]
                remaining = len(remaining_data)
                print(f"[tcp] PP v2 from {remote}: {info['src_addr']}:{info['src_port']} -> {info['dst_addr']}:{info['dst_port']}")
                # Check if the remaining payload is an API or stream request
                parsed = parse_http_request(remaining_data) if remaining_data else None
                if parsed and parsed[1] == "/stream":
                    stream_handoff = True
                    handle_stream_client(conn, addr)
                elif parsed and handle_static(conn, parsed[0], parsed[1]):
                    pass
                elif parsed and parsed[1].startswith("/api/"):
                    handle_api(conn, parsed[0], parsed[1], parsed[2])
                else:
                    conn.sendall(response_pp(info, remote, f"Remaining payload: {remaining} bytes"))
                return

            # -- Proxy Protocol v1 (text) --
            if len(data) >= 6 and data[:6] == b"PROXY ":
                nl = data.find(b"\n")
                if nl == -1 and len(data) < 108:
                    continue
                line = data[:nl + 1].decode(errors="replace")
                info = parse_v1(line)
                remaining_data = data[nl + 1:]
                print(f"[tcp] PP v1 from {remote}: {info['src_addr']}:{info['src_port']} -> {info['dst_addr']}:{info['dst_port']}")
                parsed = parse_http_request(remaining_data) if remaining_data else None
                if parsed and parsed[1] == "/stream":
                    stream_handoff = True
                    handle_stream_client(conn, addr)
                elif parsed and handle_static(conn, parsed[0], parsed[1]):
                    pass
                elif parsed and parsed[1].startswith("/api/"):
                    handle_api(conn, parsed[0], parsed[1], parsed[2])
                else:
                    conn.sendall(response_pp(info, remote))
                return

            # -- No proxy protocol (plain HTTP or other) --
            if len(data) >= 6:
                parsed = parse_http_request(data)
                if parsed and parsed[1] == "/stream":
                    stream_handoff = True
                    handle_stream_client(conn, addr)
                elif parsed and handle_static(conn, parsed[0], parsed[1]):
                    pass
                elif parsed and parsed[1].startswith("/api/"):
                    print(f"[tcp] API {parsed[0]} {parsed[1]} from {remote}")
                    handle_api(conn, parsed[0], parsed[1], parsed[2])
                else:
                    print(f"[tcp] plain connection from {remote}")
                    conn.sendall(response_no_pp(remote))
                return

    except Exception as exc:
        print(f"[tcp] error from {remote}: {exc}")
    finally:
        if not stream_handoff:
            conn.close()


# ---------------------------------------------------------------------------
# HTTP chunked stream relay (UDP video → browser via fetch + ReadableStream)
# ---------------------------------------------------------------------------

def handle_stream_client(conn, addr):
    """Send HTTP chunked stream headers, then keep connection open for MPEG-TS data."""
    try:
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: video/MP2T\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Cache-Control: no-store\r\n"
            "\r\n"
        )
        conn.sendall(headers.encode())

        with stream_lock:
            stream_clients.add(conn)
        print(f"[stream] client connected: {addr[0]}:{addr[1]}", flush=True)

        # Keep connection alive — wait for client disconnect
        conn.settimeout(1)
        while True:
            try:
                data = conn.recv(1)
                if not data:
                    break
            except socket.timeout:
                continue
            except Exception:
                break
    except Exception as exc:
        print(f"[stream] error from {addr[0]}:{addr[1]}: {exc}", flush=True)
    finally:
        with stream_lock:
            stream_clients.discard(conn)
        try:
            conn.close()
        except Exception:
            pass
        print(f"[stream] client disconnected: {addr[0]}:{addr[1]}")


_stream_broadcast_log_count = 0


def broadcast_to_stream(data):
    """Forward a UDP packet to every connected HTTP stream client as a chunked chunk."""
    global _stream_broadcast_log_count
    with stream_lock:
        n = len(stream_clients)
        if n > 0 and _stream_broadcast_log_count < 5:
            print(f"[stream] broadcasting {len(data)} bytes to {n} client(s)", flush=True)
            _stream_broadcast_log_count += 1
        dead = []
        chunk = f"{len(data):x}\r\n".encode() + data + b"\r\n"
        for client in stream_clients:
            try:
                client.settimeout(0.1)
                client.sendall(chunk)
            except Exception:
                dead.append(client)
        for c in dead:
            stream_clients.discard(c)
            try:
                c.close()
            except Exception:
                pass


def _broadcast_worker():
    """Dedicated thread: reads from queue and forwards to HTTP stream clients."""
    while True:
        data = _broadcast_q.get()
        broadcast_to_stream(data)


# ---------------------------------------------------------------------------
# UDP video receiver (+ echo)
# ---------------------------------------------------------------------------

def _check_ts_cc(data):
    """Scan MPEG-TS packets in a UDP datagram, count continuity counter errors."""
    global _ts_cc_errors, _ts_cc_last
    offset = 0
    with _ts_cc_lock:
        while offset + 188 <= len(data):
            if data[offset] == 0x47:
                pid = ((data[offset + 1] & 0x1F) << 8) | data[offset + 2]
                if pid != 0x1FFF:  # skip null packets
                    afc = (data[offset + 3] >> 4) & 0x03
                    cc  = data[offset + 3] & 0x0F
                    if afc & 0x01:  # payload present — CC should increment
                        if pid in _ts_cc_last:
                            expected = (_ts_cc_last[pid] + 1) & 0x0F
                            if cc != expected:
                                _ts_cc_errors += 1
                        _ts_cc_last[pid] = cc
            offset += 188


def udp_server():
    global udp_pkt_count
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    print(f"[udp] video relay on 0.0.0.0:{UDP_PORT}", flush=True)
    while True:
        data, addr = sock.recvfrom(65535)
        if not ffmpeg_mgr.accept_packets:
            continue  # discard stale packets during retarget transition

        # Strip Proxy Protocol v2 header if a proxy prepended one to the UDP datagram.
        # PP2 header = 12-byte sig + 4 fixed bytes + variable addr block.
        # For IPv4 DGRAM (the common case) that's exactly 28 bytes.
        # NOTE: stripping disabled — kept for reference so users can see raw PP2 in diagnostics.
        # if len(data) >= 16 and data[:12] == PP2_SIG:
        #     addr_len = struct.unpack("!H", data[14:16])[0]
        #     pp2_len = 16 + addr_len
        #     if len(data) > pp2_len:
        #         data = data[pp2_len:]
        #         print(f"[udp] stripped {pp2_len}-byte PP2 header from datagram", flush=True)

        with udp_pkt_lock:
            udp_pkt_count += 1
        # Track datagram sizes to detect proxy boundary alteration
        global _dgram_bad
        sz = len(data)
        with _ts_cc_lock:
            _dgram_sizes[sz] = _dgram_sizes.get(sz, 0) + 1
            if sz % 188 != 0:
                _dgram_bad += 1
        _check_ts_cc(data)
        # Only echo to loopback senders (local FFmpeg); don't echo to
        # external/proxied senders to avoid feedback loops via Docker NAT.
        if addr[0] == "127.0.0.1":
            sock.sendto(data, addr)
        try:
            _broadcast_q.put_nowait(data)
        except queue.Full:
            pass  # drop if broadcast can't keep up


# ---------------------------------------------------------------------------
# FFmpeg manager: start / retarget
# ---------------------------------------------------------------------------

# Detect deployment context: Docker (host.docker.internal) or desktop/local (127.0.0.1)
# For proxied UDP ports targeting the local machine, this controls where FFmpeg sends packets
def _detect_docker_context():
    """Detect if running in Docker. Falls back to localhost for desktop deployments."""
    # Check for /.dockerenv file (Docker containers have this)
    if os.path.exists("/.dockerenv"):
        return os.environ.get("DOCKER_HOST_ADDR", "host.docker.internal")
    # Not in Docker: use loopback for all targets
    return "127.0.0.1"

DOCKER_HOST = _detect_docker_context()

# Video asset path: Try bundled .ts file first, fall back to downloading
VIDEO_LOCAL = _asset_path("bigbuckbunny.ts")

def _find_ffmpeg():
    """Return path to ffmpeg binary, preferring the one bundled by PyInstaller."""
    if getattr(sys, 'frozen', False):
        # Running inside a PyInstaller bundle — check the extraction directory first.
        ext = '.exe' if sys.platform == 'win32' else ''
        bundled = os.path.join(sys._MEIPASS, f'ffmpeg{ext}')
        if os.path.exists(bundled):
            return bundled
    return shutil.which('ffmpeg')


class FFmpegManager:
    def __init__(self):
        self.proc = None
        self.target = f"127.0.0.1:{UDP_PORT}"
        self.lock = threading.Lock()
        self._ffmpeg_bin = _find_ffmpeg()
        self.available = self._ffmpeg_bin is not None
        self._local_ready = False
        self._generation = 0        # bumped on retarget; stale threads check & exit
        self.accept_packets = True   # gate checked by udp_server

    def _check_local(self):
        """Check if pre-transcoded .ts file exists (baked into image)."""
        if os.path.exists(VIDEO_LOCAL):
            self._local_ready = True
            print(f"[ffmpeg] using pre-transcoded {VIDEO_LOCAL}", flush=True)
            return True
        print(f"[ffmpeg] {VIDEO_LOCAL} not found, will stream from URL", flush=True)
        return False

    def _build_cmd(self):
        if self._local_ready:
            # Loop pre-transcoded MPEG-TS with copy — no re-encoding, clean loop
            return [
                self._ffmpeg_bin, "-loglevel", "warning",
                "-re",
                "-i", VIDEO_LOCAL,
                "-f", "mpegts",
                "-mpegts_flags", "resend_headers",
                "-c", "copy",
                f"udp://{self.target}?pkt_size=1316",
            ]
        return [
            self._ffmpeg_bin, "-loglevel", "warning",
            "-re",
            "-i", VIDEO_URL,
            "-f", "mpegts",
            "-mpegts_flags", "resend_headers",
            "-codec:v", "mpeg1video", "-b:v", "800k", "-bf", "0",
            "-g", "30",
            f"udp://{self.target}?pkt_size=1316",
        ]

    def _kill_all_ffmpeg(self):
        """Kill every ffmpeg process in the container (catches orphans)."""
        if not os.path.isdir("/proc"):
            return
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    if b"ffmpeg" in f.read():
                        os.kill(int(entry), signal.SIGKILL)
            except (OSError, ProcessLookupError, PermissionError):
                pass

    def start(self):
        if not self.available:
            print("[ffmpeg] not found in PATH — no built-in video source.", flush=True)
            print(f"[ffmpeg] Install ffmpeg or manually send MPEG-TS to udp://localhost:{UDP_PORT}", flush=True)
            return
        self._check_local()
        self._spawn()

    def _spawn(self):
        gen = self._generation  # capture generation for this thread
        def run():
            while True:
                with self.lock:
                    if self._generation != gen:
                        return  # superseded by a retarget
                cmd = self._build_cmd()
                print(f"[ffmpeg] [gen={gen}] streaming -> udp://{self.target}", flush=True)
                try:
                    with self.lock:
                        if self._generation != gen:
                            return
                        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    proc = self.proc
                    for line in proc.stderr:
                        print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}", flush=True)
                    proc.wait()
                except Exception as exc:
                    print(f"[ffmpeg] error: {exc}", flush=True)
                with self.lock:
                    if self._generation != gen:
                        return  # superseded
                time.sleep(1)
        threading.Thread(target=run, daemon=True).start()

    def retarget(self, port, ip=None):
        """Kill current FFmpeg and restart aiming at a new UDP host:port."""
        global udp_pkt_count, _ts_cc_errors, _dgram_bad, _stream_broadcast_log_count
        if not self.available:
            return False, "FFmpeg not available"

        # 1. Close the packet gate so udp_server discards stale packets
        self.accept_packets = False

        # 2. Bump generation so ALL old spawn threads exit
        with self.lock:
            self._generation += 1
            if ip is not None:
                # Explicit IP supplied by caller (e.g. from the web UI)
                self.target = f"{ip}:{port}"
            elif port == UDP_PORT:
                # Direct (default port) → loopback
                self.target = f"127.0.0.1:{port}"
            else:
                # Proxy test → Docker host (or loopback on desktop)
                self.target = f"{DOCKER_HOST}:{port}"
            if self.proc:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
                self.proc = None

        # 3. Kill ALL ffmpeg processes (catches orphans from race conditions)
        self._kill_all_ffmpeg()

        # 4. Wait for kernel UDP socket buffer to drain
        time.sleep(1.0)

        # 5. Reset counter, CC state, and flush broadcast queue
        with udp_pkt_lock:
            udp_pkt_count = 0
        _stream_broadcast_log_count = 0
        with _ts_cc_lock:
            _ts_cc_errors = 0
            _ts_cc_last.clear()
            _dgram_sizes.clear()
            _dgram_bad = 0
        while True:
            try:
                _broadcast_q.get_nowait()
            except queue.Empty:
                break

        # 6. Disconnect all HTTP stream clients so browsers reconnect fresh
        with stream_lock:
            for c in list(stream_clients):
                try:
                    c.close()
                except Exception:
                    pass
            stream_clients.clear()

        # 7. Open gate and spawn fresh
        self.accept_packets = True
        self._spawn()
        return True, self.target


ffmpeg_mgr = FFmpegManager()


def ffmpeg_retarget(port, ip=None):
    return ffmpeg_mgr.retarget(port, ip)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    threading.Thread(target=udp_server, daemon=True).start()
    threading.Thread(target=_broadcast_worker, daemon=True).start()
    ffmpeg_mgr.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", TCP_PORT))
    srv.listen(32)
    print(f"[tcp] test server listening on 0.0.0.0:{TCP_PORT}", flush=True)

    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()