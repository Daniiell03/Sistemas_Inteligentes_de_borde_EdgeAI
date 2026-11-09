#!/usr/bin/env python3
"""Agrega la pestana de agenda a la interfaz del portal.

La interfaz es del Rol A. Este parche solo anade la vista que faltaba para
la operacion AGENDA del catalogo, que el banco mide pero el portal no
cubria, y ajusta tres textos al vocabulario del aula.

Es un parche y no un archivo nuevo a proposito: la interfaz sigue siendo de
el, y las trescientas lineas que no se tocan no tienen por que viajar en un
diff. Se puede correr mas de una vez sin duplicar nada.

Uso:  python3 parche-agenda.py app/jarvis/templates/index.html
"""

import sys
from pathlib import Path

NAV = '      <button role="tab" data-tab="asistente" aria-selected="false">Asistente</button>\n'
NAV_NUEVO = ('      <button role="tab" data-tab="agenda" aria-selected="false">Agenda</button>\n'
             + NAV)

SECCION_ANCLA = "    <!-- ASISTENTE -->\n"
SECCION_NUEVA = """    <!-- AGENDA -->
    <section id="tab-agenda" class="hidden">
      <div class="card hidden" id="nuevoEvento">
        <h2>Agregar al plan de la sesión</h2>
        <label for="agDesc">Actividad</label>
        <input id="agDesc" type="text" maxlength="200" placeholder="Ej. Repaso de Yocto">
        <label for="agFecha">Hora (opcional)</label>
        <input id="agFecha" type="datetime-local">
        <div class="row" style="margin-top:12px"><button class="btn" id="btnCrearEvento">Agregar</button></div>
        <div class="msg-err" id="agErr"></div>
      </div>
      <div class="card">
        <h2>Plan de la sesión</h2>
        <div id="listaAgenda"></div>
      </div>
    </section>

"""

TABS_VIEJO = '  ["aula", "entregas", "asistente"].forEach(x => $("#tab-" + x).classList.toggle("hidden", x !== t));\n  if (t === "entregas") cargarTareas();\n'
TABS_NUEVO = '  ["aula", "entregas", "agenda", "asistente"].forEach(x => $("#tab-" + x).classList.toggle("hidden", x !== t));\n  if (t === "entregas") cargarTareas();\n  if (t === "agenda") cargarAgenda();\n'

PROF_VIEJO = '  $("#nuevaTarea").classList.toggle("hidden", !esProf());\n'
PROF_NUEVO = PROF_VIEJO + '  $("#nuevoEvento").classList.toggle("hidden", !esProf());\n'

SUG_VIEJO = ('  const ej = esProf() ? ["¿Qué temperatura hay?", "Enciende la sala", "Escena cine", '
             '"Apaga todo", "¿Cuántas entregas hay?"]\n'
             '                      : ["¿Qué temperatura hay?", "¿Qué tengo pendiente?"];\n')
SUG_NUEVO = ('  const ej = esProf() ? ["¿Qué temperatura hay en el aula?", "Encendé la luz del frente", '
             '"Poné el aula en modo proyección", "Apagá todas las luces", "¿Quién ya entregó?", '
             '"Resumime la sesión"]\n'
             '                      : ["¿Qué temperatura hay en el aula?", "¿Qué tengo pendiente?", '
             '"¿Qué hay programado hoy?"];\n')

NOTA_VIEJA = ('        <p class="muted" style="margin-bottom:0">Modo simulado: reglas simples. '
              'Se conecta al agente real (Ollama) en la Raspberry Pi.</p>\n')
NOTA_NUEVA = ('        <p class="muted" style="margin-bottom:0">El agente clasifica la orden con '
              '<code>tev1:0.8b</code> y redacta con <code>llama3.2:1b</code>, ambos locales. '
              'Cada orden tarda unos 15 segundos: es el costo de procesar el prompt a 18,5 '
              'tokens por segundo, no una demora de red. Si no reconoce la orden con '
              'suficiente confianza, se abstiene en lugar de adivinar.</p>\n')

JS = """
// --- Agenda ------------------------------------------------------------
async function cargarAgenda() {
  let items;
  try { items = await api("/api/agenda"); } catch (e) { toast(e.message); return; }
  const cont = $("#listaAgenda");
  if (!items.length) {
    cont.replaceChildren(h("p", { class: "muted" }, "No hay actividades registradas."));
    return;
  }
  cont.replaceChildren(...items.map(ev => h("div",
    { class: "row", style: "justify-content:space-between;align-items:center;gap:12px;padding:8px 0" },
    h("div", {},
      h("strong", {}, ev.descripcion),
      h("div", { class: "muted" }, fechaIso(ev.momento))),
    esProf()
      ? h("button", {
          class: "btn bad sm",
          onclick: async () => {
            try { await api("/api/agenda/" + ev.id, { method: "DELETE" }); cargarAgenda(); }
            catch (e) { toast(e.message); }
          }
        }, "Borrar")
      : null)));
}

$("#btnCrearEvento").onclick = async () => {
  const desc = $("#agDesc").value.trim();
  const err = $("#agErr");
  err.textContent = "";
  if (desc.length < 2) { err.textContent = "Escriba la actividad."; return; }
  try {
    await post("/api/agenda", { descripcion: desc, momento: $("#agFecha").value || "" });
    $("#agDesc").value = ""; $("#agFecha").value = "";
    cargarAgenda();
  } catch (e) { err.textContent = e.message; }
};
"""


def aplicar(ruta: Path) -> int:
    s = ruta.read_text(encoding="utf-8")
    cambios, omitidos = [], []

    def sust(viejo: str, nuevo: str, nombre: str, marca: str) -> None:
        nonlocal s
        if marca in s:
            omitidos.append(nombre)
            return
        if viejo not in s:
            print(f"  NO ENCONTRADO: {nombre}", file=sys.stderr)
            print(f"    se buscaba: {viejo.strip()[:80]}", file=sys.stderr)
            raise SystemExit(1)
        s = s.replace(viejo, nuevo, 1)
        cambios.append(nombre)

    sust(NAV, NAV_NUEVO, "pestaña en la barra", 'data-tab="agenda"')
    sust(SECCION_ANCLA, SECCION_NUEVA + SECCION_ANCLA,
         "sección de agenda", 'id="tab-agenda"')
    sust(TABS_VIEJO, TABS_NUEVO, "cambio de pestaña", 'cargarAgenda()')
    sust(PROF_VIEJO, PROF_NUEVO, "visibilidad para el profesor", '#nuevoEvento')
    sust(SUG_VIEJO, SUG_NUEVO, "sugerencias del asistente", "modo proyección")
    sust(NOTA_VIEJA, NOTA_NUEVA, "nota del asistente", "18,5")

    if "async function cargarAgenda" not in s:
        i = s.rfind("</script>")
        if i < 0:
            print("  NO ENCONTRADO: cierre de script", file=sys.stderr)
            raise SystemExit(1)
        s = s[:i] + JS + s[i:]
        cambios.append("funciones de agenda")
    else:
        omitidos.append("funciones de agenda")

    ruta.write_text(s, encoding="utf-8")
    for c in cambios:
        print(f"  aplicado: {c}")
    for o in omitidos:
        print(f"  ya estaba: {o}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    destino = Path(sys.argv[1])
    if not destino.is_file():
        print(f"no existe: {destino}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(aplicar(destino))
