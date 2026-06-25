#!/usr/bin/env python3
"""
Container de carga sob demanda.
A carga e disparada quando uma requisicao HTTP chega (nao no startup),
e o trabalho pesado e feito pelo stress-ng. A resposta so retorna quando
a carga termina (bom pra ter feedback claro do tempo gasto).

Rotas:
  GET /cpu?cpus=N&seconds=S       -> carga de CPU em N cores por S segundos
  GET /mem?bytes=512M&seconds=S   -> aloca/martela memoria por S segundos
  GET /io?workers=N&seconds=S     -> carga de I/O
  GET /slow?ms=2000               -> apenas latencia (sleep), sem carga
  GET /health                     -> 200 rapido (use no healthcheck)
  GET /                           -> ajuda

Variaveis de ambiente:
  PORT            porta HTTP (default: 8080)
  MAX_SECONDS     teto de duracao por requisicao (default: 300)
  MAX_CONCURRENT  maximo de cargas simultaneas via semaforo (default: 4)
"""
import os
import re
import threading
import time
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CPUS = os.cpu_count() or 1
MAX_SECONDS = int(os.getenv("MAX_SECONDS", "300"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))

_sem = threading.Semaphore(MAX_CONCURRENT)
_MEM_RE = re.compile(r"^\d+[KMGkmg]?$")

HELP = (
    "Container de carga sob demanda (stress-ng)\n\n"
    "GET /cpu?cpus=N&seconds=S       carga de CPU\n"
    "GET /mem?bytes=512M&seconds=S   carga de memoria\n"
    "GET /io?workers=N&seconds=S     carga de I/O\n"
    "GET /slow?ms=2000               latencia (sem carga)\n"
    "GET /health                     200 rapido\n"
    f"\ncores disponiveis: {CPUS}"
    f"  max_concurrent: {MAX_CONCURRENT}"
    f"  max_seconds: {MAX_SECONDS}\n"
)


def _int(value, lo, hi, name):
    try:
        v = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name} deve ser inteiro, recebeu: {value!r}")
    if not lo <= v <= hi:
        raise ValueError(f"{name} fora do intervalo [{lo}, {hi}], recebeu: {v}")
    return v


def _mem_bytes(value):
    if not _MEM_RE.match(value):
        raise ValueError(f"bytes invalido: {value!r} (ex: 256M, 1G, 512K)")
    return value


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def run_stress(self, args, desc):
        if not _sem.acquire(blocking=True, timeout=MAX_SECONDS):
            return self.reply(503, f"timeout aguardando slot livre (max_concurrent={MAX_CONCURRENT})\n")
        t0 = time.time()
        try:
            subprocess.run(
                ["stress-ng", "--temp-path", "/tmp", *args],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=MAX_SECONDS + 5,
            )
            self.reply(200, f"{desc} -> concluido em {time.time() - t0:.2f}s\n")
        except subprocess.CalledProcessError as e:
            msg = e.stderr.decode(errors="ignore")[:500]
            self.reply(500, f"stress-ng erro: {msg}\n")
        except subprocess.TimeoutExpired:
            self.reply(500, "stress-ng excedeu o timeout\n")
        finally:
            _sem.release()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"

        def arg(key, default):
            return q.get(key, [default])[0]

        if path == "/health":
            return self.reply(200, "ok\n")

        if path == "/":
            return self.reply(200, HELP)

        try:
            if path == "/cpu":
                cpus = _int(arg("cpus", str(CPUS)), 1, CPUS * 4, "cpus")
                secs = _int(arg("seconds", "10"), 1, MAX_SECONDS, "seconds")
                return self.run_stress(
                    ["--cpu", str(cpus), "--timeout", f"{secs}s"],
                    f"cpu {cpus} cores x {secs}s",
                )

            if path == "/mem":
                size = _mem_bytes(arg("bytes", "256M"))
                secs = _int(arg("seconds", "10"), 1, MAX_SECONDS, "seconds")
                return self.run_stress(
                    ["--vm", "1", "--vm-bytes", size, "--timeout", f"{secs}s"],
                    f"mem {size} x {secs}s",
                )

            if path == "/io":
                workers = _int(arg("workers", "2"), 1, 32, "workers")
                secs = _int(arg("seconds", "10"), 1, MAX_SECONDS, "seconds")
                return self.run_stress(
                    ["--io", str(workers), "--timeout", f"{secs}s"],
                    f"io {workers} workers x {secs}s",
                )

            if path == "/slow":
                ms = _int(arg("ms", "2000"), 1, MAX_SECONDS * 1000, "ms")
                time.sleep(ms / 1000.0)
                return self.reply(200, f"resposta apos {ms}ms\n")

        except ValueError as exc:
            return self.reply(400, f"parametro invalido: {exc}\n")

        return self.reply(404, HELP)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    print(f"ouvindo em :{port}  ({CPUS} cores disponiveis)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
