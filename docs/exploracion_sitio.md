# Exploración del sitio — www.bancolombia.com (M1)

> Fecha: 2026-10-01 · Herramienta: `scripts/explore_site.py` · User-Agent: `RAG-BBVA-TechTest/1.0` · Pausa: 1 s
> Evidencia: [`docs/evidencia/exploracion_bancolombia_2026-10-01.json`](evidencia/exploracion_bancolombia_2026-10-01.json)
> Estado: **decisiones validadas por Juan Felipe el 2026-10-01** (checkpoint de M1; S-03, S-04 y ADR-009 confirmados).

## 0. Cambio de sitio: por qué no bbva.com.co (ADR-008)

| Sitio | `robots.txt` | Home / sitemap | Resultado |
|---|---|---|---|
| www.bbva.com.co | **403** (WAF, página "Algo salió mal" con *Reference ID*) | 403 / 403 | ❌ Bloquea a todo cliente no navegador: `RAG-BBVA-TechTest/1.0`, `curl` y `urllib` de Python |
| www.davivienda.com | 200 | bloqueo Incapsula | ❌ |
| bancodebogota · bancodeoccidente · itau.co | 403 | — | ❌ |
| avvillas.com.co · bancopopular.com.co | 200 | 200 | ✅ alternativas |
| **www.bancolombia.com** | **200** | **200** | ✅ **elegido** |

Sin poder leer `robots.txt` no se puede verificar qué está permitido (S-08). Eludir el bot-manager simulando un navegador se descartó. El caso permite cambiar de banco; BBVA se mantiene como cliente ficticio.

## 1. robots.txt

- **Grupo que nos aplica:** `User-agent: *`. Nuestro token `RAG-BBVA-TechTest` no se nombra; el grupo `*` es el mismo bloque de buscadores SEO y de búsqueda con IA (Googlebot, OAI-SearchBot, Claude-User, PerplexityBot…).
- **76 reglas `Disallow`** para nuestro grupo:
  - Comodines: `/*myportal`, `/rest/`, `/cgi/`, `/*formulario*`, `/*?ofertaId*`, **`/*pdf*`**, `/*!ut/p/z0/`, `/*!ut/p/z1/`, `/*buscador`.
  - Rutas concretas: `solicitud-de-productos/*`, `preaprobados`, páginas `-old` y de prueba, buscadores de cada sección, `/centro-de-ayuda/preguntas-frecuentes/resultados…`.
- **22 reglas `Allow`**, todas para recursos estáticos (`/connect/*.css`, `*.js`, imágenes) bajo el comentario *"Recursos para Renderizado Allow para RAG y SEO"*.
- **Bloqueo total (`Disallow: /`) para bots de entrenamiento de IA:** GPTBot, Google-Extended, ClaudeBot, Applebot-Extended y cohere-ai. Nuestro uso es recuperación (RAG), no entrenamiento, y nuestro UA no está en esa lista. Se declarará en el README.
- **Sin `Crawl-delay`.** Usamos nuestro `CRAWL_DELAY_SECONDS=1.0`.
- **Sitemap declarado:** `https://www.bancolombia.com/sitemap-index.xml`.

Implementación: `rag_bbva/scraping/robots.py`. Es propia porque `urllib.robotparser` no soporta los comodines `*`/`$` que este archivo usa en todas partes.

### Declaración de uso
`robots.txt` de Bancolombia **bloquea a los bots de entrenamiento de IA**. Este proyecto **no entrena modelos**: hace recuperación (RAG) sobre páginas públicas, con un User-Agent identificable (`RAG-BBVA-TechTest/1.0`) que no está en la lista bloqueada, respeta todas las reglas del grupo `*` y espera 1 s entre peticiones. Se declara como limitación L-02 (`00_VISION_GENERAL.md §12`) para el README.

## 2. Sitemaps

| Sitemap | Tipo | Entradas |
|---|---|---|
| `/sitemap-index.xml` (declarado en robots) | índice | **4** (personas, acerca-de, centro-de-ayuda, educación financiera) |
| `/sitemap.xml` (no declarado) | índice | **7** (los 4 anteriores + **empresas, negocios, sala-de-prensa**) |
| `/sitemap-personas.xml` | urlset | 440 |
| `/sitemap-empresas.xml` | urlset | 201 |
| `/sitemap-centro-de-ayuda.xml` | urlset | 170 |
| `/sitemap-acerca-de.xml` | urlset | 119 |
| `/sitemap-negocios.xml` | urlset | 93 |
| `/sitemap-sala-de-prensa.xml` | urlset | 73 (URLs bajo `/acerca-de/…`) |
| `/sitemap-educacion-financiera.xml` | urlset | 28 |

**Hallazgo:** el índice que declara `robots.txt` omite 3 sitemaps. El crawler debe leer **ambos** índices y deduplicar.

## 3. URLs por sección

**1.118 URLs únicas**, todas en `www.bancolombia.com`, sin query strings y sin rutas `/wps/portal`.

| Sección (1.er segmento) | URLs |
|---|---|
| `personas` | 438 |
| `empresas` | 201 |
| `acerca-de` (incluye sala de prensa) | 189 |
| `centro-de-ayuda` | 169 |
| `negocios` | 93 |
| `educacion-financiera` | 28 |
| **Total** | **1.118** |

- **Permitidas por robots:** 1.113. Prohibidas: 5 (`solicitud-de-productos/…`, `formulario-legal`).
- **Basura en el sitemap:** 4 URLs `…/src/sass/main.sass` y 1 `…/seguros/vehiculos/www.sura.com`, que es un enlace mal formado.
- **Tipos de contenido:** en los sitemaps solo hay HTML, sin PDFs (además `/*pdf*` está prohibido en robots). En la muestra todas las respuestas 200 fueron `text/html`.

## 4. Muestra de páginas y dependencia de JavaScript

Se tomaron 14 URLs estratificadas por sección (2–3 por sección). Cada una se descargó con httpx y se comparó con el HTML renderizado por Chromium headless (Playwright, solo para esta medición, con imágenes, fuentes y multimedia bloqueadas). En total fueron 26 peticiones HTTP más 12 renders, sin un solo 403 ni 429.

| Métrica | Valor |
|---|---|
| Códigos | 12 × 200 · 2 × 301 hacia `fiduciaria.bancolombia.com` (otro host: **no se siguen**) |
| Tamaño HTML | mediana 171 KB (100–235 KB) |
| Latencia | mediana ~460 ms |
| Palabras visibles en HTML estático | 237 – 1.065 por página |
| **Cobertura del texto renderizado por el estático** | **0,78 – 1,00** (mediana ≈ 0,88) |

**¿Qué agrega el renderizado?** Comparando por frases (`only_rendered_segments`):

1. **Banner de cookies**, en todas las páginas. Es ruido y conviene que no esté.
2. **Carruseles y promociones** en landings (`/personas`: seguros de viaje, mascotas, descuentos).
3. **"Preguntas relacionadas"** en el centro de ayuda (`¿Cómo abro una Cuenta de Ahorros…?`) y **tarjetas de artículos** en `/educacion-financiera`.

Los puntos 2 y 3 son **títulos y enlaces a otras páginas que ya están en los sitemaps**: su contenido completo se obtiene al descargar esas páginas. El cuerpo de cada página (descripción del producto, respuesta de la FAQ, artículo) **está completo en el HTML estático**.

El veredicto "parcial" del analizador en 5 páginas se debe a placeholders `${title}`, `${loading}` y `${badge}`. Pertenecen a los menús ocultos de los portlets de WebSphere ("Web Content Viewer Component Action Menu"), no al contenido.

### Decisión propuesta: **httpx + BeautifulSoup/lxml (+ trafilatura en M3), sin Playwright** (ADR-009)
Renderizar encarecería todo sin agregar contenido propio: Chromium dentro de la imagen Docker, segundos extra por página y la descarga de los JS/CSS de cada página contra el sitio.

## 5. Plantillas y selectores de contenido principal

Se identificaron **3 plantillas**. Cada página de la muestra tiene exactamente uno de estos contenedores:

| Plantilla | Dónde | Selector | Ejemplo |
|---|---|---|---|
| A. Diseño nuevo (`bc-*`, Tailwind) | `negocios`, `empresas`, algunos `personas/creditos` | **`main`** | `main.flex.flex-1` (684–3.239 caracteres) |
| B. Corporativo | `acerca-de`, sala de prensa | **`#main-content`** | `div#main-content` (1.815–5.863 caracteres) |
| C. WebSphere Portal (`wptheme*`) | `personas`, `centro-de-ayuda`, `educacion-financiera` | **`[role=main]`** | contiene columnas `div.component-container.wpthemeCol`; en FAQs el cuerpo está en `div.component-container.bc-col-lg-8.wpthemeCol` |

**Cadena de extracción propuesta para M3:**
1. `trafilatura` sobre el HTML completo (extractor genérico de contenido principal).
2. Si devuelve poco texto, *fallback* con BeautifulSoup sobre el primer selector que exista: `main` → `#main-content` → `[role=main]` → `body`.
3. Eliminar siempre `header`, `nav`, `footer`, enlaces "Saltar al contenido", menús de portlet con placeholders `${…}` y cajas de error WCM (`.lrpError`). Las landings tipo hub (densidad de enlaces de 0,75–0,86 en `#layoutContainers`) aportan poco texto propio; se conservan, pero su utilidad la decide el filtro de longitud de M3.

## 6. Riesgos detectados

| Riesgo | Evidencia | Mitigación propuesta |
|---|---|---|
| **WAF / bloqueos** | bbva.com.co, Davivienda e Itaú bloquean; `fiduciaria.bancolombia.com` responde 403 | Pausa ≥ 1 s, UA identificable, abortar el crawl si hay ráfagas de 403/429 (umbral en M2), no seguir redirecciones fuera del dominio (implementado y probado) |
| **Redirecciones con `utm_*`** | `/empresas` → `/negocios?utm_source=redirect…`; `/personas/creditos/negocios` → `/negocios/…?utm_…` | Normalizar URLs en M2 (quitar `utm_*`, fragmentos y barra final) y deduplicar por URL final |
| **Contenido duplicado / soft-404** | `/empresas` y `/negocios` comparten título; en la primera corrida `paquete-chileno` devolvió 200 con el mismo título que la landing de educación financiera y un tamaño casi idéntico (215 KB frente a 214 KB): posible soft-404 | Deduplicar por hash de texto en M3; registrar la URL final en el manifest |
| **PDFs** | Ninguno en los sitemaps; `/*pdf*` prohibido en robots | Fuera de alcance (S-03), consistente con robots |
| **Contenido cargado con JS** | Carruseles, FAQs relacionadas, tarjetas de artículos | Aceptado: son enlaces a páginas que se descargan aparte (ver §4) |
| **Errores de plantilla en HTML** | `referido-leasing-vehiculo` muestra "El sistema no puede mostrar el elemento de contenido…" | Filtrar `.lrpError` en M3 y descartar páginas sin contenido |
| **URLs basura en el sitemap** | `.sass`, `www.sura.com` como ruta | Filtrar extensiones no HTML y validar respuestas `text/html` |
| **Política anti-IA de entrenamiento** | `Disallow: /` para GPTBot, ClaudeBot… | Uso RAG (no entrenamiento) con UA propio; se declara en el README |
| **Volumen** | ~1.100 páginas × ~170 KB ≈ 190 MB de HTML crudo | `data/` fuera de git; el crawl completo tarda ≈ 25–30 min (1 s de pausa + ~0,46 s de latencia por página) |

## 7. Decisiones (validadas el 2026-10-01)

1. **Secciones a incluir:** `personas`, `negocios`, `empresas`, `centro-de-ayuda`, `educacion-financiera` y `acerca-de` (incluye sala de prensa). Las 6 tienen contenido informativo útil para usuarios internos.
2. **Excluir:**
   - Rutas prohibidas por robots.
   - Redirecciones fuera de `www.bancolombia.com` (por ejemplo `fiduciaria.bancolombia.com`).
   - URLs con extensión no HTML (`.sass`, `.pdf`) y rutas mal formadas.
   - Respuestas no `text/html`.
3. **Descubrimiento:** semillas desde **ambos** índices de sitemap. BFS con `CRAWL_MAX_DEPTH=1` solo para descubrir páginas internas no listadas.
4. **Límite de páginas:** `CRAWL_MAX_PAGES=1200` (default), que cubre el sitemap completo (1.113 URLs permitidas) con margen para lo que descubra el BFS. En desarrollo se usa `--max-pages 50`. El arranque con `docker compose` no scrapea: usa un snapshot versionado de datos limpios (M12).
5. **Herramienta:** httpx + BeautifulSoup/lxml, sin Playwright (ADR-009).
6. **Selectores:** trafilatura con *fallback* `main` → `#main-content` → `[role=main]` (§5).
7. **Fecha de publicación:** en `acerca-de`/sala de prensa, si la página la tiene se guarda como metadato `published_at` (se implementa en M2/M3).
