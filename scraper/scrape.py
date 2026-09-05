#!/usr/bin/env python3
"""
Extrae los titulares del bloque principal de es.finance.yahoo.com
y los guarda en data/noticias.json.

Pensado para ejecutarse desde GitHub Actions con un cron.
Los titulares se republican reformulados; el original queda como
referencia en titulo_fuente y el enlace apunta a la nota.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://es.finance.yahoo.com/"

SECCIONES = {
    "Portada": {"url": BASE},
}

MAX_POR_SECCION = 60
DIAS_RETENCION = 30

# --- Reescritura de titulares -------------------------------------------
# No se publica el titular literal de La República: se guarda como
# referencia y se muestra una redacción propia enlazada al original.
API_MODELO = "gemini-3.5-flash-lite"
API_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{API_MODELO}:generateContent")
API_CLAVE = os.environ.get("GEMINI_API_KEY", "")
LOTE = 5         # titulares por llamada
REINTENTOS = 4   # ante 429/503, que son transitorios

INSTRUCCION = """Reformula cada titular de prensa económica colombiana con
palabras distintas, conservando exactamente el mismo significado.

Reglas estrictas:
- No agregues ningún dato, cifra, nombre o matiz que no esté en el original.
- No omitas cifras ni nombres que sí estén.
- Nada de adjetivos valorativos, interpretaciones ni opiniones.
- Máximo 110 caracteres. Español de Colombia. Sin comillas ni punto final.
- Si un titular no se puede reformular sin cambiar el sentido, devuélvelo igual.

Responde ÚNICAMENTE con un arreglo JSON de cadenas, en el mismo orden y con
la misma cantidad de elementos que recibiste. Sin explicaciones ni markdown."""

SALIDA = Path(__file__).resolve().parent.parent / "data" / "noticias.json"
BOGOTA = timezone(timedelta(hours=-5))

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-CO,es;q=0.9",
}


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip()


def descargar(url: str) -> str:
    r = requests.get(url, headers=CABECERAS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def extraer(html: str) -> list:
    """Recorre el bloque principal y devuelve titular, enlace y medio.

    Yahoo agrupa cada nota en un <a> que envuelve el <h2> o <h3> del
    titular. El medio que la firma está en el .publishing del contenedor.
    """
    sopa = BeautifulSoup(html, "lxml")
    zona = sopa.select_one("section.module-hero") or sopa
    items = []
    urls_vistas = set()

    for enlace in zona.select("a[href]"):
        encabezado = enlace.select_one("h2, h3")
        if not encabezado:
            continue

        titulo = limpiar(encabezado.get_text())
        href = enlace.get("href") or ""
        if not titulo or not href or href.startswith("/quote/"):
            continue

        url = urljoin(BASE, href)
        if url in urls_vistas:
            continue
        urls_vistas.add(url)

        caja = enlace.find_parent(["section", "div"])
        medio = ""
        for _ in range(4):
            if caja is None:
                break
            firma = caja.select_one(".publishing")
            if firma:
                medio = limpiar(firma.get_text()).split("•")[0].strip()
                break
            caja = caja.parent

        items.append({
            "titulo": titulo,
            "url": url,
            "resumen": "",
            "medio": medio,
        })

    return items


def diagnostico(html: str) -> None:
    sopa = BeautifulSoup(html, "lxml")
    print(f"  diagnóstico: {len(html)} bytes, "
          f"{len(sopa.select('section.module-hero'))} bloques hero, "
          f"{len(sopa.select('h3'))} h3", file=sys.stderr)


def adaptar(titulares: list) -> list:
    """Devuelve una redacción propia de cada titular, o [] si no se pudo.

    Se llama solo con los titulares nuevos de la corrida, así que son unos
    pocos por hora. Si falla, el llamador conserva los que ya tenía y vuelve
    a intentar en la siguiente ejecución.
    """
    if not titulares:
        return []
    if not API_CLAVE:
        print("  falta GEMINI_API_KEY: no se adaptan titulares", file=sys.stderr)
        return []

    salida = []
    for i in range(0, len(titulares), LOTE):
        trozo = titulares[i:i + LOTE]
        cuerpo = {
            "system_instruction": {"parts": [{"text": INSTRUCCION}]},
            "contents": [{
                "role": "user",
                "parts": [{"text": json.dumps(trozo, ensure_ascii=False)}],
            }],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
        adaptados = None
        for intento in range(1, REINTENTOS + 1):
            try:
                r = requests.post(
                    API_URL,
                    headers={
                        "x-goog-api-key": API_CLAVE,
                        "content-type": "application/json",
                    },
                    json=cuerpo,
                    timeout=90,
                )
                # 429 (cuota) y 5xx (saturación) son transitorios: se reintenta.
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()

                partes = r.json()["candidates"][0]["content"]["parts"]
                texto = "".join(x.get("text", "") for x in partes)
                texto = re.sub(r"^```(?:json)?|```$", "", texto.strip()).strip()
                adaptados = json.loads(texto)
                break

            except requests.HTTPError as e:
                codigo = e.response.status_code if e.response is not None else 0
                if codigo not in (429,) and codigo < 500:
                    print(f"  no se pudieron adaptar los titulares ({e})",
                          file=sys.stderr)
                    return []
                if intento == REINTENTOS:
                    print(f"  no se pudieron adaptar los titulares tras "
                          f"{REINTENTOS} intentos ({e})", file=sys.stderr)
                    return []
                espera = 2 ** intento          # 2, 4, 8 segundos
                print(f"  {e}; reintento {intento}/{REINTENTOS - 1} "
                      f"en {espera}s", file=sys.stderr)
                time.sleep(espera)

            except (requests.RequestException, json.JSONDecodeError,
                    KeyError, IndexError) as e:
                print(f"  no se pudieron adaptar los titulares ({e})",
                      file=sys.stderr)
                return []

        if adaptados is None:
            return []

        if not isinstance(adaptados, list) or len(adaptados) != len(trozo):
            print("  respuesta inesperada al adaptar titulares", file=sys.stderr)
            return []

        salida.extend(limpiar(str(a)) for a in adaptados)

    return salida


def cargar_previo() -> dict:
    if not SALIDA.exists():
        return {}
    try:
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def fusionar(previo: dict, nuevo: dict, ahora: str) -> dict:
    secciones_previas = previo.get("secciones", {})
    limite = datetime.fromisoformat(ahora) - timedelta(days=DIAS_RETENCION)
    salida = {}
    nuevos_totales = 0

    for seccion in SECCIONES:
        por_url = {}

        for item in secciones_previas.get(seccion, []):
            try:
                if datetime.fromisoformat(item["capturado"]) < limite:
                    continue
            except (KeyError, ValueError):
                pass
            por_url[item["url"]] = item

        # Solo entran los titulares que aún no están en el histórico.
        pendientes = [i for i in nuevo.get(seccion, []) if i["url"] not in por_url]
        adaptados = adaptar([i["titulo"] for i in pendientes])

        if pendientes and not adaptados:
            print(f"  {seccion}: {len(pendientes)} titulares quedan para la "
                  f"próxima corrida (sin adaptación)", file=sys.stderr)

        for item, propio in zip(pendientes, adaptados):
            por_url[item["url"]] = {
                **item,
                "titulo": propio,                 # redacción propia, es la que se publica
                "titulo_fuente": item["titulo"],  # original, solo como referencia
                "capturado": ahora,
            }
            nuevos_totales += 1

        ordenados = sorted(
            por_url.values(),
            key=lambda i: i.get("capturado", ""),
            reverse=True,
        )
        salida[seccion] = ordenados[:MAX_POR_SECCION]

    return {
        "fuente": BASE,
        "actualizado": ahora,
        "nuevos_en_esta_corrida": nuevos_totales,
        "secciones": salida,
    }


def main() -> int:
    ahora = datetime.now(BOGOTA).isoformat(timespec="seconds")
    nuevo = {}
    fallos = 0

    for seccion, cfg in SECCIONES.items():
        try:
            html = descargar(cfg["url"])
        except requests.RequestException as e:
            print(f"{seccion}: no se pudo descargar ({e})", file=sys.stderr)
            nuevo[seccion] = []
            fallos += 1
            continue

        items = extraer(html)
        if not items:
            diagnostico(html)

        nuevo[seccion] = items
        print(f"{seccion}: {len(items)} titulares")

    if not any(nuevo.values()):
        print("No se encontraron titulares: revise el diagnóstico de arriba.",
              file=sys.stderr)
        return 1

    datos = fusionar(cargar_previo(), nuevo, ahora)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Guardado {SALIDA} · {datos['nuevos_en_esta_corrida']} titulares nuevos")
    return 1 if fallos == len(SECCIONES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
