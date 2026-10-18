"""Sunucuyu hem IPv4 hem IPv6 bağlantılarını kabul edecek şekilde başlatır.

uvicorn --host :: yalnız IPv6, --host 0.0.0.0 yalnız IPv4 dinler. Railway'in genel
ağı ve iç ağı farklı aileleri kullanabildiği için tek bir çift yığınlı soket açıyoruz.
"""

import os
import socket

import uvicorn


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        family = "IPv4 + IPv6"
    except OSError:  # IPv6 desteklenmeyen ortam
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        family = "IPv4"
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("::" if sock.family == socket.AF_INET6 else "0.0.0.0", port))
    config = uvicorn.Config("app.main:app", proxy_headers=True, forwarded_allow_ips="*")
    print(f"BOLBÉ paneli {port} portunda dinliyor ({family})", flush=True)
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
