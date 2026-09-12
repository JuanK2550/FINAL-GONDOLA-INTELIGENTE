# data/videos/

## >>> COLOCAR AQUI LOS VIDEOS <<<

Deja el archivo de video de la tienda en esta carpeta y apunta a el desde tu
`.env`:

    VIDEO_PATH=data/videos/video_001.mp4
    VIDEO_ID=video_001

Reglas:

- Los videos **NO se suben a git** (pesan mucho y contienen imagenes de
  personas reales). `.gitignore` los excluye a proposito.
- Los seis clips (`video_001.mp4`..`video_006.mp4`) salen de la MISMA
  camara fija en el mismo local: 920x680 a 30 fps, entre 2 y 3,5 minutos
  cada uno. Por eso comparten una sola calibracion de zonas.
- No estan en git. Se reparten por el chat de WhatsApp del equipo.
- **El nombre del archivo NO importa, no hace falta renombrar nada al
  guardarlo.** WhatsApp (o quien te lo mande) le pone el nombre que quiera
  (`VID-20260904-WA0002.mp4`, por ejemplo): si es el UNICO video en esta
  carpeta, `gondola/config.py` lo detecta solo, sin tocar `.env`. Si vas a
  tener mas de uno a la vez (para procesar varios videos distintos), ahi si
  usa `VIDEO_PATH` para decirle a cual apuntar -ver mas abajo-.
- `VIDEO_ID` es la etiqueta corta que quedara escrita en cada evento de salida.
  Si trabajas con otro video, cambia tambien el `VIDEO_ID`.

Los seis se importan a PostgreSQL como `video_001`..`video_006` (ver
`data/zones/README.md` para su calibracion; en algunas maquinas quedaron
importados antes con nombres viejos del tipo `video_demo_merl_*`).
