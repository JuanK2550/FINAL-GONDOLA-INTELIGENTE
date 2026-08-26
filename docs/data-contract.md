# Contrato de datos

**Version:** 1.0.0 · **Codigo:** `ai-service/gondola/contract.py`

Este documento describe el unico formato de datos del proyecto. Si algo no esta
aqui, no existe en el sistema.

---

## 1. La idea en una frase

Un **evento** es *una persona detectada en un frame*. Las etapas del pipeline no
crean formatos nuevos: reciben el mismo evento y le **rellenan sus campos**.

```
    Persona 2          Persona 3         Persona 4              Persona 5
   (deteccion)       (seguimiento)   (zonas y permanencia)   (interaccion)
       |                  |                  |                    |
       v                  v                  v                    v
   detection  ------>  track_id  ------>  zone            ------> interaction
                                          metrics.dwell_time
```

Todos los campos que otra etapa debe rellenar nacen en `null`. Un `null` no es
un error: significa "esa etapa todavia no ha pasado por aqui".

---

## 2. El evento completo

```json
{
  "video_id": "video_001",
  "frame": 253,
  "timestamp": 8.43,
  "track_id": null,
  "detection": {
    "class": "person",
    "confidence": 0.94,
    "bbox": {"x": 145.0, "y": 40.0, "width": 90.0, "height": 150.0}
  },
  "zone": {"zone_id": null, "segment": null},
  "interaction": {"event": null, "product_zone": null},
  "metrics": {"dwell_time": null}
}
```

Se guarda en archivos `.jsonl`: **un evento completo por linea**, sin comas ni
corchetes entre lineas. Asi el archivo se puede leer de a poquitos aunque pese
gigabytes, y una linea corrupta no arruina el resto.

---

## 3. Que significa cada campo

### Nivel raiz

| Campo | Tipo | Quien lo pone | Que significa |
|---|---|---|---|
| `video_id` | texto | Fase 2 (lector) | Etiqueta del video. Sale del `.env` (`VIDEO_ID`). Permite mezclar varios videos en un mismo analisis sin confundirlos. |
| `frame` | entero >= 0 | Fase 2 (lector) | Numero de frame dentro del video. |
| `timestamp` | decimal >= 0 | Fase 2 (lector) | Segundos desde el inicio del video. Es `frame / fps`. Se guarda aparte porque nadie quiere andar dividiendo por los fps en cada consulta. |
| `track_id` | entero o `null` | **Persona 3** | Identificador **temporal** de seguimiento. Ver la advertencia de privacidad mas abajo. |

### `detection` — la rellena la **Persona 2** (YOLO)

| Campo | Tipo | Que significa |
|---|---|---|
| `class` | texto | Que detecto el modelo. Hoy siempre `"person"`. En Python el atributo se llama `class_name` porque `class` es palabra reservada; en el JSON **siempre** sale como `"class"`. |
| `confidence` | decimal 0.0 a 1.0 | Cuanta certeza tiene el modelo. `0.94` = 94%. Se descartan las detecciones por debajo de `CONFIDENCE_THRESHOLD`. |
| `bbox` | objeto | La caja que rodea a la persona. Ver la seccion siguiente. |

### `bbox` — LEER ESTO CON ATENCION

| Campo | Tipo | Que significa |
|---|---|---|
| `x` | decimal >= 0 | Borde **izquierdo** de la caja, en pixeles. |
| `y` | decimal >= 0 | Borde **superior** de la caja, en pixeles. |
| `width` | decimal > 0 | **Ancho de la caja, en pixeles.** |
| `height` | decimal > 0 | **Alto de la caja, en pixeles.** |

> ### `width` y `height` son PIXELES DE LA CAJA
>
> **NO son la estatura de la persona. NO son su peso. NO son su contextura.**
>
> Una misma persona produce cajas completamente distintas segun que tan lejos
> este de la camara: cerca da `height=400`, al fondo del pasillo da `height=90`.
> Y una persona agachada da una caja baja y ancha sin haber encogido.
>
> El sistema **no mide personas**: mide rectangulos en una imagen. Nadie debe
> derivar de `bbox` ninguna caracteristica fisica, y ninguna etapa debe
> escribirla.

El origen de coordenadas es la **esquina superior izquierda** de la imagen, y
la `y` **crece hacia abajo** (convencion de OpenCV y YOLO). Por eso el borde
inferior es `y + height`, no `y - height`.

```
   (0,0) ------------------------------> x
     |
     |        (x, y) +----------+
     |               |          |
     |               |  person  |  height
     |               |          |
     |               +-----*----+
     |                     ^   width
     |            support_point = (x + width/2, y + height)
     v y
```

### `support_point` — el punto de apoyo (calculado, no almacenado)

Es el **centro del borde inferior** de la caja: `(x + width/2, y + height)`.
Aproxima el punto donde la persona toca el piso.

Por que existe: para saber si alguien esta frente a la gondola hay que ubicarlo
en el **plano del piso**. El centro de la caja no sirve, porque "flota" a la
altura del pecho y falsea las distancias — dos personas a la misma distancia de
la gondola dan centros a alturas distintas segun su estatura y su postura.

Es una **propiedad calculada en Python**, no un campo del JSON: se deriva de la
caja, asi que guardarlo seria informacion duplicada que puede quedar desfasada.
Se usa asi:

```python
x_pies, y_pies = evento.detection.bbox.support_point
```

Lo adelantamos en la Fase 1 para que la Persona 4 no lo reinvente mal.

### `zone` — la rellena la **Persona 4**

| Campo | Tipo | Que significa |
|---|---|---|
| `zone_id` | texto o `null` | En que zona de la tienda esta. Ej: `"gondola_A"`. |
| `segment` | texto o `null` | Que parte de esa zona. Ej: `"estante_2"`. |

### `interaction` — la rellena la **Persona 5**

| Campo | Tipo | Que significa |
|---|---|---|
| `event` | enum o `null` | `"APPROACH"` (se acerca), `"PICK_UP"` (toma un producto), `"PUT_BACK"` (lo devuelve). Solo estos tres. |
| `product_zone` | texto o `null` | Sobre que grupo de productos ocurrio. Ej: `"bebidas"`. |

### `metrics` — la rellena la **Persona 4**

| Campo | Tipo | Que significa |
|---|---|---|
| `dwell_time` | decimal >= 0 o `null` | Segundos acumulados que ese `track_id` lleva en esa zona. Es la metrica central del reto: donde se detiene la gente. |

---

## 4. Como se enriquece el evento, etapa por etapa

### Etapa 1 — Deteccion (Persona 2)

Encuentra personas en el frame y crea el evento.

```json
{"video_id": "video_001", "frame": 253, "timestamp": 8.43, "track_id": null,
 "detection": {"class": "person", "confidence": 0.94,
               "bbox": {"x": 145.0, "y": 40.0, "width": 90.0, "height": 150.0}},
 "zone": {"zone_id": null, "segment": null},
 "interaction": {"event": null, "product_zone": null},
 "metrics": {"dwell_time": null}}
```

### Etapa 2 — Seguimiento (Persona 3): aparece `track_id`

Enlaza la caja del frame 253 con la del frame 252: es "la misma silueta".

```json
{"...": "...", "track_id": 7}
```

### Etapa 3 — Zonas y permanencia (Persona 4): aparecen `zone` y `dwell_time`

Toma el `support_point`, mira en que zona del piso cae y acumula el tiempo.

```json
{"...": "...", "track_id": 7,
 "zone": {"zone_id": "gondola_A", "segment": "estante_2"},
 "metrics": {"dwell_time": 12.5}}
```

### Etapa 4 — Interaccion (Persona 5): aparece `interaction`

Detecta el gesto hacia el estante.

```json
{"...": "...", "interaction": {"event": "PICK_UP", "product_zone": "bebidas"}}
```

### Resultado final

```json
{"video_id": "video_001", "frame": 253, "timestamp": 8.43, "track_id": 7,
 "detection": {"class": "person", "confidence": 0.94,
               "bbox": {"x": 145.0, "y": 40.0, "width": 90.0, "height": 150.0}},
 "zone": {"zone_id": "gondola_A", "segment": "estante_2"},
 "interaction": {"event": "PICK_UP", "product_zone": "bebidas"},
 "metrics": {"dwell_time": 12.5}}
```

Lo que dice esta linea: *"a los 8.43 segundos, una silueta llevaba 12.5 segundos
frente al estante 2 de la gondola A y tomo un producto de bebidas"*. Suficiente
para optimizar un planograma. Y no dice absolutamente nada sobre **quien** era.

---

## 5. Privacidad por diseno

Estan **prohibidos** en el contrato, hoy y siempre:

- edad, rango etario, genero
- rostro, vectores faciales, `embeddings`, huellas o cualquier dato biometrico
- identidad, nombre, documento
- emocion, estado de animo
- cualquier caracteristica fisica derivada de `bbox`

Esto no es una recomendacion en un documento: todos los modelos usan
`extra="forbid"`, asi que **anadir un campo prohibido rompe la validacion al
instante**, en la maquina de quien lo intento, antes de llegar a la integracion.
Hay un test que lo comprueba (`test_un_campo_prohibido_de_biometria_falla`).

### Sobre `track_id`

`track_id` es un numero **temporal y sin significado**: solo dice "esta caja y
aquella son la misma silueta dentro de este video". Se reinicia con cada video y
no corresponde a ninguna persona real. Si alguien sale y vuelve a entrar, recibe
un `track_id` distinto, y eso esta bien: **no queremos** reconocerlo.

---

## 6. Reglas para todo el equipo

1. **No inventes campos.** Si de verdad falta uno, se discute con el equipo, se
   sube `CONTRACT_VERSION` y se actualiza este documento. Nunca por tu cuenta.
2. **Rellena solo lo tuyo.** Tu etapa no toca los campos de otra etapa.
3. **Serializa con `Event.to_jsonl()`**, nunca con `json.dumps()` a mano: el
   campo `class` necesita el alias y a mano se te va a olvidar.
4. **Lee con `Event.from_jsonl()`**, que valida. Si el archivo esta corrupto,
   quieres enterarte al leerlo, no tres etapas despues.
5. Un `null` significa "todavia no", no "error".
