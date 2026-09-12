// Subir un video desde el navegador
// Parte del dashboard de Gondola Inteligente. Se carga desde index.html
// como <script> clasico (no modulo ES): ver el comentario de index.html.

// ==========================================================================
// SUBIR UN VIDEO (portado de una version paralela del dashboard, hecha por
// un companero de equipo -ver backend/uploads.py para el lado servidor-).
// --------------------------------------------------------------------------
// Seis pasos, uno por pantalla del mismo modal:
//   terminos -> subiendo -> revisando -> zonas -> procesando -> listo
//                                    \-> rechazado
//
// El paso "zonas" no se puede saltar: las zonas son rectangulos en pixeles
// de UNA camara concreta (ver docs/zones-format.md), y un video que sube
// alguien viene de otra. Sin ese dibujo, el backend no puede medir por
// estante -y su importador se niega a importar-. Es preferible pedir treinta
// segundos de calibracion que inventar unas zonas por defecto y mostrar
// metricas por estante que no significan nada.
// ==========================================================================

// El frame de fondo y el temporizador de sondeo viven FUERA de state: son un
// objeto Image() y un id de setTimeout, no datos que la interfaz pinte, y
// meterlos en state solo haria ruido en cada setState().
let imagenCalibracion = null;
let sondeoSubida = null;

// sondearTrabajo() llama a setState() cada 2s mientras el prevuelo/proceso
// esta en marcha, y setState() reconstruye #root ENTERO (incluido este
// modal, ver app.js) en cada vuelta -incluidas las clases
// 'modal-backdrop'/'modal-anim', que traen una animacion de ENTRADA (fade
// + escala). Al recrear el mismo nodo con las mismas clases cada 2s, el
// navegador vuelve a disparar esa animacion de entrada una y otra vez: el
// modal "parpadea" (se ve pestanear) todo el tiempo que dura la subida,
// no solo al abrirse -bug real, reportado en la practica-. Con esta bandera
// la animacion de entrada se pinta SOLO la primera vez que el modal
// aparece en pantalla; los repintados de despues (con contenido
// actualizado: la barra de progreso, el mensaje) no vuelven a animarse.
let subidaYaAnimada = false;

function abrirSubida() {
  imagenCalibracion = null;
  subidaYaAnimada = false;
  setState({ subida: { ...SUBIDA_VACIA(), abierto: true } });
}

function cerrarSubida() {
  clearTimeout(sondeoSubida);
  sondeoSubida = null;
  imagenCalibracion = null;
  subidaYaAnimada = false;
  setState({ subida: SUBIDA_VACIA() });
}

// Mientras el modal de subida esta ABIERTO, cambiar el estado repinta SOLO
// el modal, no el dashboard entero.
//
// setState() reemplaza `#root.innerHTML` completo. Durante una subida,
// sondearTrabajo() actualiza el progreso cada 2 segundos, asi que el panel
// de detras se rehacia entero cada 2 segundos: medido con un
// MutationObserver, en cinco vueltas del sondeo se recrearon cinco veces el
// canvas del mapa de calor, el hueco del reproductor y el propio modal. Eso
// es el parpadeo que se veia detras de la ventana de subida (reportado con
// captura). Y no es solo estetico: el <canvas> del mapa se borra y se
// vuelve a pintar cada vez, que es trabajo tirado mientras la CPU ya esta
// ocupada procesando el video.
//
// ABRIR y CERRAR si necesitan el render completo -hay que esconder los
// reproductores, tapar el panel, volver a mostrarlo-, y por eso
// abrirSubida()/cerrarSubida() siguen llamando a setState(). Aqui se fuerza
// el render completo tambien si alguien toca `abierto` por su cuenta.
function actualizarSubida(cambios) {
  state.subida = { ...state.subida, ...cambios };

  const anfitrion = document.getElementById('subida-modal-host');
  if (!state.subida.abierto || !anfitrion || 'abierto' in cambios) {
    render();
    return;
  }

  anfitrion.innerHTML = renderSubidaModal();
  // El <canvas> de calibracion es un nodo NUEVO cada vez que se repinta el
  // modal, asi que hay que volver a dibujarlo y a engancharle el raton -lo
  // mismo que hace render() en su doble requestAnimationFrame, y por la
  // misma razon: medir el contenedor antes de que el navegador pinte da 0-.
  requestAnimationFrame(() => requestAnimationFrame(initLienzoZonas));
}

function terminosCompletos() {
  const t = state.subida.terminos;
  return t.gondola && t.privacidad && t.custodia;
}

function subirVideo() {
  const s = state.subida;
  if (!s.archivo || !terminosCompletos()) return;
  const datos = new FormData();
  datos.append('file', s.archivo);
  datos.append('acepta_terminos', 'true');
  datos.append('confirma_gondola', 'true');

  // XMLHttpRequest en vez de fetch() a proposito: fetch no sabe informar del
  // progreso de SUBIDA, y un video de decenas de MB sin barra parece colgado.
  const peticion = new XMLHttpRequest();
  peticion.open('POST', `${cleanBaseUrl(state.apiBaseUrl)}/uploads`);
  peticion.upload.onprogress = (e) => {
    if (e.lengthComputable) actualizarSubida({ pct: Math.round((e.loaded / e.total) * 100) });
  };
  peticion.onload = () => {
    let cuerpo = null;
    try { cuerpo = JSON.parse(peticion.responseText); } catch { /* respuesta no-JSON */ }
    if (peticion.status >= 400) {
      actualizarSubida({ paso: 'rechazado', error: (cuerpo && cuerpo.detail) || `El servidor respondió ${peticion.status}.` });
      return;
    }
    actualizarSubida({ paso: 'revisando', jobId: cuerpo.job_id, job: cuerpo, pct: 100 });
    sondearTrabajo();
  };
  peticion.onerror = () => actualizarSubida({
    paso: 'rechazado',
    error: `No se pudo contactar la API en ${state.apiBaseUrl}. ¿Está corriendo?`,
  });
  actualizarSubida({ paso: 'subiendo', pct: 0, error: null });
  peticion.send(datos);
}

async function sondearTrabajo() {
  const s = state.subida;
  if (!s.jobId) return;
  try {
    const job = await fetchFromApi(`${cleanBaseUrl(state.apiBaseUrl)}/uploads/${s.jobId}`);
    const siguiente = {
      esperando_zonas: 'zonas',
      procesando: 'procesando',
      listo: 'listo',
      rechazado: 'rechazado',
      error: 'rechazado',
    }[job.estado] || 'revisando';
    actualizarSubida({ job, paso: siguiente, error: siguiente === 'rechazado' ? job.mensaje : null });
    if (job.estado === 'listo') {
      loadVideos();
      return;
    }
    if (job.estado === 'rechazado' || job.estado === 'error') return;
    // 'esperando_zonas' tambien corta el sondeo: nada va a cambiar del lado
    // del servidor mientras la persona dibuja los estantes -el siguiente
    // paso (procesando) lo arranca enviarZonas() a mano, no este sondeo-.
    // Sin este corte, cada 2s se reconstruia el modal ENTERO (incluido el
    // <canvas> de calibracion, un nodo nuevo) aunque el estado del trabajo
    // no hubiera cambiado: el lienzo con los rectangulos ya dibujados
    // parpadeaba y se reiniciaba a medio dibujar -bug real, reportado en la
    // practica-.
    if (job.estado === 'esperando_zonas') return;
  } catch (err) {
    actualizarSubida({ error: `Se perdió el contacto con la API: ${err.message}` });
  }
  sondeoSubida = setTimeout(sondearTrabajo, 2000);
}

async function enviarZonas() {
  const s = state.subida;
  if (!s.rects.length) return;
  try {
    const cuerpo = {
      gondola_name: s.nombreGondola || 'Góndola subida',
      shelves: s.rects.map((r, i) => ({
        name: r.name || `Estante ${i + 1}`,
        product_category: r.categoria || null,
        x: Math.round(r.x), y: Math.round(r.y),
        width: Math.round(r.w), height: Math.round(r.h),
      })),
    };
    const res = await fetch(`${cleanBaseUrl(state.apiBaseUrl)}/uploads/${s.jobId}/zones`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      actualizarSubida({ error: e.detail || `El servidor respondió ${res.status}.` });
      return;
    }
    actualizarSubida({ paso: 'procesando', error: null, job: await res.json() });
    sondearTrabajo();
  } catch (err) {
    actualizarSubida({ error: `No se pudieron guardar las zonas: ${err.message}` });
  }
}

// --- El lienzo de calibración -------------------------------------------
// Se dibuja a mano en vez de con una libreria: son cuatro rectangulos sobre
// una imagen. Las coordenadas se guardan SIEMPRE en pixeles del frame
// original (no de la pantalla), que es lo que espera data/zones/*.json.

// Propone un rectangulo a partir de DONDE CAMINO LA GENTE de verdad, en
// vez de dejar a la persona frente a un lienzo en blanco adivinando.
//
// Se usan percentiles (10-90) y no el minimo/maximo: un solo punto suelto
// -alguien que cruzo el fondo del pasillo una vez- estiraria el rectangulo
// hasta cubrir media imagen. Con los percentiles la zona se ajusta al
// corredor por donde de verdad pasa la mayoria.
//
// Es una PROPUESTA, no una decision: queda como un rectangulo mas, que se
// puede borrar y redibujar a mano como cualquier otro.
const PERCENTIL_BORDE = 0.10;   // recorta el 10% de cada extremo: un paso suelto no define la zona
const BINS_HISTOGRAMA = 24;     // resolucion con la que se busca el pasillo vacio entre dos grupos
const MINIMO_POR_GRUPO = 0.25;  // cada grupo tiene que llevarse al menos este porcentaje del transito
const VALLE_MAXIMO = 0.45;      // el hueco entre los dos grupos tiene que ser un valle de verdad

function _cajaDePuntos(puntos) {
  const percentil = (valores, p) => {
    const orden = [...valores].sort((a, b) => a - b);
    return orden[Math.min(orden.length - 1, Math.floor(orden.length * p))];
  };
  const xs = puntos.map((p) => p.x);
  const ys = puntos.map((p) => p.y);
  const x1 = percentil(xs, PERCENTIL_BORDE), x2 = percentil(xs, 1 - PERCENTIL_BORDE);
  const y1 = percentil(ys, PERCENTIL_BORDE), y2 = percentil(ys, 1 - PERCENTIL_BORDE);
  // Un respiro alrededor: los pies marcan el centro del paso, no su borde.
  const margenX = Math.max(12, (x2 - x1) * 0.08);
  const margenY = Math.max(12, (y2 - y1) * 0.12);
  const r = {
    x: Math.max(0, x1 - margenX), y: Math.max(0, y1 - margenY),
    w: (x2 - x1) + margenX * 2, h: (y2 - y1) + margenY * 2,
  };
  return (r.w < 20 || r.h < 20) ? null : r;
}

// Donde CORTAR el transito en dos, o null si es un solo corredor.
//
// Se arma un histograma de las x de los pies y se busca el bin mas vacio
// que tenga un pico a cada lado. Un supermercado con dos estantes deja dos
// grupos de pisadas con un hueco en medio (la gente para FRENTE a cada
// estante, no entre ellos); un pasillo unico deja una sola mancha y
// entonces no hay valle que encontrar y se devuelve null.
//
// Las dos condiciones existen para no partir por partir: cada lado tiene
// que llevarse al menos MINIMO_POR_GRUPO del transito, y el valle tiene que
// estar de verdad por debajo del pico mas bajo (VALLE_MAXIMO). Sin ellas,
// cualquier irregularidad del muestreo parecia una division.
function _corteEntreGrupos(xs) {
  const min = Math.min(...xs), max = Math.max(...xs);
  if (max - min < 40) return null;
  const ancho = (max - min) / BINS_HISTOGRAMA;
  const bins = new Array(BINS_HISTOGRAMA).fill(0);
  xs.forEach((x) => {
    bins[Math.min(BINS_HISTOGRAMA - 1, Math.floor((x - min) / ancho))] += 1;
  });

  let mejor = null;
  for (let i = 2; i < BINS_HISTOGRAMA - 2; i += 1) {
    const izquierda = bins.slice(0, i).reduce((a, b) => a + b, 0);
    const derecha = xs.length - izquierda;
    if (izquierda < xs.length * MINIMO_POR_GRUPO) continue;
    if (derecha < xs.length * MINIMO_POR_GRUPO) continue;
    const picoIzq = Math.max(...bins.slice(0, i));
    const picoDer = Math.max(...bins.slice(i));
    if (bins[i] > Math.min(picoIzq, picoDer) * VALLE_MAXIMO) continue;
    if (!mejor || bins[i] < mejor.altura) mejor = { altura: bins[i], x: min + (i + 0.5) * ancho };
  }
  return mejor ? mejor.x : null;
}

// Una caja por corredor de transito. Devuelve [] si no hay con que proponer.
//
// Antes devolvia SIEMPRE una sola caja, del percentil 10 al 90 de TODOS los
// pies a la vez: en un video con dos estantes separados esa caja se tragaba
// los dos y el hueco del medio, y quedaba una zona que no distingue nada
// -pedido explicito: "la IA a veces coloca dos estantes en uno solo"-.
function zonasSugeridas(puntos, cupo = 4) {
  if (puntos.length < 8) return [];
  const corte = cupo >= 2 ? _corteEntreGrupos(puntos.map((p) => p.x)) : null;
  const grupos = corte === null
    ? [puntos]
    : [puntos.filter((p) => p.x < corte), puntos.filter((p) => p.x >= corte)];
  return grupos.map(_cajaDePuntos).filter(Boolean).slice(0, cupo);
}

function pintarLienzo() {
  const lienzo = document.getElementById('lienzo-zonas');
  if (!lienzo || !imagenCalibracion) return;
  const ctx = lienzo.getContext('2d');
  const escala = lienzo.width / imagenCalibracion.naturalWidth;
  ctx.drawImage(imagenCalibracion, 0, 0, lienzo.width, lienzo.height);

  // Puntos de apoyo (pies) de la gente que el prevuelo YA vio en este mismo
  // video (ver `puntos_pies` en backend/uploads.py), dibujados ANTES que
  // los rectangulos -para que la persona vea donde camina la gente de
  // verdad y dibuje la zona ENCIMA de esos puntos, en vez de adivinar
  // mirando solo donde se ven los productos-. Pedido explicito: antes de
  // esto, alguien calibrando a ojo podia marcar el estante (arriba del
  // cuadro, en camara cenital) en vez del piso (mas abajo, donde estan los
  // pies), y esa zona nunca hacia match con nadie -bug real, encontrado
  // en la practica-.
  const puntos = (state.subida.job && state.subida.job.detalles && state.subida.job.detalles.puntos_pies) || [];
  // Se dibujan como una NUBE, no como circulitos sueltos: cada punto es un
  // halo suave que se suma a los vecinos, asi que donde mas paso gente el
  // color se acumula y el corredor aparece solo. Con puntos duros habia que
  // ir contandolos con la vista para intuir donde estaba el paso.
  // 'lighter' hace que los halos se SUMEN en vez de taparse entre si.
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  // El radio va en pixeles del FRAME reescalados, no en pixeles del canvas:
  // con un radio fijo, al angostar la ventana el canvas se encoge pero los
  // halos no, y acababan tapando la imagen que se esta calibrando.
  const radioHalo = Math.max(6, 22 * escala);
  puntos.forEach((p) => {
    const cx = p.x * escala, cy = p.y * escala, radio = radioHalo;
    const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, radio);
    halo.addColorStop(0, 'rgba(80,200,140,0.34)');
    halo.addColorStop(1, 'rgba(80,200,140,0)');
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, radio, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.restore();
  // Encima, el punto exacto: la nube dice "por aqui pasa gente", el punto
  // dice "aqui piso alguien". Las dos cosas se necesitan para calibrar.
  puntos.forEach((p) => {
    ctx.beginPath();
    ctx.arc(p.x * escala, p.y * escala, Math.max(1.2, 2.6 * escala), 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(235,255,245,0.9)';
    ctx.fill();
  });

  state.subida.rects.forEach((r, i) => {
    ctx.strokeStyle = '#1F6C9F';
    ctx.lineWidth = 2;
    ctx.fillStyle = 'rgba(31,108,159,0.18)';
    ctx.fillRect(r.x * escala, r.y * escala, r.w * escala, r.h * escala);
    ctx.strokeRect(r.x * escala, r.y * escala, r.w * escala, r.h * escala);
    ctx.fillStyle = '#1F6C9F';
    ctx.font = 'bold 13px system-ui, sans-serif';
    ctx.fillText(`${i + 1}. ${r.name || 'sin nombre'}`, r.x * escala + 6, r.y * escala + 18);
  });
}

function initLienzoZonas() {
  const lienzo = document.getElementById('lienzo-zonas');
  if (!lienzo || state.subida.paso !== 'zonas') return;

  const dibujar = () => {
    const ancho = Math.min(lienzo.parentElement.clientWidth, imagenCalibracion.naturalWidth);
    lienzo.width = ancho;
    lienzo.height = Math.round(ancho * imagenCalibracion.naturalHeight / imagenCalibracion.naturalWidth);
    pintarLienzo();
  };

  // Pintar Y enganchar los manejadores de mouse viven en la MISMA funcion
  // -activar()-, llamada desde los DOS caminos posibles (imagen ya cargada
  // de una vuelta anterior, o recien terminada de cargar) en vez de que
  // enganchar los manejadores depende de una SEGUNDA llamada a
  // initLienzoZonas() despues de la primera (la de la imagen). Antes esa
  // segunda llamada la disparaba gratis el sondeo de subida, que volvia a
  // renderizar el modal entero cada 2s -incluso ya en el paso 'zonas', un
  // bug real de parpadeo, arreglado en sondearTrabajo()-. Al arreglar eso
  // se corto tambien la unica repeticion que hacia que esta funcion
  // llegara alguna vez a enganchar onmousedown/onmousemove/onmouseup:
  // la imagen se veia, pero arrastrar para dibujar un estante no hacia
  // nada -bug real, reportado en la practica, sintoma inverso al parpadeo
  // pero misma causa raiz-.
  const activar = () => {
    dibujar();

    // Arrastrar para crear un rectangulo. Durante el arrastre NO se llama a
    // setState(): repintar todo el dashboard en cada mousemove daria tirones.
    // Solo al soltar se guarda el rectangulo y se vuelve a renderizar -eso
    // SI dispara un render() nuevo, que crea un <canvas> nuevo, por eso
    // activar() se vuelve a llamar entera la proxima vez en vez de enganchar
    // los manejadores una sola vez para siempre.
    let inicio = null;
    const aFrame = (e) => {
      const caja = lienzo.getBoundingClientRect();
      const escala = imagenCalibracion.naturalWidth / caja.width;
      return { x: (e.clientX - caja.left) * escala, y: (e.clientY - caja.top) * escala };
    };
    lienzo.onmousedown = (e) => { if (state.subida.rects.length < 4) inicio = aFrame(e); };
    lienzo.onmousemove = (e) => {
      if (!inicio) return;
      const p = aFrame(e);
      pintarLienzo();
      const ctx = lienzo.getContext('2d');
      const escala = lienzo.width / imagenCalibracion.naturalWidth;
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = '#9F2F2D';
      ctx.strokeRect(inicio.x * escala, inicio.y * escala, (p.x - inicio.x) * escala, (p.y - inicio.y) * escala);
      ctx.setLineDash([]);
    };
    lienzo.onmouseup = (e) => {
      if (!inicio) return;
      const p = aFrame(e);
      const r = {
        x: Math.min(inicio.x, p.x), y: Math.min(inicio.y, p.y),
        w: Math.abs(p.x - inicio.x), h: Math.abs(p.y - inicio.y),
        name: `Estante ${state.subida.rects.length + 1}`, categoria: '',
      };
      inicio = null;
      // Un clic sin arrastrar no es un estante: se ignora en vez de crear un
      // rectangulo de 2 px que despues hay que borrar a mano.
      if (r.w < 20 || r.h < 20) { pintarLienzo(); return; }
      actualizarSubida({ rects: [...state.subida.rects, r] });
    };
    lienzo.onmouseleave = () => { inicio = null; pintarLienzo(); };
  };

  if (!imagenCalibracion) {
    const img = new Image();
    img.onload = () => { imagenCalibracion = img; activar(); };
    img.onerror = () => actualizarSubida({ error: 'No pude cargar el fotograma de calibración.' });
    img.src = `${cleanBaseUrl(state.apiBaseUrl)}/uploads/${state.subida.jobId}/frame`;
    return;
  }
  activar();
}

// --- Las seis pantallas --------------------------------------------------

function casillaTermino(clave, texto) {
  const marcada = state.subida.terminos[clave];
  return `
    <label class="flex items-start gap-2.5 p-3 rounded-lg border ${marcada ? 'border-[#1F6C9F]/40 bg-[#1F6C9F]/5' : 'border-[#EAEAEA] bg-white'} cursor-pointer transition-colors">
      <input type="checkbox" data-termino="${clave}" ${marcada ? 'checked' : ''} class="mt-0.5 accent-[#1F6C9F] w-4 h-4 shrink-0">
      <span class="text-xs leading-relaxed text-[#2F3437]">${texto}</span>
    </label>`;
}

function barraProgreso(pct, etiqueta) {
  return `
    <div>
      <div class="flex justify-between text-xs font-medium text-[#57534E] mb-1.5">
        <span>${esc(etiqueta)}</span><span class="tabular-nums">${pct}%</span>
      </div>
      <div class="h-2 bg-[#EAEAEA] rounded-full overflow-hidden">
        <div class="h-full bg-[#1F6C9F] transition-all duration-300" style="width:${pct}%"></div>
      </div>
    </div>`;
}

function cuerpoSubida() {
  const s = state.subida;
  const err = s.error ? `
    <div class="p-3 rounded-lg border border-[#9F2F2D]/30 bg-[#9F2F2D]/5 text-xs text-[#9F2F2D] leading-relaxed">${esc(s.error)}</div>` : '';

  if (s.paso === 'terminos') {
    return `
      ${err}
      <div class="space-y-2">
        ${casillaTermino('gondola', 'Declaro que el video muestra una <strong>góndola o lineal de tienda</strong> y a sus clientes, y que tengo autorización para analizarlo.')}
        ${casillaTermino('privacidad', 'Entiendo que el sistema <strong>no identifica personas</strong>: no reconoce rostros, no infiere edad, género ni emociones. Solo cuenta siluetas y mide permanencia e interacción.')}
        ${casillaTermino('custodia', 'Entiendo que el video se guarda <strong>en esta máquina</strong> (<code>data/videos/</code>), no se sube a internet, y que si la revisión previa lo rechaza <strong>se borra en el acto</strong>.')}
      </div>
      <div>
        <label class="block text-xs font-semibold text-[#57534E] mb-1.5">Archivo de video</label>
        <input type="file" id="archivo-subida" accept="video/mp4,video/quicktime,video/x-msvideo,video/*"
               class="block w-full text-xs text-[#2F3437] file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-[#F3F2EF] file:text-[#2F3437] hover:file:bg-[#EAEAEA] cursor-pointer">
        ${s.archivo ? `<p class="text-[11px] text-[#787774] mt-1.5">${esc(s.archivo.name)} · ${(s.archivo.size / 1048576).toFixed(1)} MB</p>` : ''}
      </div>
      <p class="text-[11px] text-[#787774] leading-relaxed">
        Antes de procesarlo, el sistema revisa que el archivo abra, que dure entre 5 s y 15 min, y que
        <strong>YOLO encuentre personas</strong> en una muestra de 24 fotogramas. No comprueba que sea una góndola:
        eso no lo puede saber un detector de objetos, y por eso lo declaras tú arriba.
      </p>`;
  }

  if (s.paso === 'subiendo') return `${err}${barraProgreso(s.pct, 'Subiendo el archivo...')}`;

  if (s.paso === 'revisando') {
    return `
      ${err}
      <div class="flex items-center gap-3 text-sm text-[#2F3437]">
        ${icon('refresh-cw', 'w-4 h-4 animate-spin text-[#1F6C9F]')}
        <span>Revisando el video con YOLO (24 fotogramas repartidos)...</span>
      </div>`;
  }

  if (s.paso === 'rechazado') {
    return `
      <div class="p-4 rounded-lg border border-[#9F2F2D]/30 bg-[#9F2F2D]/5">
        <p class="text-sm font-bold text-[#9F2F2D] mb-1">Video no apto</p>
        <p class="text-xs text-[#2F3437] leading-relaxed">${esc(s.error || (s.job && s.job.mensaje) || 'Sin motivo.')}</p>
      </div>
      <p class="text-[11px] text-[#787774]">El archivo ya se borró de <code>data/videos/</code>.</p>`;
  }

  if (s.paso === 'zonas') {
    const d = (s.job && s.job.detalles) || {};
    const aviso = d.frame_con_personas ? `
      <div class="p-2.5 rounded-lg border border-[#956400]/30 bg-[#956400]/5 text-[11px] text-[#956400] leading-relaxed">
        Este video no tiene ningún fotograma sin gente, así que el fondo muestra personas reales.
        No se guarda ni se comparte: solo se usa aquí para que puedas ubicar los estantes.
      </div>` : `
      <div class="p-2.5 rounded-lg border border-[#346538]/30 bg-[#346538]/5 text-[11px] text-[#346538] leading-relaxed">
        El fondo es un fotograma <strong>sin ninguna persona detectada</strong>, elegido a propósito para calibrar sin mirar a nadie.
      </div>`;
    const lista = s.rects.length ? s.rects.map((r, i) => `
      <div class="flex items-center gap-2">
        <span class="text-[11px] font-bold text-[#1F6C9F] w-4 shrink-0">${i + 1}</span>
        <input type="text" data-rect-nombre="${i}" value="${esc(r.name)}" placeholder="Nombre del estante"
               class="flex-1 min-w-0 px-2 py-1 text-xs border border-[#EAEAEA] rounded-md bg-white">
        <input type="text" data-rect-categoria="${i}" value="${esc(r.categoria || '')}" placeholder="categoría"
               class="w-24 px-2 py-1 text-xs border border-[#EAEAEA] rounded-md bg-white">
        <button type="button" data-action="borrar-rect" data-indice="${i}"
                class="p-1 rounded text-[#9F2F2D] hover:bg-[#9F2F2D]/10 shrink-0" aria-label="Borrar">${icon('x', 'w-3.5 h-3.5')}</button>
      </div>`).join('') : `<p class="text-[11px] text-[#787774]">Todavía no has dibujado ningún estante.</p>`;

    const puntosPies = d.puntos_pies || [];
    const guiaPies = puntosPies.length ? `
      <p class="text-[11px] text-[#57534E] flex items-center gap-1.5">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:rgba(52,101,56,0.75);border:1px solid #fff;box-shadow:0 0 0 1px #34653880"></span>
        Los puntos verdes son pies de gente real que la revisión previa ya detectó en este video: dibuja el estante
        rodeándolos, no adivinando dónde crees que camina la gente.
      </p>` : '';

    // Cuantos puntos de pies trajo el prevuelo: sin ellos no hay nada con
    // que proponer una zona.
    const hayPuntos = ((s.job && s.job.detalles && s.job.detalles.puntos_pies) || []).length >= 8;

    return `
      ${err}${aviso}
      <p class="text-xs text-[#2F3437] leading-relaxed">
        <strong>Arrastra sobre la imagen</strong> para marcar el <strong>área del piso</strong> por donde la gente camina
        frente a cada estante (no los productos: a las personas se las ubica por los pies). Puedes marcar hasta 4.
      </p>
      ${guiaPies}
      ${hayPuntos && s.rects.length < 4 ? `
      <button type="button" data-action="sugerir-zona"
              class="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-[#1F6C9F]/40 bg-[#1F6C9F]/5 text-xs font-bold text-[#1F6C9F] hover:bg-[#1F6C9F]/10">
        ${icon('sparkles', 'w-3.5 h-3.5')} Proponer zonas con el tránsito detectado
      </button>` : ''}
      <div class="rounded-lg border border-[#EAEAEA] overflow-hidden bg-[#F3F2EF]">
        <canvas id="lienzo-zonas" class="block w-full cursor-crosshair"></canvas>
      </div>
      <div>
        <label class="block text-xs font-semibold text-[#57534E] mb-1.5">Nombre de la góndola</label>
        <input type="text" id="nombre-gondola" value="${esc(s.nombreGondola)}"
               class="w-full px-2.5 py-1.5 text-xs border border-[#EAEAEA] rounded-md bg-white">
      </div>
      <div class="space-y-2">${lista}</div>
      <p class="text-[11px] text-[#787774]">
        Resolución del video: ${d.width || '?'}×${d.height || '?'} px · ${d.duration_s || '?'} s ·
        personas en ${d.frames_con_personas || 0}/${d.frames_muestreados || 0} fotogramas muestreados.
      </p>`;
  }

  if (s.paso === 'procesando') {
    const job = s.job || {};
    const etapa = job.etapa ? `Etapa: ${job.etapa}` : 'Preparando...';
    return `
      ${err}
      ${barraProgreso(job.progreso || 0, etapa)}
      <p class="text-xs text-[#2F3437] leading-relaxed">${esc(job.mensaje || '')}</p>
      <p class="text-[11px] text-[#787774] leading-relaxed">
        La cadena completa (detect → track → zones → interact → metrics) tarda varios minutos en CPU.
        Puedes cerrar esta ventana: el procesado sigue en el servidor y el video aparecerá en el selector al terminar.
      </p>`;
  }

  // listo
  return `
    <div class="p-4 rounded-lg border border-[#346538]/30 bg-[#346538]/5">
      <p class="text-sm font-bold text-[#346538] mb-1">Video procesado</p>
      <p class="text-xs text-[#2F3437] leading-relaxed">Ya está importado y disponible en el selector, con sus métricas, mapa de calor y video anonimizado.</p>
    </div>`;
}

function renderSubidaModal() {
  const s = state.subida;
  if (!s.abierto) return '';

  const acciones = {
    terminos: `<button type="button" data-action="subida-enviar" ${(!s.archivo || !terminosCompletos()) ? 'disabled' : ''}
      class="px-4 py-2 bg-[#111111] text-white rounded-lg text-xs font-semibold hover:bg-[#2F3437] disabled:opacity-40 disabled:cursor-not-allowed transition-colors">Subir y revisar</button>`,
    rechazado: `<button type="button" data-action="subida-reiniciar" class="px-4 py-2 bg-[#111111] text-white rounded-lg text-xs font-semibold hover:bg-[#2F3437] transition-colors">Probar con otro video</button>`,
    zonas: `<button type="button" data-action="subida-zonas" ${!s.rects.length ? 'disabled' : ''}
      class="px-4 py-2 bg-[#111111] text-white rounded-lg text-xs font-semibold hover:bg-[#2F3437] disabled:opacity-40 disabled:cursor-not-allowed transition-colors">Guardar zonas y procesar</button>`,
    listo: `<button type="button" data-action="subida-ver" class="px-4 py-2 bg-[#111111] text-white rounded-lg text-xs font-semibold hover:bg-[#2F3437] transition-colors">Ver el video</button>`,
  }[s.paso] || '';

  // Solo la PRIMERA vez que este modal se pinta desde que se abrio se le
  // pone la animacion de entrada -las vueltas siguientes, mientras
  // sondearTrabajo() actualiza el progreso cada 2s, la omiten (ver el
  // docstring de subidaYaAnimada), para no "parpadear" todo el rato.
  const animacionEntrada = subidaYaAnimada ? '' : 'modal-backdrop';
  const animacionCaja = subidaYaAnimada ? '' : 'modal-anim';
  subidaYaAnimada = true;

  return `
  <div role="dialog" aria-modal="true" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs ${animacionEntrada}">
    <div class="bg-white rounded-2xl max-w-2xl w-full border border-slate-200 shadow-xl overflow-hidden ${animacionCaja} max-h-[92vh] flex flex-col">
      <div class="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70 shrink-0">
        <div class="flex items-center gap-2.5">
          <div class="p-2 rounded-lg bg-slate-900 text-white">${icon('upload', 'w-4 h-4')}</div>
          <div>
            <h2 class="text-sm font-bold text-slate-900">Subir un video de góndola</h2>
            <p class="text-xs text-slate-500">Condiciones de uso · revisión previa · calibración · análisis</p>
          </div>
        </div>
        <button type="button" data-action="subida-cerrar" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-200 transition-colors" aria-label="Cerrar ventana">${icon('x', 'w-4 h-4')}</button>
      </div>
      <div class="p-6 space-y-4 overflow-y-auto">${cuerpoSubida()}</div>
      <div class="px-6 py-3.5 border-t border-slate-100 bg-slate-50/70 flex justify-end gap-2 shrink-0">
        <button type="button" data-action="subida-cerrar" class="px-4 py-2 rounded-lg text-xs font-semibold text-[#57534E] hover:bg-[#EAEAEA] transition-colors">Cerrar</button>
        ${acciones}
      </div>
    </div>
  </div>`;
}
