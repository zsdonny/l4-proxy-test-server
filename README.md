# TCP/UDP L4 Proxy Test Server

[![Build and Publish Docker Image](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/docker-publish.yml)
[![Update JSMpeg](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/update-jsmpeg.yml/badge.svg)](https://github.com/zsdonny/l4-proxy-test-server/actions/workflows/update-jsmpeg.yml)

A lightweight test server for verifying **L4 proxy route** configurations. Works with any proxy that supports L4 TCP/UDP routing (e.g. Caddy L4, HAProxy, Envoy). It serves a styled HTML page with connection details (Proxy Protocol v1/v2 or none), and streams **Big Buck Bunny** as a live video via UDP → HTTP chunked stream → browser — all in a single Python script with no pip dependencies.

## Features

- **TCP server** (port `11111`) — **Test this with your L4 TCP proxy.** Accepts plain HTTP or Proxy Protocol v1/v2 prefixed connections. Returns an HTML page showing connection details and a live video player. The `/stream` endpoint serves live MPEG-TS via HTTP chunked transfer.
- **UDP video receiver** (port `22222`) — **Test this with your L4 UDP proxy.** Receives MPEG-TS packets and broadcasts them to all connected HTTP stream clients.
- **Built-in video source** — On startup, FFmpeg (included in the Docker image) streams a pre-transcoded `bigbuckbunny.ts` in a loop using copy mode (no re-encoding). Falls back to downloading from URL if the local file is missing.
- **Proxy Protocol detection** — Automatically distinguishes between no proxy protocol, v1 (text), and v2 (binary) headers.
- **Interactive UDP target** — The page has a text box to redirect where FFmpeg sends its UDP stream. Use the default port for direct playback, or enter a proxied UDP port to verify L4 routing. Status and a live data-flow diagram update in real time.
- **MPEG-TS diagnostics** — Real-time packet rate sparkline graph, continuity counter (CC) error tracking per PID, and datagram size/alignment monitoring to detect proxy-induced corruption.

## Quick Start

### Run with Docker

```bash
docker pull ghcr.io/zsdonny/l4-proxy-test-server:latest
docker run -d --name test-stream -p 11111:11111/tcp -p 22222:22222/udp ghcr.io/zsdonny/l4-proxy-test-server:latest
```

### Run directly with Python

Requires Python 3 (standard library only). FFmpeg must be installed and in PATH for the built-in video source.

```bash
python server.py
```

If FFmpeg is not found, the server still works — you just won’t get automatic video. You can send your own MPEG-TS stream to the UDP port instead.

## Usage

### TCP — Plain HTTP

Open a browser or use `curl`:

```bash
curl http://localhost:11111
```

Returns an HTML page showing **"No Proxy Protocol"** and the remote address of the client.

### TCP — Proxy Protocol v1

Send a Proxy Protocol v1 header followed by any payload:

```bash
echo -e "PROXY TCP4 192.168.1.100 10.0.0.1 12345 80\r\nGET / HTTP/1.1\r\n\r\n" | nc localhost 11111
```

The response page will display the parsed v1 header fields (source/destination IP and port, address family).

### TCP — Proxy Protocol v2

Send a binary Proxy Protocol v2 header. The server detects the 12-byte v2 signature and parses the address block automatically. Any tool or proxy that speaks PP v2 (e.g., with `proxy_protocol` enabled) will work.

### Testing with a Browser

When your L4 proxy is configured to prepend Proxy Protocol headers, you can test directly in a browser. Point the proxy at this test server and simply open the proxied URL in your browser. The server will detect the Proxy Protocol v1 or v2 header injected by the proxy and render an HTML page showing all the parsed connection details.

### UDP — Echo

The UDP port echoes data back to loopback senders for connectivity testing:

```bash
echo "hello" | nc -u localhost 22222
```

> **Note:** Echo is restricted to `127.0.0.1` to prevent feedback loops when traffic arrives via Docker NAT or a proxy.

### UDP — Live Video Stream (Browser)

Every page served by the TCP server (plain, Proxy Protocol v1, or v2) includes a built-in **JSMpeg video player** and a **UDP target port** input.

When running with Docker, Big Buck Bunny streams automatically — just open the page:

1. `docker run` the container (see Quick Start).
2. Open `http://localhost:11111` in a browser (or the proxied URL for PP testing).
3. The video player auto-connects and starts playing. The status shows **"Streaming — direct"**.

#### Testing L4 UDP Routing

To verify that your proxy is correctly routing **UDP** traffic:

1. Configure your L4 proxy to forward a UDP port (e.g., `44444`) to the container’s UDP port (`22222`).
2. Open the page in a browser.
3. In the **FFmpeg UDP Target Port** text box, change the port from `22222` to `44444` (or whatever UDP port your proxy exposes).
4. Click **Send**.

The server restarts FFmpeg to send its video stream to UDP port `44444`. If your proxy is correctly routing that traffic back to the server’s UDP port `22222`, you’ll see:

- **Streaming — via proxy (FFmpeg → 127.0.0.1:44444)** — Video plays, UDP routing works.
- Video stops / packet counter stalls — The proxy isn’t routing UDP to this server, check your L4 config.

The data-flow diagram on the page updates to show exactly what path the traffic is taking:

```
Direct:   FFmpeg → UDP :22222 (direct) → server → HTTP stream → Browser
Proxied:  FFmpeg → UDP :44444 → L4 proxy → UDP :22222 → server → HTTP stream → Browser
```

#### Diagnostics

Below the video player, real-time diagnostics are displayed:

- **Rate (pkt/s)** — A sparkline graph showing the UDP packet rate over the last 60 samples.
- **CC errors** — MPEG-TS continuity counter errors. Should be 0 during normal playback. Non-zero values indicate dropped or reordered packets.
- **Bad dgram** — Datagrams not aligned to 188-byte MPEG-TS boundaries. Non-zero means the proxy is altering packet boundaries.
- **Datagram sizes** — Top 3 datagram sizes by frequency. Direct traffic should show `1316B` (7 × 188).

#### Manual FFmpeg (without Docker or for custom video)

If running without Docker, or if you want to stream your own content:

```bash
# Stream Big Buck Bunny from the internet
ffmpeg -re -stream_loop -1 \
  -i https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4 \
  -f mpegts -codec:v mpeg1video -b:v 800k -bf 0 udp://localhost:22222

# Generate a test pattern (no input file needed)
ffmpeg -re -f lavfi -i testsrc=size=640x480:rate=30 \
  -f mpegts -codec:v mpeg1video -b:v 800k -bf 0 udp://localhost:22222
```

## API Endpoints

| Endpoint              | Method | Description                                                      |
|-----------------------|--------|------------------------------------------------------------------|
| `/`                   | GET    | HTML page with connection details and video player               |
| `/stream`             | GET    | HTTP chunked MPEG-TS stream (used by the embedded video player)  |
| `/api/status`         | GET    | JSON: `udp_packets`, `ffmpeg_target`, `ffmpeg_running`, `stream_clients`, `ts_cc_errors`, `dgram_bad`, `dgram_sizes` |
| `/api/ffmpeg-target`  | POST   | JSON body `{"port": N}` — retargets FFmpeg to a new UDP port     |

## Configuration

| Variable          | Default                  | Description                                                 |
|-------------------|--------------------------|-------------------------------------------------------------|
| `TCP_PORT`        | `11111`                  | TCP listen port — **test this with your L4 TCP proxy**   |
| `UDP_PORT`        | `22222`                  | UDP listen port — **test this with your L4 UDP proxy**   |
| `DOCKER_HOST_ADDR`| `host.docker.internal`   | Address used to reach the Docker host for proxied UDP ports |

## License

- Source code in this repository is licensed under the [MIT License](LICENSE).
- Bundled video asset `bigbuckbunny.ts` (Big Buck Bunny) is © Blender Foundation and distributed under [CC-BY-3.0](https://creativecommons.org/licenses/by/3.0/).
- Bundled `jsmpeg.min.js` ([JSMpeg](https://github.com/phoboslab/jsmpeg)) is © 2017 Dominic Szablewski and distributed under the [MIT License](https://github.com/phoboslab/jsmpeg/blob/master/LICENSE).
