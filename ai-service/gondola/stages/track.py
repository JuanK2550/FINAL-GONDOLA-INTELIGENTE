"""Etapa 2: seguimiento entre frames. Responsable: Persona 3.

Lee las detecciones sueltas de la Persona 2 y les asigna un `track_id`: el
numero que dice "esta caja y aquella son la misma silueta". Nada mas se toca.

SIN APARIENCIA, A PROPOSITO
----------------------------
La reidentificacion por apariencia esta prohibida por privacidad (ver
`docs/data-contract.md`), asi que solo queda la geometria: donde estaba,
hacia donde iba y cuanto se solapa con lo que se ve ahora. Esta etapa ni
siquiera RECIBE pixeles -lee un .jsonl de cajas y numeros-, asi que no es
una promesa de buen comportamiento: es estructuralmente imposible. El video
solo se reabre, opcionalmente, para DIBUJAR.

COMO DECIDE QUE DOS CAJAS SON LA MISMA PERSONA
-----------------------------------------------
Cada track guarda su ultima caja, cuando se vio y su velocidad. Por frame:

1. PREDICE donde deberia estar cada track, extrapolando su velocidad. Esto
   es lo que distingue a dos personas que se cruzan: sus cajas pueden
   solaparse casi del todo, pero sus predicciones vienen de direcciones
   distintas. La velocidad se AMORTIGUA con el tiempo perdido (ver
   `predecir_bbox`): extrapolar varios segundos en linea recta es peor que
   asumir que sigue cerca de donde estaba.

2. EMPAREJA por IoU, con umbral IOU_MINIMO_PARA_EMPAREJAR. Entre candidatos
   del mismo escalon de IoU desempata `coherencia_de_movimiento`. Ver el
   docstring de `emparejar`: el orden importa y hubo un intento peor antes.

3. SEGUNDA OPORTUNIDAD para los que se quedaron sin pareja, por posicion y
   tamaño en vez de IoU: primero contra los tracks vivos sin emparejar,
   despues contra el cementerio de perdidos (bloque REAPARICION, mas abajo).

4. Un track sin emparejar NO muere de inmediato: flota sobre su prediccion
   hasta MAX_AGE_S. Despues pasa al cementerio, y solo tras
   VENTANA_DE_REAPARICION_S se olvida del todo.

TODO SE MIDE EN SEGUNDOS, NUNCA EN FRAMES DEL ARCHIVO
-------------------------------------------------------
Con FRAME_STRIDE > 1, diez lineas seguidas del .jsonl pueden ser diez frames
reales o cincuenta. Contar eventos para decidir "hace cuanto se vio" haria
que la misma tolerancia real cambiara de tamaño sin que nadie lo note. Por
eso todo usa `evento.timestamp`; `frame` solo sirve para agrupar.

QUE NO RESUELVE (honesto, no una promesa)
-------------------------------------------
Una oclusion casi total entre dos personas que se mueven parecido no siempre
se puede desambiguar sin apariencia: eso produce ID switches reales.
`contar_cruces_sospechosos` cuenta los frames en que el emparejamiento
elegido tambien habria sido valido AL REVES. Es una COTA SUPERIOR, no un
conteo: subestima si tres o mas personas se enredan a la vez, y no sabe si
el cruce ocurrio o si la geometria solo se puso ambigua. Sirve para lo que
el equipo necesita: un numero que empeora si alguien toca el algoritmo.

Tampoco recupera a quien reaparece con un cambio de escala grande, aunque la
posicion se prediga bien (medido contra video_003: un cambio del 62% en la
altura rompe la recuperacion).

EL RENDER Y LOS IMPORTS PESADOS
--------------------------------
Reutiliza el `Renderer` de `detect` con otro color y etiqueta. `color_desde_id`
es una funcion determinista del numero: ninguna tabla, nada derivado de la
persona. Si el video de origen no existe, el .jsonl se escribe igual.

Los imports de OpenCV van DENTRO de las funciones, como en `detect.py`: los
7 compañeros corren `pytest` sin instalar 3 GB.
"""

from __future__ import annotations

import colorsys
import json
import math
import time
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Iterator

from gondola import pipeline
from gondola.config import Config
from gondola.contract import CONTRACT_VERSION, BBox, Event
from gondola.jsonl import read_events, write_events

IOU_MINIMO_PARA_EMPAREJAR = 0.3
"""Por debajo de este solape, dos cajas no se consideran la misma persona.
Mas vale abrir un track nuevo que forzar un emparejamiento dudoso: un track de
mas se ve y se puede filtrar despues, un ID switch no avisa a nadie.

Se probo (y se descarto) hacer que este umbral crezca con el tiempo que un
track lleva perdido -mas exigente cuanto mas viejo-, exactamente para que
subir MAX_AGE_S no fuera gratis. Con datos reales (video_003.mp4) esa version
resulto MENOS estable que esta (el rango de tracks creados entre distintos
MAX_AGE_S paso de 32-33 a 33-40, y de forma no monotona) y encima no
alcanzaba a recuperar el caso concreto que la motivo. La razon: el umbral
creciente y la ventana de espera compartian la misma referencia (MAX_AGE_S),
asi que tocar un numero movia dos comportamientos a la vez de formas dificiles
de predecir. Un sistema estable y explicable vale mas que uno afinado que ya
no se puede defender con claridad. La proteccion contra que un track viejo le
robe la caja a otra persona vive ahora solo en `predecir_bbox`
(amortiguacion de la velocidad): una prediccion que converge a la ultima
posicion conocida, en vez de seguir alejandose, ya es mucho menos probable
que se cruce por casualidad con alguien mas."""

MAX_AGE_S = 2.0
"""Segundos que un track puede pasar sin ninguna deteccion emparejada antes de
darse por perdido. EN SEGUNDOS, no en frames ni en eventos: ver el docstring
del modulo sobre por que (FRAME_STRIDE cambia cuantos eventos representan el
mismo tiempo real).

Medido contra video_003.mp4 (real): una oclusion de 1.53s -alguien tapado por
otra persona que camina enfrente- necesita al menos 1.7s de margen para
recuperar su id; 2.0s le sobra un poco sin llegar a ser un valor arbitrario.
Subirlo ya no es una apuesta ciega gracias a la amortiguacion de velocidad en
`predecir_bbox` (ver mas abajo): un track viejo no sigue extrapolando a la
velocidad -posiblemente ruidosa- con la que se le vio por ultima vez, sino
que converge hacia esa ultima posicion."""

NIVEL_IOU_PARA_DESEMPATE = 0.1
"""Ancho del escalon de IoU dentro del cual dos candidatos se consideran
'igual de buenos' y se desempatan por coherencia de movimiento
(ver `emparejar`). Una diferencia de IoU mayor que esto es una senal
geometrica demasiado fuerte como para que la direccion la contradiga."""

EVENTOS_ENTRE_AVISOS = 2000
"""Cada cuantos eventos se informa del progreso. Esta etapa es mucho mas
rapida que YOLO, pero un video largo igual conviene que avise que sigue viva."""

VELOCIDAD_MINIMA_PARA_COHERENCIA = 20.0

# --------------------------------------------------------------------------
# REAPARICION: recuperar el mismo numero, no estrenar uno
# --------------------------------------------------------------------------
# Cuando un track se da por perdido (MAX_AGE_S sin verse) no se tira a la
# basura: pasa a un 'cementerio'. Si poco despues aparece una deteccion que
# NO empareja con nadie vivo pero cae justo donde estaba uno de esos
# perdidos, se le devuelve SU numero en vez de estrenar uno nuevo. Eso es lo
# que evita que una misma persona salga como 'Persona 1' al principio del
# video y como 'Persona 7' al final.
#
# POR QUE ASI Y NO SUBIENDO MAX_AGE_S
# ------------------------------------
# Es la pregunta obvia, y subir MAX_AGE_S ya se probo: EMPEORA. Medido sobre
# video_001 (2181 detecciones): con 2.0s salen 10 tracks, con 4.0s once, con
# 6.0s doce y con 8.0s trece. Un track vivo pero no visto sigue COMPITIENDO
# en el emparejamiento, flotando sobre una prediccion cada vez peor, y le
# roba detecciones a quien si esta ahi: alargar la vida de los fantasmas
# fragmenta mas, no menos.
#
# El cementerio es otra cosa: un track muerto NO compite por ninguna
# deteccion. Solo se lo consulta en el ultimo momento, cuando ya se decidio
# que esa deteccion no es de nadie vivo y el camino normal seria estrenar un
# numero. No puede robarle nada a nadie; como mucho, evita un numero nuevo.
#
# Las tres condiciones son estrictas a proposito: es preferible un numero de
# mas a fundir dos personas distintas en una, porque eso ultimo no da ningun
# error -solo mezcla dos recorridos y corrompe la permanencia de los dos-.
VENTANA_DE_REAPARICION_S = 4.0
"""Cuanto tiempo despues de perderlo se acepta que alguien reaparezca.

El numero sale de mirar, en los siete videos, cada track que NACE y a que
distancia estaba el que acababa de morir. La distancia mediana por tamano
del hueco:

    hueco 0-3s -> 193 px    (ya se rechazan por lejanos: es otra gente)
    hueco 3-4s ->  41 px    <-- casi no se movieron: la misma persona
    hueco 4-5s ->  86 px
    hueco 5-8s ->  56 px
    hueco  >8s -> 107 px

La banda de 3 a 4 segundos es la de quien se quedo practicamente en el
sitio -41 px es menos de lo que mide una persona de ancho en este
encuadre-, asi que ahi la reaparicion esta bien sostenida. Subirlo a 4.0
baja las identidades de 49 a 44 en los siete videos.

DONDE SE PARA Y POR QUE. Con 5 segundos bajarian a 40, y con 6 a menos.
No se hace: pasados unos segundos, 'alguien parado donde estaba el
anterior' deja de ser evidencia de nada. El sitio frente a un estante es
EXACTAMENTE donde se para la gente, asi que otro cliente ocupandolo es tan
probable como que vuelva el mismo. Sin reidentificacion por apariencia
-prohibida, ver docs/data-contract.md- no hay forma de distinguirlo, y un
numero mas bonito conseguido adivinando no es un numero mejor. Ojo: los
`id_switches_sospechosos` se quedan en 6 con cualquiera de estos valores,
pero eso NO valida nada: ese contador solo mira el emparejamiento por IoU
y la reaparicion ocurre fuera de ahi."""

DISTANCIA_MAXIMA_PARA_REAPARECER = 140.0
"""Pixeles entre donde se perdio y donde reaparece. Quien vuelve al otro lado
del pasillo no es 'el mismo que estaba aqui', es otra persona."""

PROPORCION_DE_TAMANO_PARA_REAPARECER = 2.0
"""Cuanto puede cambiar el area de la caja. Una caja del doble o la mitad
significa que esta al doble de cerca de la camara: no es la misma silueta
volviendo al mismo sitio, aunque el centro coincida."""
"""Px/s por debajo de esto, un track no tiene un rumbo confiable que comparar
(recien creado, o alguien casi quieto): la coherencia de movimiento queda
neutra (1.0) en vez de penalizar una direccion que no significa nada."""


@dataclass
class Track:
    """Lo que sabemos de una silueta seguida: donde estaba y hacia donde iba.

    Vive solo en memoria mientras corre `run()`; no es lo que se escribe en el
    .jsonl (eso es el `track_id`, un entero, dentro de cada `Event`).
    """

    bbox: BBox
    timestamp: float
    velocity: tuple[float, float] = (0.0, 0.0)  # (vx, vy) en pixeles/segundo


@dataclass
class Resumen:
    """Lo que se va contando durante la corrida y acaba en el JSON de resumen."""

    eventos_procesados: int = 0
    tracks_creados: int = 0
    emparejamientos: int = 0
    tracks_activos_al_cierre: int = 0
    id_switches_sospechosos: int = 0
    # Veces que alguien perdido recupero su numero en vez de estrenar uno.
    # Cada una es un 'Persona 1' que no se convirtio en 'Persona 7'.
    reapariciones: int = 0
    # Lo mismo pero SIN que el track llegara a morir: se salvo en la
    # segunda oportunidad del mismo frame (ver _asignar_por_frame).
    rescates: int = 0


# --------------------------------------------------------------------------
# Logica pura: se prueba sin archivos ni video
# --------------------------------------------------------------------------

def iou(a: BBox, b: BBox) -> float:
    """Interseccion sobre union de dos cajas. 0.0 si no se tocan."""
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2 = min(a.x + a.width, b.x + b.width)
    iy2 = min(a.y + a.height, b.y + b.height)
    ancho_i, alto_i = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    interseccion = ancho_i * alto_i
    if interseccion <= 0.0:
        return 0.0
    union = a.width * a.height + b.width * b.height - interseccion
    return interseccion / union if union > 0 else 0.0


def centro(bbox: BBox) -> tuple[float, float]:
    """Centro geometrico de la caja, para medir movimiento.

    No es `bbox.support_point` (los pies): ese punto es para ubicar a la
    persona en el plano del piso (trabajo de la Persona 4), y usarlo aqui
    mezclaria dos ideas distintas. Para seguimiento, el centro de la caja
    basta.
    """
    return (bbox.x + bbox.width / 2, bbox.y + bbox.height / 2)


def predecir_bbox(track: Track, timestamp: float) -> BBox:
    """Donde deberia estar la caja del track AHORA, si siguio a la misma
    velocidad -amortiguada por cuanto lleva perdido.

    Con velocidad (0, 0) -si el track solo tiene una deteccion todavia- la
    prediccion es simplemente su ultima caja: no se asume movimiento sin
    evidencia de al menos dos posiciones.

    LA VELOCIDAD SE AMORTIGUA CON EL TIEMPO PERDIDO. Una velocidad estimada
    con dos observaciones (posiblemente ruidosas) y extrapolada varios
    segundos en linea recta es PEOR que no extrapolar nada -medido contra
    video_003.mp4: un track ocluido durante 1.53s, con una velocidad de solo
    -17.6 px/s estimada de dos frames cercanos, terminaba con una prediccion
    27px mas lejos de la persona real (que apenas se habia movido) que si se
    hubiera asumido quieta. La velocidad efectiva decae en linea recta hasta
    0 segun el track se acerca a MAX_AGE_S sin verse, de forma que la
    prediccion CONVERGE hacia su ultima posicion conocida en vez de seguir
    alejandose indefinidamente. Tiene sentido fisico: si no vi a alguien hace
    1.5 segundos, la mejor apuesta es "sigue cerca de donde estaba", no
    "siguio caminando en linea recta a la velocidad que le medi con dos
    frames".

    `x` e `y` se recortan en 0 porque `BBox` (contract.py) exige coordenadas
    no negativas: una prediccion hacia el borde de la imagen no debe romper
    la validacion.
    """
    dt = timestamp - track.timestamp
    factor_amortiguacion = max(0.0, 1.0 - dt / MAX_AGE_S)
    vx, vy = track.velocity
    vx_efectiva = vx * factor_amortiguacion
    vy_efectiva = vy * factor_amortiguacion
    return BBox(
        x=max(0.0, track.bbox.x + vx_efectiva * dt),
        y=max(0.0, track.bbox.y + vy_efectiva * dt),
        width=track.bbox.width,
        height=track.bbox.height,
    )


def actualizar_track(track: Track, evento: Event) -> None:
    """Registra una nueva deteccion emparejada: mueve el track y reestima su velocidad."""
    dt = evento.timestamp - track.timestamp
    if dt > 0:
        cx0, cy0 = centro(track.bbox)
        cx1, cy1 = centro(evento.detection.bbox)
        track.velocity = ((cx1 - cx0) / dt, (cy1 - cy0) / dt)
    track.bbox = evento.detection.bbox
    track.timestamp = evento.timestamp


def coherencia_de_movimiento(track: Track, prediccion: BBox, evento: Event) -> float:
    """Que tan bien encaja este candidato con el RUMBO que el track ya traia.

    No compara contra la ultima posicion cruda, sino contra el RESIDUAL: hacia
    donde queda el candidato respecto a lo que ya se predijo. Si ese residual
    apunta en la misma direccion que la velocidad establecida, el candidato
    continua el rumbo (cerca de 1.0). Si apunta en la direccion contraria,
    implicaria que la persona invirtio su movimiento en un solo frame (cerca
    de 0.0) -que es exactamente lo que pasa cuando dos identidades se cruzan
    y el emparejamiento las intercambia.

    Es neutro (1.0, no penaliza) cuando no hay rumbo confiable que comparar:
    un track recien creado (velocidad (0,0)), uno casi quieto (por debajo de
    VELOCIDAD_MINIMA_PARA_COHERENCIA), o un candidato que cae practicamente
    encima de la prediccion (residual ~0, nada que indique una direccion).
    """
    vx, vy = track.velocity
    magnitud_velocidad = math.hypot(vx, vy)
    if magnitud_velocidad < VELOCIDAD_MINIMA_PARA_COHERENCIA:
        return 1.0

    cx_p, cy_p = centro(prediccion)
    cx_e, cy_e = centro(evento.detection.bbox)
    residual = (cx_e - cx_p, cy_e - cy_p)
    magnitud_residual = math.hypot(*residual)
    if magnitud_residual < 1.0:
        return 1.0

    coseno = (vx * residual[0] + vy * residual[1]) / (magnitud_velocidad * magnitud_residual)
    return (1.0 + coseno) / 2.0


def emparejar(
    tracks: dict[int, Track], eventos: list[Event]
) -> tuple[dict[int, Event], list[Event]]:
    """Empareja los tracks activos con las detecciones de UN mismo frame.

    Voraz: se ordenan los pares (track, deteccion) por (escalon de IoU,
    coherencia) y se toman mientras ninguno de los dos este usado. No es el
    optimo (eso seria el algoritmo Hungaro), pero con la poca gente que hay
    frente a una gondola la diferencia es despreciable y evita depender de
    scipy solo para esto.

    TRES DECISIONES, CADA UNA CON SU CICATRIZ
    ------------------------------------------
    1. EL IoU MANDA; LA COHERENCIA SOLO DESEMPATA dentro del mismo escalon
       (NIVEL_IOU_PARA_DESEMPATE). Se intento primero multiplicar
       iou*coherencia, y en video_003 un IoU de 0.99 -una persona quieta-
       perdio contra uno de 0.32 porque la caja de YOLO temblo un poco de
       alto: la direccion de un residual minusculo es ruido, no una reversa,
       y fragmentaba tracks que no tenian nada de ambiguo.

    2. LA COHERENCIA HACE FALTA. Dos personas que se cruzan en direcciones
       opuestas pueden dar EL MISMO IoU a las dos cajas del otro lado -un
       empate real-, y sin mas informacion decidiria el orden en que YOLO
       entrego las cajas. La direccion si distingue: la opcion correcta
       continua el rumbo, la cruzada implica invertir el movimiento en un
       frame.

    3. LA PREDICCION NO PUEDE SER PEOR QUE QUEDARSE QUIETO. El IoU de cada
       par es el MEJOR entre la prediccion y la ultima caja conocida.
       Extrapolar ayuda a quien sigue caminando y perjudica a quien FRENA
       -justo lo que hace un cliente al llegar a la gondola-. Medido: el
       track 4 daba su ultima señal en t=1.62s y el 11 nacia en t=1.71s A UN
       PIXEL. Tomar el maximo no relaja el umbral ni cambia el desempate;
       solo deja de castigar al track cuya prediccion se equivoco.

    Devuelve (emparejados: {track_id: evento}, sin_emparejar: detecciones que
    no encontraron track).
    """
    timestamp = eventos[0].timestamp
    candidatos: list[tuple[tuple[float, float], int, int]] = []
    for track_id, track in tracks.items():
        prediccion = predecir_bbox(track, timestamp)
        for indice, evento in enumerate(eventos):
            # El MEJOR de dos lecturas: donde la velocidad dice que deberia
            # estar, y donde estaba la ultima vez que se vio. Ver
            # "LA PREDICCION NO PUEDE SER PEOR QUE QUEDARSE QUIETO", arriba.
            valor = max(
                iou(prediccion, evento.detection.bbox),
                iou(track.bbox, evento.detection.bbox),
            )
            if valor >= IOU_MINIMO_PARA_EMPAREJAR:
                escalon = round(valor / NIVEL_IOU_PARA_DESEMPATE)
                coherencia = coherencia_de_movimiento(track, prediccion, evento)
                candidatos.append(((escalon, coherencia), track_id, indice))
    candidatos.sort(key=lambda c: c[0], reverse=True)

    usados_tracks: set[int] = set()
    usados_eventos: set[int] = set()
    emparejados: dict[int, Event] = {}
    for _valor, track_id, indice in candidatos:
        if track_id in usados_tracks or indice in usados_eventos:
            continue
        emparejados[track_id] = eventos[indice]
        usados_tracks.add(track_id)
        usados_eventos.add(indice)

    sin_emparejar = [e for i, e in enumerate(eventos) if i not in usados_eventos]
    return emparejados, sin_emparejar


def contar_cruces_sospechosos(
    tracks: dict[int, Track], emparejados: dict[int, Event], timestamp: float
) -> int:
    """Cuenta pares de tracks cuyo emparejamiento en ESTE frame se pudo haber
    intercambiado sin violar el umbral de IoU.

    Se llama ANTES de actualizar los tracks (con su posicion previa a este
    frame): para cada par de tracks emparejados (ta -> ea, tb -> eb), si
    tambien habria sido valido asignar ta -> eb y tb -> ea (las dos con IoU
    por encima de IOU_MINIMO_PARA_EMPAREJAR), la geometria por si sola no
    alcanzaba para distinguirlos. Eso es exactamente la situacion que produce
    un ID switch real: dos identidades que se pudieron haber cruzado entre
    este frame y el anterior.

    Es una senal SOSPECHOSA, no una certeza: sin apariencia ni groundtruth no
    se puede confirmar que el intercambio de verdad ocurrio, solo que la
    geometria no lo descarta. Ver el docstring del modulo.
    """
    ids = list(emparejados.keys())
    sospechosos = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            id_a, id_b = ids[i], ids[j]
            pred_a = predecir_bbox(tracks[id_a], timestamp)
            pred_b = predecir_bbox(tracks[id_b], timestamp)
            cruzado_a_con_b = iou(pred_a, emparejados[id_b].detection.bbox)
            cruzado_b_con_a = iou(pred_b, emparejados[id_a].detection.bbox)
            if (cruzado_a_con_b >= IOU_MINIMO_PARA_EMPAREJAR
                    and cruzado_b_con_a >= IOU_MINIMO_PARA_EMPAREJAR):
                sospechosos += 1
    return sospechosos


def color_desde_id(track_id: int) -> tuple[int, int, int]:
    """Color BGR estable y distinto por track_id, para pintarlo en el video.

    Es una funcion matematica determinista del numero (angulo dorado sobre el
    circulo de tono, para que colores consecutivos se vean bien distintos): no
    se guarda ninguna tabla ni se deriva de nada de la persona. El mismo
    track_id siempre da el mismo color, en cualquier frame del video.
    """
    tono = (track_id * 0.618033988749895) % 1.0  # razon aurea: tonos bien repartidos
    r, g, b = colorsys.hsv_to_rgb(tono, 0.65, 0.95)
    return (int(b * 255), int(g * 255), int(r * 255))  # BGR: el orden de OpenCV


def buscar_reaparicion(
    cementerio: dict[int, Track], evento: Event, timestamp: float,
    ya_usados: set[int],
) -> int | None:
    """El track perdido que mejor explica esta deteccion, o None.

    Se devuelve el MAS CERCANO de los que cumplen las tres condiciones (ver
    las constantes de arriba). Si ninguno cumple, None: quien llama estrena
    un numero, que es lo correcto cuando no hay evidencia de que sea alguien
    ya conocido.
    """
    area_nueva = evento.detection.bbox.width * evento.detection.bbox.height
    cx, cy = centro(evento.detection.bbox)
    mejor: tuple[float, int] | None = None

    for track_id, track in cementerio.items():
        if track_id in ya_usados:
            continue
        if timestamp - track.timestamp > VENTANA_DE_REAPARICION_S:
            continue
        px, py = centro(track.bbox)
        distancia = math.hypot(cx - px, cy - py)
        if distancia > DISTANCIA_MAXIMA_PARA_REAPARECER:
            continue
        area_vieja = track.bbox.width * track.bbox.height
        if area_vieja <= 0:
            continue
        proporcion = area_nueva / area_vieja
        if not (1 / PROPORCION_DE_TAMANO_PARA_REAPARECER <= proporcion
                <= PROPORCION_DE_TAMANO_PARA_REAPARECER):
            continue
        if mejor is None or distancia < mejor[0]:
            mejor = (distancia, track_id)

    return mejor[1] if mejor else None


def _asignar_por_frame(
    eventos: Iterator[Event], resumen: Resumen
) -> Iterator[tuple[int, list[Event]]]:
    """El nucleo del seguimiento: agrupa por frame y va asignando track_id.

    Entrega (numero_de_frame, eventos_de_ese_frame_con_track_id) en orden, en
    vez de eventos sueltos, para que quien dibuje el video (`_procesar`) pueda
    emparejar cada grupo con su frame real sin volver a agrupar nada.
    """
    tracks: dict[int, Track] = {}
    # Tracks perdidos que todavia pueden reaparecer. No compiten por ninguna
    # deteccion: solo se consultan antes de estrenar un numero nuevo (ver
    # buscar_reaparicion).
    cementerio: dict[int, Track] = {}
    siguiente_id = 1

    for frame, grupo_iter in groupby(eventos, key=lambda e: e.frame):
        grupo = list(grupo_iter)
        timestamp = grupo[0].timestamp

        # Los tracks que llevan demasiado sin verse se dan por perdidos ANTES
        # de emparejar, para que no le "roben" el track_id a una persona
        # distinta que aparece despues en el mismo sitio.
        vencidos = [tid for tid, tr in tracks.items() if timestamp - tr.timestamp > MAX_AGE_S]
        for tid in vencidos:
            cementerio[tid] = tracks.pop(tid)

        # Los que ya ni pueden reaparecer se olvidan del todo: el cementerio
        # no crece sin limite aunque el video dure horas.
        for tid in [t for t, tr in cementerio.items()
                    if timestamp - tr.timestamp > VENTANA_DE_REAPARICION_S]:
            del cementerio[tid]

        emparejados, sin_emparejar = emparejar(tracks, grupo)
        resumen.id_switches_sospechosos += contar_cruces_sospechosos(
            tracks, emparejados, timestamp
        )

        for track_id, evento in emparejados.items():
            actualizar_track(tracks[track_id], evento)
            evento.track_id = track_id
            resumen.emparejamientos += 1

        # SEGUNDA OPORTUNIDAD, antes de estrenar numeros.
        #
        # Los que no encontraron pareja se miran dos veces mas, con la
        # regla de posicion y tamano en vez de con IoU:
        #
        #   1. contra los tracks VIVOS que este frame se quedaron sin
        #      nada. Caso real medido en video_001: alguien se movia y su
        #      caja se encogio a la mitad de area en un solo frame (se
        #      alejo de la camara); a 94 px de distancia el IoU no llegaba
        #      al umbral y nacia 'Persona 2' encima de 'Persona 1', que
        #      seguia viva. El cementerio no lo cubre justamente porque el
        #      track NO habia muerto.
        #   2. contra los tracks ya perdidos (el cementerio).
        #
        # En ese orden a proposito: un track vivo tiene informacion mas
        # fresca que uno muerto, asi que se le da preferencia.
        vivos_sin_pareja = {
            tid: tr for tid, tr in tracks.items() if tid not in emparejados
        }
        usados_en_este_frame: set[int] = set()
        for evento in sin_emparejar:
            segunda = buscar_reaparicion(
                vivos_sin_pareja, evento, timestamp, usados_en_este_frame
            )
            if segunda is not None:
                usados_en_este_frame.add(segunda)
                actualizar_track(tracks[segunda], evento)
                evento.track_id = segunda
                resumen.rescates += 1
                continue

            # Es alguien que ya estaba y se perdio del todo? Recupera SU numero.
            reaparecido = buscar_reaparicion(
                cementerio, evento, timestamp, usados_en_este_frame
            )
            if reaparecido is not None:
                usados_en_este_frame.add(reaparecido)
                tracks[reaparecido] = cementerio.pop(reaparecido)
                actualizar_track(tracks[reaparecido], evento)
                evento.track_id = reaparecido
                resumen.reapariciones += 1
                continue

            tracks[siguiente_id] = Track(bbox=evento.detection.bbox, timestamp=evento.timestamp)
            evento.track_id = siguiente_id
            resumen.tracks_creados += 1
            siguiente_id += 1

        resumen.eventos_procesados += len(grupo)
        resumen.tracks_activos_al_cierre = len(tracks)
        if resumen.eventos_procesados % EVENTOS_ENTRE_AVISOS < len(grupo):
            _avisar_progreso(resumen)

        yield frame, grupo


def asignar_track_ids(eventos: Iterator[Event], resumen: Resumen) -> Iterator[Event]:
    """Recorre los eventos en orden y les va asignando track_id, de a un frame.

    Es un generador: solo mantiene en memoria los tracks activos y las
    detecciones del frame que esta procesando, nunca el archivo completo.
    """
    for _frame, grupo in _asignar_por_frame(eventos, resumen):
        yield from grupo


def _avisar_progreso(resumen: Resumen) -> None:
    print(
        f"  eventos {resumen.eventos_procesados:>7}   "
        f"tracks_creados {resumen.tracks_creados:>5}   "
        f"emparejamientos {resumen.emparejamientos:>7}"
    )


# --------------------------------------------------------------------------
# Render: reutiliza gondola/video/, no reimplementa nada de OpenCV aqui
# --------------------------------------------------------------------------

def _fusionar_con_video(grupos, frames_de_video):
    """Empareja los grupos (frame, eventos) con los frames reales del video.

    Un frame de video sin ninguna deteccion se entrega con lista vacia -igual
    que hace `detect`-, para que el video de salida tenga tantos frames como
    el original. Si el video se acaba antes que los datos (por ejemplo,
    porque se reconfiguro MAX_FRAMES entre una corrida y otra), lo que sobra
    se entrega igual, con `imagen=None`, para que NUNCA se pierda un evento
    del .jsonl por culpa del render: dibujar es secundario, escribir el
    contrato no.
    """
    grupos_iter = iter(grupos)
    frame_actual, grupo_actual = next(grupos_iter, (None, None))
    for indice, timestamp, imagen in frames_de_video:
        if frame_actual == indice:
            eventos = grupo_actual
            frame_actual, grupo_actual = next(grupos_iter, (None, None))
        else:
            eventos = []
        yield indice, timestamp, imagen, eventos

    while frame_actual is not None:
        yield frame_actual, None, None, grupo_actual
        frame_actual, grupo_actual = next(grupos_iter, (None, None))


def _procesar(cfg: Config, entrada: Path, resumen: Resumen) -> Iterator[Event]:
    """Asigna track_id y, si hay video disponible, dibuja el resultado.

    Reutiliza `gondola.video.render.Renderer` (el mismo que usa `detect`, en
    los mismos modos privacy/debug), pasandole el color y la etiqueta por
    track_id en vez de las de confianza. Sin video, o con RENDER_MODE=none,
    el .jsonl se escribe exactamente igual: el render es un extra, nunca un
    requisito.
    """
    grupos = _asignar_por_frame(read_events(entrada), resumen)

    if cfg.render_mode == "none":
        for _frame, grupo in grupos:
            yield from grupo
        return

    if not cfg.video_path.exists():
        print(f"[track] Aviso: no encuentro el video en {cfg.video_path}; "
              f"no se puede renderizar. El .jsonl se escribe igual.")
        for _frame, grupo in grupos:
            yield from grupo
        return

    from gondola.video.reader import VideoReader
    from gondola.video.render import Renderer

    # Numero que se DIBUJA, no el track_id. El track_id es un contador
    # global que solo sube: cada track que se abre y se descarta consume uno,
    # asi que en un video con una sola persona se acababa viendo "id 7"
    # mientras la cabecera decia "personas: 1". Aqui se renumera desde 1 en
    # orden de aparicion. Como el render va en streaming (grupos es un
    # generador que se consume en orden de frame) se construye sobre la
    # marcha: da el mismo resultado que recorrerlo todo antes, sin tener que
    # cargar el archivo entero en memoria. El track_id del .jsonl no cambia.
    numeros_por_track: dict[int, int] = {}

    def numero_visible(evento) -> str:
        tid = evento.track_id
        if tid is None:
            return "?"
        if tid not in numeros_por_track:
            numeros_por_track[tid] = len(numeros_por_track) + 1
        return str(numeros_por_track[tid])

    video_salida = pipeline.render_path("track", cfg, cfg.render_mode)
    with VideoReader(cfg.video_path) as video, Renderer(
        video_salida, cfg.render_mode, video.info.width, video.info.height, video.info.fps
    ) as renderer:
        print(f"[track] Render: {cfg.render_mode}  ->  {video_salida.name}")
        for indice, timestamp, imagen, grupo in _fusionar_con_video(
            grupos, video.frames(cfg.frame_stride, cfg.max_frames)
        ):
            if imagen is not None:
                renderer.write(
                    imagen, grupo, indice, timestamp,
                    color_de=lambda e: color_desde_id(e.track_id),
                    etiqueta_de=lambda e: f"id {numero_visible(e)}",
                )
            yield from grupo


# --------------------------------------------------------------------------
# Punto de entrada de la etapa
# --------------------------------------------------------------------------

def run(cfg: Config, abrir_video: bool = False) -> int:
    """Ejecuta el seguimiento completo. Devuelve el codigo de salida.

    `abrir_video` (igual que en `detect`): al terminar, abre el video anotado
    con el reproductor por defecto del sistema. No hace nada si RENDER_MODE es
    "none" o si el video no se pudo generar (por ejemplo, porque el video de
    origen no existia).
    """
    rutas = pipeline.stage_paths("track", cfg)
    pipeline.require_input("track", cfg)

    print(f"[track] Entrada: {rutas.input_path}")
    print(f"[track] iou_minimo={IOU_MINIMO_PARA_EMPAREJAR}  max_age={MAX_AGE_S}s")
    print()

    resumen = Resumen()
    inicio = time.perf_counter()
    escritos = write_events(rutas.output_path, _procesar(cfg, rutas.input_path, resumen))
    transcurrido = time.perf_counter() - inicio

    ruta_resumen = pipeline.summary_path("track", cfg)
    _escribir_resumen(ruta_resumen, cfg, resumen, transcurrido)

    video_salida = pipeline.render_path("track", cfg, cfg.render_mode)
    _imprimir_resultado(resumen, escritos, transcurrido, rutas.output_path, ruta_resumen,
                       cfg, video_salida)

    if abrir_video and cfg.render_mode != "none" and video_salida.exists():
        from gondola.video.render import abrir_con_el_sistema

        abrir_con_el_sistema(video_salida)

    return 0


def _leer_info_de_video_desde_detect(cfg: Config) -> dict:
    """Lee width/height/fps del resumen de `detect`, para que `verify` tambien
    pueda comprobar "bbox cabe dentro del frame" y "timestamp coincide con
    frame/fps" sobre la salida de esta etapa (si no los propagamos, verify
    omite esas dos reglas para track.jsonl aunque las tenga para detect.jsonl).

    Si el resumen anterior no existe, esta corrupto o no trae esos campos, no
    se falla: track no depende de que detect se haya corrido con render
    activado ni de nada mas alla de su propia entrada; solo aprovecha el dato
    si esta.
    """
    ruta = pipeline.summary_path("detect", cfg)
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return datos.get("video", {})


def _escribir_resumen(destino: Path, cfg: Config, resumen: Resumen, transcurrido: float) -> None:
    """Guarda las metricas de la corrida. Sin esto no se puede comparar nada."""
    datos = {
        "contract_version": CONTRACT_VERSION,
        "stage": "track",
        "video_id": cfg.video_id,
        "video": _leer_info_de_video_desde_detect(cfg),
        "params": {
            "iou_minimo_para_emparejar": IOU_MINIMO_PARA_EMPAREJAR,
            "max_age_s": MAX_AGE_S,
            "ventana_de_reaparicion_s": VENTANA_DE_REAPARICION_S,
            "distancia_maxima_para_reaparecer": DISTANCIA_MAXIMA_PARA_REAPARECER,
        },
        "results": {
            "eventos_procesados": resumen.eventos_procesados,
            "tracks_creados": resumen.tracks_creados,
            "emparejamientos": resumen.emparejamientos,
            "tracks_activos_al_cierre": resumen.tracks_activos_al_cierre,
            "id_switches_sospechosos": resumen.id_switches_sospechosos,
            "reapariciones": resumen.reapariciones,
            "rescates": resumen.rescates,
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
    cfg: Config, video_salida: Path,
) -> None:
    print()
    print("-" * 66)
    print(f"  Eventos procesados     {resumen.eventos_procesados}")
    print(f"  Tracks creados         {resumen.tracks_creados}")
    print(f"  Emparejamientos        {resumen.emparejamientos}")
    print(f"  Tracks activos al cierre  {resumen.tracks_activos_al_cierre}  "
          f"(quedaron 'vivos' al terminar el video)")
    print(f"  Reapariciones          {resumen.reapariciones}  "
          f"(veces que alguien perdido recupero SU numero en vez de estrenar uno)")
    print(f"  Rescates               {resumen.rescates}  "
          f"(enganchados en segunda oportunidad, sin llegar a perderse)")
    print(f"  ID switches sospechosos   {resumen.id_switches_sospechosos}  "
          f"(cota superior sin groundtruth; ver docstring del modulo)")
    print(f"  Tiempo                 {transcurrido:.2f} s")
    print("-" * 66)
    print(f"  Eventos   {jsonl}  ({escritos} lineas)")
    print(f"  Resumen   {ruta_resumen}")
    if cfg.render_mode != "none" and video_salida.exists():
        print(f"  Video     {video_salida}")
    print()
    print("  Siguiente etapa:  python -m gondola zones   (Persona 4)")
