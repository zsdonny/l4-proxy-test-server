# TCP/UDP L4 Proxy Test Server

[![Build and Publish Docker Image](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/docker-publish.yml)
[![Update JSMpeg](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/update-jsmpeg.yml/badge.svg)](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/update-jsmpeg.yml)

A lightweight test server for verifying **L4 proxy route** configurations. Works with any proxy that supports L4 TCP/UDP routing (e.g. Caddy L4, HAProxy, Envoy). Serves a styled HTML page with connection details (Proxy Protocol v1/v2 or none) and streams **Big Buck Bunny** as a live video via UDP `->` HTTP chunked stream `->` browser — all in a single Python script with no pip dependencies.

<img width="633" height="521" alt="image" src="https://github.com/user-attachments/assets/9be50e0f-6c71-4edf-8a82-0f53c2ebd0df" />

<img width="633" height="732" alt="image" src="https://github.com/user-attachments/assets/c361cce7-beaf-4c29-8a95-c4a82577da6a" />

## Features

- **TCP server** (port `11111`) — Accepts plain HTTP or Proxy Protocol v1/v2 prefixed connections. Returns an HTML page with connection details and a live video player.
- **UDP video receiver** (port `22222`) — Receives MPEG-TS packets and broadcasts them to all connected HTTP stream clients.
- **Built-in video source** — FFmpeg (bundled in Docker/exe) streams `bigbuckbunny.ts` in a loop on startup.
- **Proxy Protocol detection** — Automatically distinguishes between no proxy, v1 (text), and v2 (binary) headers.
- **Interactive UDP target** — The page lets you redirect FFmpeg's UDP stream to any IP:port to verify L4 routing end-to-end.
- **MPEG-TS diagnostics** — Real-time packet rate sparkline, CC error tracking, and datagram size/alignment monitoring.

## Quick Start

### Docker

```bash
docker pull ghcr.io/zsdonny/l4-proxy-test-server:latest
docker run -d --name test-stream -p 11111:11111/tcp -p 22222:22222/udp ghcr.io/zsdonny/l4-proxy-test-server:latest
```

### Standalone executable (no Python or Docker required)

Pre-built binaries with FFmpeg, `bigbuckbunny.ts`, and JSMpeg bundled are on the [GitHub Releases](https://github.com/zsdonny/l4-proxy-test-server/releases/latest) page.

**Windows:** Download `L4-Proxy-Test-Server.exe` and run it (or double-click). Accept the SmartScreen prompt if it appears.

**macOS:** Download and unzip `L4-Proxy-Test-Server-macos.zip`. Right-click `->` **Open** to bypass Gatekeeper, or run:

```bash
xattr -d com.apple.quarantine "L4 Proxy Test Server.app"
```

#### Building locally

```bash
bash scripts/build-windows.sh   # Output: dist/L4-Proxy-Test-Server.exe
bash scripts/build-macos.sh     # Output: dist/L4 Proxy Test Server.app
```

Requires Python 3 and PyInstaller. The scripts install PyInstaller and download a static FFmpeg binary automatically.

### Python (no install)

```bash
python server.py
```

Requires Python 3 (standard library only) and FFmpeg in PATH for automatic video. Without FFmpeg the server still runs — send your own MPEG-TS to the UDP port.

## Testing

### TCP

Open a browser at `http://localhost:11111` (or the proxied URL). The page shows whether the connection arrived with no proxy protocol, Proxy Protocol v1, or v2.

```bash
# Plain HTTP
curl http://localhost:11111

# Proxy Protocol v1
echo -e "PROXY TCP4 192.168.1.100 10.0.0.1 12345 80\r\nGET / HTTP/1.1\r\n\r\n" | nc localhost 11111
```

### UDP — Live Video

The page includes a built-in JSMpeg player. Video streams automatically when the container/exe runs.

**To test L4 UDP routing:**

1. Configure your proxy to forward a UDP port (e.g. `44444`) to the server's UDP port (`22222`).
2. On the page, set the **IP** and **Port** fields to the proxy's address/port and click **Send**.
3. FFmpeg restarts targeting the proxy. If routing works, video continues and the flow diagram updates:

```
Direct:   FFmpeg -> UDP :22222 (direct) -> server -> HTTP stream -> Browser
Proxied:  FFmpeg -> UDP 127.0.0.1:44444 -> L4 proxy -> UDP :22222 -> server -> HTTP stream -> Browser
```

**Diagnostics** below the player show packet rate, CC errors (should be 0), and datagram alignment — useful for spotting proxy-induced packet corruption.

#### Manual FFmpeg

```bash
ffmpeg -re -stream_loop -1 \
  -i https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4 \
  -f mpegts -codec:v mpeg1video -b:v 800k -bf 0 udp://localhost:22222
```

## Configuration

| Variable           | Default                | Description                          |
|--------------------|------------------------|--------------------------------------|
| `TCP_PORT`         | `11111`                | TCP listen port                      |
| `UDP_PORT`         | `22222`                | UDP listen port                      |
| `DOCKER_HOST_ADDR` | `host.docker.internal` | Host address for proxied UDP targets |

## License

- Source code: [MIT License](LICENSE)
- `bigbuckbunny.ts`: © Blender Foundation, [CC-BY-3.0](https://creativecommons.org/licenses/by/3.0/)
- `jsmpeg.min.js`: © 2017 Dominic Szablewski, [MIT License](https://github.com/phoboslab/jsmpeg/blob/master/LICENSE)
