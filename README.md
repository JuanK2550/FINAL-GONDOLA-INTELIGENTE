# Góndola Inteligente

**Percepción visual soberana para optimizar el espacio en retail.**
Reto de la empresa Scapder.

A partir del video de una tienda, el sistema mide cómo se mueven los clientes
alrededor de las góndolas —flujo, permanencia e interacción con productos— para
recomendar mejoras de *space management* y planogramas.

> **No identifica personas, no reconoce rostros, no infiere emociones y no crea
> perfiles biométricos.** No es una promesa: son restricciones técnicas
> verificables. Ver [privacy.md](docs/privacy.md).

---

## El problema

Un supermercado sabe qué vende, pero no sabe **qué pasa frente al estante**:
cuánta gente pasa sin detenerse, cuánta se para y no toma nada, qué altura de
estante funciona, qué producto se toma y se devuelve.

Ese vacío se llena hoy con intuición. Este sistema lo llena con medición, sin
tocar la privacidad de nadie.

---

## Qué hace, en concreto

Sobre un video de cámara fija, el sistema entrega:

- **Cuánta gente pasó** por el encuadre y cuánta entró a cada góndola o estante.
- **Cuánto tiempo se quedó** cada persona frente a cada estante.
- **Cuántas veces alguien alcanzó el estante**, y cuántas de esas acabaron en
  tomar un producto o devolverlo.
- **Un mapa de calor por coordenadas** con las posiciones reales de los pies,
  dibujado sobre la silueta de la góndola calibrada.
- **Un video anonimizado** donde se ve la detección y el seguimiento sin un solo
  píxel del original.
- **Un dashboard** con todo lo anterior, comparación de dos videos, exportación
  a PDF/Excel y subida de un video nuevo desde el propio navegador.

---

## Arquitectura

```
   video.mp4
       |
   [ detect ] --> [ track ] --> [ zones ] --> [ interact ] --> [ metrics ]
       |              |             |              |               |
       +--------- archivos .jsonl (un evento por línea) ------------+
                              |
                    [ Backend ] --> PostgreSQL
                          |
                       API REST
                          v
                    [ Dashboard ]
```

Cada etapa lee el archivo de la anterior, **rellena sus propios campos** del
mismo evento y escribe el siguiente. Nadie inventa formatos.

**El AI Service nunca escribe en la base de datos.** Produce archivos; solo el
backend los importa. Así se puede trabajar en el pipeline sin instalar
PostgreSQL, y si un procesamiento falla a medias se borra un archivo en vez de
limpiar tablas. El razonamiento completo está en
[architecture.md](docs/architecture.md).

---

## Tecnologías

| Capa | Herramientas |
|---|---|
| Detección de personas | Python 3.12+, Ultralytics **YOLO11n**, OpenCV |
| Seguimiento y métricas | Python puro (geometría; sin dependencias pesadas) |
| Contrato de datos | Pydantic v2 (`extra="forbid"`) |
| Formato de intercambio | JSONL (un evento por línea, leído y escrito en streaming) |
| Backend y API | **Python** (FastAPI) + psycopg, sin ORM |
| Base de datos | PostgreSQL 17 (en Docker para desarrollo) |
| Video renderizado | H.264 vía OpenCV + openh264, reproducible en cualquier navegador |
| Dashboard | HTML/CSS/JS vanilla + Tailwind vendorizado — **la única excepción al Python** |
| Tests | pytest (460 tests) |

**Python para todo el sistema**, de la detección al backend. No se usa Java,
Spring Boot, JPA, Maven, Gradle ni IntelliJ, y no hay una línea de eso en el
repositorio.

El dashboard (`frontend/`) es la excepción, a propósito y por escrito: es
HTML/CSS/JS vanilla **sin build ni Node**, repartido en 13 archivos por
responsabilidad (`index.html` solo carga el resto). Se abre con doble clic y
consume la API por `fetch()`. Se hizo así para no obligar a nadie a instalar un
segundo entorno solo para verlo funcionar. Detalle en
[frontend/README.md](frontend/README.md).

---

## Estructura

```
FINAL-GONDOLA-INTELIGENTE/
├── ai-service/            El pipeline de visión
│   ├── gondola/
│   │   ├── contract.py      EL CONTRATO. Empieza a leer por aquí.
│   │   ├── config.py        Configuración, leída del .env
│   │   ├── pipeline.py      Registro de etapas: el código decide los nombres
│   │   ├── jsonl.py         Lectura/escritura en streaming
│   │   ├── cli.py           python -m gondola
│   │   ├── stages/          Una etapa por archivo
│   │   ├── video/           Lo único que sabe de OpenCV
│   │   ├── verify/          ¿cumple el contrato y la privacidad?
│   │   └── evaluate/        ¿acierta? (contra anotaciones humanas)
│   └── tests/               unit/ (rápidos) e integration/ (con YOLO)
├── backend/               Importador y API REST
│   └── database/            schema.sql y datos de ejemplo
├── frontend/              Dashboard (ver su README)
├── data/                  videos, models, zones, output, groundtruth
├── docs/                  Documentación técnica
└── scripts/               setup, calibración de zonas, clips de prueba
```

---

## Instalación

```bash
git clone https://github.com/JuanK2550/FINAL-GONDOLA-INTELIGENTE.git
cd FINAL-GONDOLA-INTELIGENTE
python scripts/setup.py
```

`scripts/setup.py` detecta e instala lo que falte: copia los `.env`, instala
las dependencias ligeras, levanta PostgreSQL con Docker y carga el esquema, y
en Windows descarga la librería `openh264` que hace falta para que los videos
renderizados se reproduzcan en un navegador. Es seguro correrlo varias veces.

```bash
python scripts/setup.py --full     # además instala PyTorch/YOLO (~3 GB)
python scripts/setup.py --model    # además descarga data/models/yolo11n.pt
```

Comprueba que todo está en su sitio:

```bash
cd ai-service
python -m gondola doctor
```

`doctor` es lo primero que hay que correr cuando algo no funciona: dice qué
está instalado, qué archivos faltan, si el códec H.264 **codifica de verdad**
(no solo si la librería existe) y en qué punto va la cadena.

---

## Configuración

Todo se ajusta en el `.env` (copiado de `.env.example`). Lo que más se toca:

| Variable | Para qué |
|---|---|
| `VIDEO_PATH` | Dónde está el video |
| `VIDEO_ID` | Etiqueta corta que queda escrita en cada evento |
| `CONFIDENCE_THRESHOLD` | Confianza mínima (0.0–1.0). Súbela si aparecen personas donde no las hay |
| `FRAME_STRIDE` | Procesar 1 de cada N frames. Súbelo para ir más rápido en pruebas |
| `MAX_FRAMES` | Cortar tras N frames. 0 = video completo |
| `RENDER_MODE` | `privacy` (defecto), `debug` o `none` |
| `DEVICE` | `cpu`, `cuda` o `mps` |

---

## Los videos

**Van en `data/videos/`.** No se suben al repositorio: pesan, y contienen
imágenes de personas reales (`.gitignore` los excluye a propósito). Los
archivos de calibración de zonas (`data/zones/*.json`) **sí** están
versionados: son texto plano con coordenadas de cámara, sin ningún dato de
personas.

```bash
# 1. deja el archivo en data/videos/mi_video.mp4
# 2. apúntalo en tu .env:
#      VIDEO_PATH=data/videos/mi_video.mp4
#      VIDEO_ID=video_001

cd ai-service
python -m gondola run
```

**¿Todavía no tienes video?** Genera clips sintéticos para trabajar:

```bash
python scripts/make_test_clips.py
cd ai-service
python -m gondola detect --video data/videos/clip_formas.mp4
```

Esos clips son **controles negativos**: no hay ninguna persona en ellos, así que
la detección debe dar 0. Eso prueba que el sistema no se inventa personas; **no**
prueba que detecte bien.

---

## Ejecutar

```bash
cd ai-service

python -m gondola doctor      # diagnóstico. Empieza SIEMPRE por aquí
python -m gondola detect      # detección de personas
python -m gondola track       # seguimiento entre frames
python -m gondola zones       # zonas y permanencia
python -m gondola interact    # interacción con productos
python -m gondola metrics     # métricas agregadas
python -m gondola run         # la cadena completa
python -m gondola verify <archivo>   # ¿cumple el contrato y la privacidad?
python -m gondola eval        # ¿acierta? (necesita anotaciones)
python -m gondola purge       # borra videos y salidas de data/
```

Opciones de `detect` (sobrescriben el `.env` solo para esa corrida):

```
--video RUTA     --conf 0.6      --stride 5       --max-frames 50
--imgsz 640      --device cpu    --render MODO    --open
```

Códigos de salida: `0` éxito · `1` error · `2` falta un requisito.

Para subir los resultados a PostgreSQL y verlos en el dashboard:
[`backend/README.md`](backend/README.md) y
[`frontend/README.md`](frontend/README.md).

---

## Cómo leer la salida

Cada etapa escribe un `.jsonl` en `data/output/`: **un evento por línea**, donde
un evento es *una persona detectada en un frame*.

```json
{"video_id": "video_001", "frame": 253, "timestamp": 8.43, "track_id": 7,
 "detection": {"class": "person", "confidence": 0.94,
               "bbox": {"x": 145.0, "y": 40.0, "width": 90.0, "height": 150.0}},
 "zone": {"zone_id": "gondola_A", "segment": "estante_2"},
 "interaction": {"event": "PICK_UP", "product_zone": "bebidas"},
 "metrics": {"dwell_time": 12.5}}
```

Esa línea dice: *"a los 8.43 segundos, una silueta llevaba 12.5 segundos frente
al estante 2 de la góndola A y tomó un producto de bebidas"*. Suficiente para
optimizar un planograma; nada sobre **quién** era.

Los `null` no son errores: significan *"esa etapa todavía no ha pasado por
aquí"*. Formato completo en [data-contract.md](docs/data-contract.md).

> ⚠️ `bbox.width` y `bbox.height` son **píxeles de la caja**, nunca la estatura
> ni ninguna medida de la persona.

Además de los eventos, cada corrida deja un `.summary.json` con los parámetros
usados y un video renderizado (en modo `privacy`, sin un solo píxel del
original).

### Qué significan exactamente los números del dashboard

Dos cifras que se leen como contradictorias y no lo son:

- **"Personas vistas por la cámara"** cuenta siluetas distintas en TODO el
  encuadre. **"Personas" de la tabla de zonas** cuenta solo a quien entró en esa
  zona: es un subconjunto, por eso suele ser menor.
- **Los estantes pueden sumar más personas que su góndola.** Quien mira el
  estante 1 y después el 2 cuenta en los dos, pero en la góndola cuenta una vez:
  es un conteo de personas distintas, no una suma de filas.

`interaction_rate` y `conversion_rate` son **proporciones de personas** (cuántas
interactuaron al menos una vez), no interacciones por persona: así nunca pasan
de 1 y no hay que recortarlas.

Tampoco cuenta como persona una silueta que se vio menos de un segundo: es un
parpadeo de la detección, no un cliente. Ese mismo criterio se aplica al video
renderizado, así que lo que se dibuja y lo que se cuenta siempre coinciden.

---

## Privacidad

Tres barreras técnicas, no una declaración:

1. **El contrato usa `extra="forbid"`.** Un campo de edad o rostro rompe la
   validación al instante, en la máquina de quien lo intentó.
2. **`verify` relee los archivos** buscando campos prohibidos en español e
   inglés, incluso anidados.
3. **El render por defecto es `privacy`**: fondo neutro generado desde cero. Al
   renderizador ni se le pasa el fotograma original.

Además, el seguimiento **no puede** reidentificar por apariencia aunque alguien
quisiera: la etapa `track` no recibe píxeles, solo un archivo de cajas y
números. No es una promesa de buen comportamiento, es imposibilidad estructural.

Detalle, incluida la relación con la Ley 1581 de 2012, en
[privacy.md](docs/privacy.md).

---

## Estado

El sistema está completo de punta a punta: se procesa un video, se importa a
PostgreSQL y se ve en el dashboard.

| Parte | Estado |
|---|---|
| Arquitectura, contrato de datos, CLI | hecho |
| Las cinco etapas (detect, track, zones, interact, metrics) | hecho |
| Verificador de contrato y privacidad | hecho |
| Base de datos, importador y API REST | hecho |
| Dashboard, con recomendaciones y nivel de confianza | hecho |
| Subir un video desde el navegador y procesarlo | hecho |
| Viabilidad *edge* (CPU, sin GPU, sin nube) | medido — [edge-viability.md](docs/edge-viability.md) |
| Evaluación de interacción contra groundtruth | medido — [interact-evaluacion-merl.md](docs/interact-evaluacion-merl.md) |
| Cobertura de pruebas | 460 tests |

### Alcance: qué mide y qué no

Todo lo que el dashboard muestra es una medición sobre el video. Estas cuatro
fronteras son decisiones de diseño, y conviene tenerlas claras al leer los
números:

- **Mide comportamiento, no producto.** El sistema sabe que alguien alcanzó el
  estante; no sabe qué había en la mano. `PICK_UP` y `PUT_BACK` son el mismo
  gesto físico —el brazo sale y vuelve—, así que el primer alcance de una visita
  se cuenta como toma y el segundo como devolución. La tasa de rechazo se apoya
  en esa convención.

- **La interacción sí está evaluada contra groundtruth**, con las etiquetas
  oficiales del MERL Shopping Dataset (167 gestos reales, de 0,43 a 3,50 s). Con
  esa evidencia se recalibró la ventana del detector de gestos. Ver
  [interact-evaluacion-merl.md](docs/interact-evaluacion-merl.md).

- **La detección se apoya en YOLO11n preentrenado**, sin reentrenar. Para dar
  una cifra propia de precisión/recall haría falta anotar video a mano en
  `data/groundtruth/`; mientras eso no exista, el proyecto no publica una cifra
  en vez de publicar una sin respaldo.

- **El conteo de personas es una cota superior.** Sin reidentificación por
  apariencia —prohibida por privacidad, no omitida— una oclusión larga entre
  dos desconocidos puede partir una visita en dos identidades. Se redujo de 76
  a 44 identidades en los siete videos medidos, con los relevos de 23 a 3 (ver
  `ai-service/gondola/stages/track.py`), pero el caso límite existe y el número
  se lee sabiéndolo.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`requirements-dev.txt` es ligero a propósito: los tests corren sin descargar
los 3 GB de PyTorch. Lo pesado vive en `requirements.txt` y solo lo necesita
quien ejecuta el pipeline completo.

---

## Documentación

| Documento | Qué contiene |
|---|---|
| [data-contract.md](docs/data-contract.md) | **El contrato. Empieza aquí.** |
| [architecture.md](docs/architecture.md) | Por qué las piezas están así |
| [zones-format.md](docs/zones-format.md) | Formato de calibración de cámara |
| [database.md](docs/database.md) | Esquema SQL |
| [privacy.md](docs/privacy.md) | Privacidad por diseño |
| [evaluation.md](docs/evaluation.md) | Cómo anotar y medir exactitud |
| [edge-viability.md](docs/edge-viability.md) | Medición de rendimiento en CPU |
| [interact-evaluacion-merl.md](docs/interact-evaluacion-merl.md) | Evaluación de interacción contra groundtruth |
| [tracking-informe-cierre.md](docs/tracking-informe-cierre.md) | Decisiones y medidas del seguimiento |
| [development.md](docs/development.md) | Estándares y ramas |
