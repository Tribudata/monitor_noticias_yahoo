# Monitor Yahoo Finanzas — marquesina

Recoge cada media hora los titulares del bloque principal de
es.finance.yahoo.com, los reformula con Gemini y los publica como una
marquesina horizontal de desplazamiento lento.

```
.github/workflows/actualizar-noticias.yml   cron + commit automático
scraper/scrape.py                           extracción, adaptación y fusión
data/noticias.json                          archivo que consume la página
index.html                                  marquesina (GitHub Pages)
requirements.txt
```

## Montaje

1. Repositorio nuevo llamado `monitor_noticias_yahoo`, rama `main`.
   Cree las carpetas escribiendo la ruta completa en **Add file → Create new
   file**, o clone y empuje la estructura desde su equipo.
2. Secreto `GEMINI_API_KEY` en Settings → Secrets and variables → Actions.
3. **Settings → Actions → General**: *Read and write permissions*.
4. **Settings → Pages**: rama `main`, carpeta `/ (root)`.
5. **Actions → Actualizar Yahoo Finanzas → Run workflow**.

Si le pone otro nombre al repositorio, cambie `FUENTE_JSON` en `index.html`.

## La marquesina

- Se pinta la lista dos veces seguidas y se desplaza media anchura: al
  terminar el ciclo la segunda copia está donde empezó la primera, así que
  no se ve ningún salto.
- La duración se calcula del ancho real a razón de `VELOCIDAD` píxeles por
  segundo (22 por defecto). Bájelo para ir más lento; el ritmo no cambia
  aunque haya más o menos titulares.
- Se detiene al pasar el mouse por encima, para poder leer y hacer clic.
- Respeta `prefers-reduced-motion`: a quien tenga desactivadas las
  animaciones en su sistema se le muestra la cinta quieta y desplazable a
  mano.

Alto sugerido para el iframe: 90 px.

## Extracción

- Se limita a `section.module-hero`, el bloque principal de la portada.
- Toma el `<a>` que envuelve cada `h2` o `h3`, descarta los enlaces a
  cotizaciones (`/quote/`) y deduplica por URL, porque la nota principal
  aparece enlazada dos veces (imagen y titular).
- Guarda además el medio que firma la nota, en el campo `medio`. La página
  no lo muestra, pero queda disponible en el JSON.

## Prueba local

```bash
pip install -r requirements.txt
python scraper/scrape.py
python -m http.server 8000
```
