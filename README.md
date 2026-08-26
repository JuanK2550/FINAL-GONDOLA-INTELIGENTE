# Gondola Inteligente

Percepcion visual soberana para optimizar el espacio en retail. Reto de Scapder.

A partir del video de una tienda, el sistema cuantifica la dinamica de los
clientes alrededor de las gondolas (flujo, permanencia e interaccion con
productos) para recomendar mejoras de planograma. **No identifica personas, no
reconoce rostros, no infiere emociones y no crea perfiles biometricos.**

## Estado: Fase 3 de 5 (deteccion funcionando)

El sistema ya detecta personas en video. Lo que hay hoy:

- El **contrato de datos** (`ai-service/gondola/contract.py`), documentado en
  [`docs/data-contract.md`](docs/data-contract.md).
- El **registro de etapas** (`ai-service/gondola/pipeline.py`): el codigo decide
  los nombres de archivo, no las personas.
- La **deteccion** (`ai-service/gondola/stages/detect.py`) con YOLO11n.
- El **render de privacidad**: video de salida con fondo neutro y cero pixeles
  del original, proyectable en cualquier presentacion.
- La **CLI** (`python -m gondola`), con `doctor`, `run` y `purge`.

Pendientes: seguimiento, zonas, interaccion y metricas (Fases 4 y 5).

## Empezar

```bash
cp .env.example .env
pip install -r requirements-dev.txt
pytest

cd ai-service
python -m gondola doctor
```

`doctor` es lo primero que debes correr si algo no funciona: dice que hay
instalado, que archivos faltan y en que va la cadena.

Para trabajar con el video: dejalo en `data/videos/` (ver el README de esa
carpeta) y ajusta `VIDEO_PATH` en tu `.env`.

### Sin el video de Scapder

```bash
pip install -r requirements.txt          # pesado: arrastra PyTorch
python scripts/make_test_clips.py        # genera clips sinteticos

cd ai-service
python -m gondola detect --video data/videos/clip_formas.mp4
```

Esos clips son **controles negativos**: no hay ninguna persona en ellos, asi
que la deteccion debe dar 0. Eso prueba que el sistema no se inventa personas;
**no** prueba que detecte bien. Para eso hace falta video real y anotaciones
manuales (Fase 5).

## Comandos

```
python -m gondola doctor     Diagnostico del entorno
python -m gondola detect     Deteccion de personas      (Persona 2)
python -m gondola track      Seguimiento                (Persona 3)
python -m gondola zones      Zonas y permanencia        (Persona 4)
python -m gondola interact   Interaccion con productos  (Persona 5)
python -m gondola metrics    Metricas finales           (Persona 6)
python -m gondola run        La cadena completa
python -m gondola purge      Borra videos y salidas de data/
```

Opciones de `detect` (sobrescriben el `.env` solo para esa corrida):

```
--video RUTA     Otro video, en vez de VIDEO_PATH
--conf 0.6       Confianza minima
--stride 5       Procesar 1 de cada 5 frames
--max-frames 50  Cortar tras 50 frames (util para probar rapido)
--imgsz 640      Lado en pixeles para el modelo
--device cpu     cpu, cuda o mps
--render MODO    privacy (defecto) | debug | none
--open           Abrir el video resultante al terminar
```

**Los modos de render.** `privacy` dibuja sobre un fondo neutro: solo los
rectangulos, el frame, el timestamp y el conteo. No contiene ni un pixel del
video original, asi que se puede proyectar sin exponer a nadie. `debug` dibuja
sobre el video real y **no se comparte**.

## Documentacion

- [Contrato de datos](docs/data-contract.md) — **empieza por aqui**.
- [CLAUDE.md](CLAUDE.md) — reglas y convenciones del proyecto.

> README completo (arquitectura, uso, resultados): Fase 5.
