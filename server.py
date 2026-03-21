"""
Combined test server for Caddy Proxy Manager L4 route testing.

TCP port 11111  – accepts plain HTTP *or* Proxy Protocol v1/v2 prefixed connections.
                  Returns a styled HTML page showing connection details.
UDP port 22222  – simple echo server (returns whatever it receives).
"""
import html as html_mod
import os
import signal
import socket
import struct
import threading

TCP_PORT = int(os.environ.get("TCP_PORT", "11111"))
UDP_PORT = int(os.environ.get("UDP_PORT", "22222"))

PP2_SIG = b"\x0d\x0a\x0d\x0a\x00\x0d\x0a\x51\x55\x49\x54\x0a"

# ---------------------------------------------------------------------------
# Shared HTML template
# ---------------------------------------------------------------------------

STYLE = """\
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; background: #0b1220; color: #e5eefc; }
  h1 { color: #00d4ff; margin-bottom: 4px; }
  .subtitle { color: #6b7fa3; font-size: 0.85em; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; }
  td, th { border: 1px solid #1e2d44; padding: 8px 14px; text-align: left; }
  th { background: #16213e; color: #00d4ff; width: 160px; }
  td { background: #0f1e36; }
  .tag { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 600; font-size: 0.85em; }
  .tag-none { background: #1a3a2a; color: #4ade80; }
  .tag-v1   { background: #1a2a3a; color: #60a5fa; }
  .tag-v2   { background: #2a1a3a; color: #c084fc; }
  .footer { margin-top: 20px; color: #4a5568; font-size: 0.8em; }"""


def e(v):
    return html_mod.escape(str(v))


def build_page(title, tag_class, tag_text, rows, footer=""):
    row_html = "\n".join(f'  <tr><th>{e(k)}</th><td>{e(v)}</td></tr>' for k, v in rows)
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{STYLE}</style></head><body>
<h1>{e(title)}</h1>
<p class="subtitle">Listening on TCP port {TCP_PORT}</p>
<p><span class="tag {e(tag_class)}">{e(tag_text)}</span></p>
<table>
{row_html}
</table>
<p class="footer">{e(footer)}</p>
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

def http_wrap(body):
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
    ) + body


def response_no_pp(remote):
    rows = [
        ("Proxy Protocol", "None"),
        ("Remote Address", remote),
    ]
    return http_wrap(build_page("TCP Test Server", "tag-none", "No Proxy Protocol", rows))


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
    return http_wrap(build_page("TCP Test Server", tag_class, f"Proxy Protocol {info['version']}", rows, extra))


# ---------------------------------------------------------------------------
# TCP handler (detects PP v1, v2, or plain HTTP)
# ---------------------------------------------------------------------------

def handle_client(conn, addr):
    remote = f"{addr[0]}:{addr[1]}"
    try:
        data = b""
        while len(data) < 512:
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
                remaining = len(data) - needed
                print(f"[tcp] PP v2 from {remote}: {info['src_addr']}:{info['src_port']} -> {info['dst_addr']}:{info['dst_port']}")
                conn.sendall(response_pp(info, remote, f"Remaining payload: {remaining} bytes"))
                return

            # -- Proxy Protocol v1 (text) --
            if len(data) >= 6 and data[:6] == b"PROXY ":
                nl = data.find(b"\n")
                if nl == -1 and len(data) < 108:
                    continue
                line = data[:nl + 1].decode(errors="replace")
                info = parse_v1(line)
                print(f"[tcp] PP v1 from {remote}: {info['src_addr']}:{info['src_port']} -> {info['dst_addr']}:{info['dst_port']}")
                conn.sendall(response_pp(info, remote))
                return

            # -- No proxy protocol (plain HTTP or other) --
            if len(data) >= 6:
                print(f"[tcp] plain connection from {remote}")
                conn.sendall(response_no_pp(remote))
                return

    except Exception as exc:
        print(f"[tcp] error from {remote}: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UDP echo server
# ---------------------------------------------------------------------------

def udp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    print(f"[udp] echo server listening on 0.0.0.0:{UDP_PORT}", flush=True)
    while True:
        data, addr = sock.recvfrom(65535)
        print(f"[udp] echo {len(data)} bytes -> {addr[0]}:{addr[1]}")
        sock.sendto(data, addr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    threading.Thread(target=udp_server, daemon=True).start()

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