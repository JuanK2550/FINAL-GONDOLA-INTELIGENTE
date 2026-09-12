"""Etapa 3: zonas y permanencia. Responsable: Persona 4.

Lee `<video>.track.jsonl`, calcula el `support_point` de cada caja (los
pies) y decide en que `floor_zone` cae. Rellena `zone` y
`metrics.dwell_time`; nada mas. Caer fuera de toda zona -el pasillo, entre
gondolas- deja `zone` en null: es un resultado correcto, no un error.

El archivo de calibracion se busca en `data/zones/<video_id>.json` y lo
valida `gondola/zones_config.py`. Formato: `docs/zones-format.md`.

POR QUE LOS PIES Y NO EL CENTRO DE LA CAJA
-------------------------------------------
Las zonas son rectangulos de PISO, asi que hay que compararlas contra un
punto que tambien este en el piso. El centro de la caja flota a la altura
del pecho: dos personas pisando el mismo sitio, una alta y otra baja, dan
centros a alturas distintas. Los pies no tienen ese problema.

TRES DECISIONES QUE NO SON OBVIAS
-----------------------------------
1. BORDE ENTRE DOS ZONAS. `punto_en_zona` usa intervalo SEMIABIERTO:
   `[x, x+width)`. Con dos zonas que comparten borde en x=530, el punto
   x=530 cae siempre en la de la derecha: nunca en las dos (duplicaria a la
   persona) ni en ninguna (la mandaria al pasillo estando pegada al
   estante). Misma convencion que el rasterizado 2D.

2. ZONAS QUE SE SOLAPAN. Gana la de MENOR AREA. La mas pequeña es casi
   siempre la mas especifica -alguien la dibujo pensando en un sitio
   concreto-; la grande suele ser una zona general que por descuido cubrio
   de mas. Es determinista y no depende del orden del JSON.

3. "SE DETIENE" vs "PASA DE LARGO". Un solo umbral,
   `UMBRAL_SE_DETIENE_S=2.0`: cruzar un estante caminando toma bastante
   menos de un segundo, asi que quien lleva 2 ya freno a mirar. NO esta
   validado contra groundtruth, y por eso `clasifica_visita` lo recibe como
   parametro: recalibrarlo no exige tocar la funcion. Clasifica el INSTANTE,
   no la visita entera -alguien con dwell 1.0 que luego llega a 3.0 sale
   "pasa_de_largo" ahora y "se_detiene" despues, y es correcto-.

QUE ES dwell_time, EXACTO
--------------------------
Tiempo real (por `timestamp`, nunca contando frames: `FRAME_STRIDE` puede
ser > 1) que ese `track_id` lleva CONTINUAMENTE en la MISMA zona. Empieza
en 0.0 en cada visita nueva; cambiar de zona reinicia. Fuera de zona es
`null`. No es un acumulado historico de todas sus visitas.

EL HUECO: UN track_id PUEDE DESAPARECER Y VOLVER (leelo antes de tocar)
-----------------------------------------------------------------------
Desde que `track` recupera identidades (bloque REAPARICION en `track.py`),
un mismo `track_id` puede tener un hueco de hasta VENTANA_DE_REAPARICION_S
segundos sin eventos y seguir. Como `dwell_time` es `timestamp -
visita.entrada` -reloj de pared, no suma de intervalos vistos-, esos
segundos invisibles CUENTAN como permanencia.

Es deliberado: para reaparecer, la persona tuvo que volver a menos de
DISTANCIA_MAXIMA_PARA_REAPARECER pixeles de donde se perdio, o sea que no
se fue -algo la tapo-. Alguien parado frente al estante sigue estando ahi
aunque la camara lo pierda medio segundo.

Lo que SI queda subestimado es la fragmentacion que el tracker no alcanza a
recuperar: dos `track_id` donde hubo una persona, cada uno con su dwell
desde 0.0. Desde aqui no se puede detectar -llega un numero, no una
persona-.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from gondola import pipeline
from gondola.config import RAIZ, Config
from gondola.contract import CONTRACT_VERSION, Event
from gondola.jsonl import read_events, write_events
from gondola.zones_config import FloorZone, Gondola, Shelf, ZonesConfig, load_zones_config


# --------------------------------------------------------------------------
# Logica pura: se prueba sin archivos ni zonas cargadas de disco
# --------------------------------------------------------------------------

def punto_en_zona(punto: tuple[float, float], zona: FloorZone) -> bool:
    """True si `punto` cae dentro de `zona`. Ver "CASO 1" en el docstring del
    modulo: intervalo semiabierto, el borde derecho/inferior no cuenta."""
    px, py = punto
    return zona.x <= px < zona.x + zona.width and zona.y <= py < zona.y + zona.height


def asignar_zona(
    punto: tuple[float, float], zonas: ZonesConfig
) -> tuple[Gondola, Shelf] | None:
    """La (gondola, estante) donde cae `punto`, o `None` si no cae en ninguno.

    Ver "CASO 2" en el docstring del modulo: si varias zonas se solapan,
    gana la de menor area.
    """
    candidatos = [(g, e) for g, e in zonas.shelves() if punto_en_zona(punto, e.floor_zone)]
    if not candidatos:
        return None
    candidatos.sort(key=lambda par: par[1].floor_zone.width * par[1].floor_zone.height)
    return candidatos[0]


UMBRAL_SE_DETIENE_S = 2.0
"""Segundos de `dwell_time` a partir de los cuales una visita cuenta como
"se detiene" en vez de "pasa de largo". Ver "CASO 3" en el docstring del
modulo para el porque de este valor y de por que no esta fijo en el codigo."""


def clasifica_visita(
    dwell_time: float | None, umbral_s: float = UMBRAL_SE_DETIENE_S
) -> str | None:
    """"se_detiene" si `dwell_time >= umbral_s`, "pasa_de_largo" si es menor,
    o `None` si `dwell_time` es `None` (la persona no esta en ninguna zona).

    Pura: no lee ni escribe ningun `Event`. Ver "CASO 3" en el docstring del
    modulo.
    """
    if dwell_time is None:
        return None
    return "se_detiene" if dwell_time >= umbral_s else "pasa_de_largo"


@dataclass
class Visita:
    """La visita en curso de un `track_id`: a que zona, y desde cuando.

    Vive solo en memoria mientras corre `_procesar`; no es lo que se escribe
    en el .jsonl (eso es `zone` y `metrics.dwell_time`, dentro de cada
    `Event`).
    """

    zona: tuple[str, str] | None  # (zone_id, segment), o None si esta en el pasillo
    entrada: float  # timestamp en el que empezo ESTA visita continua


@dataclass
class Resumen:
    """Lo que se va contando durante la corrida y acaba en el JSON de resumen."""

    eventos_procesados: int = 0
    eventos_con_zona: int = 0
    eventos_sin_zona: int = 0
    eventos_por_zona: dict[str, int] = field(default_factory=dict)
    eventos_se_detiene: int = 0
    eventos_pasa_de_largo: int = 0


def _procesar(entrada: Path, zonas: ZonesConfig, resumen: Resumen) -> Iterator[Event]:
    """Recorre los eventos en orden, les asigna zona y acumula dwell_time.

    Es un generador: solo mantiene en memoria la visita en curso de cada
    `track_id` (un dict pequeno, uno por persona activa), nunca el archivo
    completo. `track.jsonl` garantiza `track_id` siempre relleno (ver
    `docs/tracking-guia-para-zonas.md`), asi que no hace falta un caso aparte
    para `track_id is None`.
    """
    visitas: dict[int, Visita] = {}

    for evento in read_events(entrada):
        resultado = asignar_zona(evento.detection.bbox.support_point, zonas)

        if resultado is None:
            zona_actual = None
            resumen.eventos_sin_zona += 1
        else:
            gondola, estante = resultado
            evento.zone.zone_id = gondola.zone_id
            evento.zone.segment = estante.segment
            zona_actual = (gondola.zone_id, estante.segment)
            resumen.eventos_con_zona += 1
            clave = f"{gondola.zone_id}/{estante.segment}"
            resumen.eventos_por_zona[clave] = resumen.eventos_por_zona.get(clave, 0) + 1

        visita = visitas.get(evento.track_id)
        if visita is None or visita.zona != zona_actual:
            visita = Visita(zona=zona_actual, entrada=evento.timestamp)
            visitas[evento.track_id] = visita

        evento.metrics.dwell_time = (
            evento.timestamp - visita.entrada if zona_actual is not None else None
        )

        clasificacion = clasifica_visita(evento.metrics.dwell_time)
        if clasificacion == "se_detiene":
            resumen.eventos_se_detiene += 1
        elif clasificacion == "pasa_de_largo":
            resumen.eventos_pasa_de_largo += 1

        resumen.eventos_procesados += 1
        yield evento


# --------------------------------------------------------------------------
# Punto de entrada de la etapa
# --------------------------------------------------------------------------

def _ruta_zonas(cfg: Config) -> Path:
    """Donde vive la calibracion de esta camara. Ver docstring del modulo:
    `ZONES_PATH` queda pendiente para cuando se pueda tocar `config.py`."""
    return RAIZ / "data" / "zones" / f"{cfg.video_id}.json"


def run(cfg: Config) -> int:
    """Ejecuta la asignacion de zonas completa. Devuelve el codigo de salida."""
    rutas = pipeline.stage_paths("zones", cfg)
    pipeline.require_input("zones", cfg)

    ruta_zonas = _ruta_zonas(cfg)
    zonas = load_zones_config(ruta_zonas)  # ZonesConfigError si falta o esta mal formado

    print(f"[zones] Entrada: {rutas.input_path}")
    print(f"[zones] Zonas:   {ruta_zonas}  "
          f"({len(zonas.gondolas)} gondola(s), {len(zonas.shelves())} estante(s))")
    print()

    resumen = Resumen()
    inicio = time.perf_counter()
    escritos = write_events(rutas.output_path, _procesar(rutas.input_path, zonas, resumen))
    transcurrido = time.perf_counter() - inicio

    ruta_resumen = pipeline.summary_path("zones", cfg)
    _escribir_resumen(ruta_resumen, cfg, zonas, ruta_zonas, resumen, transcurrido)

    _imprimir_resultado(resumen, escritos, transcurrido, rutas.output_path, ruta_resumen)
    return 0


def _leer_info_de_video_desde_track(cfg: Config) -> dict:
    """Copia `width`/`height`/`fps` del resumen de `track`, para que `verify`
    tambien pueda comprobar `bbox_en_frame` y `timestamps` sobre esta salida.
    Mismo patron que usa `track.py` con el resumen de `detect` -ver
    `docs/tracking-guia-para-zonas.md`, seccion de convenciones."""
    ruta = pipeline.summary_path("track", cfg)
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return datos.get("video", {})


def _escribir_resumen(
    destino: Path, cfg: Config, zonas: ZonesConfig, ruta_zonas: Path,
    resumen: Resumen, transcurrido: float,
) -> None:
    """Guarda las metricas de la corrida. Sin esto no se puede comparar nada."""
    datos = {
        "contract_version": CONTRACT_VERSION,
        "stage": "zones",
        "video_id": cfg.video_id,
        "video": _leer_info_de_video_desde_track(cfg),
        "params": {
            "archivo_de_zonas": str(ruta_zonas),
            "gondolas": len(zonas.gondolas),
            "estantes": len(zonas.shelves()),
        },
        "results": {
            "eventos_procesados": resumen.eventos_procesados,
            "eventos_con_zona": resumen.eventos_con_zona,
            "eventos_sin_zona": resumen.eventos_sin_zona,
            "eventos_por_zona": resumen.eventos_por_zona,
            "eventos_se_detiene": resumen.eventos_se_detiene,
            "eventos_pasa_de_largo": resumen.eventos_pasa_de_largo,
            "umbral_se_detiene_s": UMBRAL_SE_DETIENE_S,
        },
        "performance": {
            "segundos": round(transcurrido, 2),
            "eventos_por_segundo": round(
                resumen.eventos_procesados / transcurrido, 2
            ) if transcurrido > 0 else 0.0,
        },
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")


def _imprimir_resultado(
    resumen: Resumen, escritos: int, transcurrido: float, jsonl: Path, ruta_resumen: Path,
) -> None:
    print()
    print("-" * 66)
    print(f"  Eventos procesados     {resumen.eventos_procesados}")
    print(f"  Eventos con zona       {resumen.eventos_con_zona}")
    print(f"  Eventos sin zona       {resumen.eventos_sin_zona}  (pasillo, entre gondolas)")
    for clave, cuenta in sorted(resumen.eventos_por_zona.items()):
        print(f"      {clave:<30} {cuenta}")
    print(f"  Se detiene (>= {UMBRAL_SE_DETIENE_S:.1f}s)   {resumen.eventos_se_detiene}")
    print(f"  Pasa de largo          {resumen.eventos_pasa_de_largo}")
    print(f"  Tiempo                 {transcurrido:.2f} s")
    print("-" * 66)
    print(f"  Eventos   {jsonl}  ({escritos} lineas)")
    print(f"  Resumen   {ruta_resumen}")
    print()
    print("  Siguiente etapa:  python -m gondola interact   (Persona 5)")
