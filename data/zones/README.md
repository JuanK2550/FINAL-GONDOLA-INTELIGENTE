# data/zones/

Archivos de calibracion de camara: donde estan las gondolas y estantes de una
tienda, en pixeles. Formato completo: [`docs/zones-format.md`](../../docs/zones-format.md).

- `video_001.example.json` — **ejemplo**, calibrado a ojo contra
  `data/videos/video_001.mp4` con `scripts/draw_zones.py`. Sirve para
  desarrollar y probar sin depender de que alguien mas haya calibrado ya el
  video real. No lo trates como calibracion final: revisalo con la
  herramienta antes de confiar en el.
- `video_001.json`..`video_006.json` — **una sola calibracion, compartida
  por los seis a proposito.** Los seis clips salen de la MISMA camara fija
  en el MISMO local (el del **MERL Shopping Dataset**): mismo encuadre,
  misma resolucion 920x680, misma estanteria. Calibrar cada uno por
  separado daria seis copias del mismo rectangulo, no seis calibraciones.
  1 gondola con 2 estantes: "Cereales" (izquierda) y "Snacks y pasabocas"
  (derecha), separados en x=530, que es donde esta la division real de la
  estanteria en la imagen.

  La `floor_zone` de cada estante es el AREA DE PISO donde se para la
  gente frente a ese estante, no donde estan los productos (a las personas
  se las ubica por los pies, ver `docs/zones-format.md`). Sale de medir
  las 12.725 posiciones de pies de los seis videos, no de dibujarla a ojo:
  la version anterior (`x` 120-900, `y` 270-650) cubria el escritorio de
  la izquierda -por donde no camina nadie- y cortaba en `y=650` justo
  donde SI esta la gente, dejando fuera entre el 6% y el 28% del transito
  segun el video. La actual (`y` 440-680) cubre el 98-100%.

  Para los `video_id` viejos: en algunas maquinas
  esos mismos clips quedaron importados antes con el nombre viejo
  `video_demo_merl_24_3`/`_15_3`/`_39_1`/`_18_3`/`_36_1`: no hace
  falta recalibrar esos, solo re-importar con el `video_id` nuevo cuando se
  pueda (ver `VIDEOS_REALES_CONOCIDOS` en `frontend/index.html`, que
  reconoce ambos nombres mientras tanto).
- A diferencia de los videos y los modelos, **estos archivos SI se versionan
  en git**: son texto plano, pequenos, y no contienen ninguna imagen ni dato
  de personas -son coordenadas de una camara fija, no de gente.

Para calibrar un video nuevo: copia el ejemplo, ajusta las coordenadas, y
revisa el resultado con

    python scripts/draw_zones.py --zones data/zones/<tu_archivo>.json --frame <algun_frame_con_gente>
