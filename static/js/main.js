// static/js/main.js
// Utilidades globales

function abrirGoogleMaps(direccion){
  const url = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(direccion);
  window.open(url, "_blank");
}

// Imprimir un contenedor por id (para historial / calendario por mes)
function imprimirDiv(id){
  const el = document.getElementById(id);
  if(!el){ window.print(); return; }
  const w = window.open("", "_blank", "width=900,height=700");
  w.document.write("<html><head><title>Imprimir</title>");
  w.document.write('<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">');
  w.document.write("</head><body>");
  w.document.write(el.outerHTML);
  w.document.write("</body></html>");
  w.document.close();
  w.focus();
  w.print();
  w.close();
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.body.classList.remove('modal-open');
    document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
    document.querySelectorAll('.modal.show').forEach(m => m.classList.remove('show'));
  }
});



// --- FIX: evitar pantalla gris por backdrops colgados (Bootstrap modals) ---
function limpiarBackdrops() {
  const algunoVisible = document.querySelector('.modal.show');
  const backdrops = document.querySelectorAll('.modal-backdrop');
  if (!algunoVisible && backdrops.length) {
    backdrops.forEach(b => b.remove());
    document.body.classList.remove('modal-open');
    document.body.style.removeProperty('padding-right');
  }
}

// Limpiar al cerrar modales (normal)
document.addEventListener('hidden.bs.modal', limpiarBackdrops);
// Si por algún motivo el backdrop queda sin disparar hidden, limpiamos también con interacciones
document.addEventListener('click', () => setTimeout(limpiarBackdrops, 0), true);
document.addEventListener('keyup', (e) => { if (e.key === 'Escape') setTimeout(limpiarBackdrops, 0); }, true);
// Fallback: por si quedó colgado por errores de JS, intentamos un par de veces al cargar
window.addEventListener('load', () => { setTimeout(limpiarBackdrops, 300); setTimeout(limpiarBackdrops, 1200); });
// --- FIN FIX backdrops ---



/* ================== FIX MODALES (EP91) ==================
   - Confirm modal visible y sin backdrop colgado
   - Editar/Eliminar alumno (Matrícula) robusto por delegación
==========================================================*/
(function () {
  function cleanupBackdrops() {
    // Quitar backdrops colgados y liberar scroll
    document.querySelectorAll(".modal-backdrop").forEach(b => b.remove());
    document.body.classList.remove("modal-open");
    document.body.style.removeProperty("padding-right");
    document.body.style.removeProperty("overflow");
  }

  function ensureModalInBody(modalEl) {
    if (!modalEl) return;
    if (modalEl.parentElement !== document.body) {
      document.body.appendChild(modalEl);
    }
  }

  // Confirm modal global (sobrescribe si existe para hacerlo más robusto)
  window.showConfirmModal = function (titulo, mensaje, onOk) {
    const modalEl = document.getElementById("confirmModal");
    if (!modalEl || typeof bootstrap === "undefined") {
      // Fallback nativo
      if (confirm((titulo ? (titulo + "\n\n") : "") + (mensaje || "¿Confirmás la acción?"))) {
        try { onOk && onOk(); } catch (e) {}
      }
      return;
    }

    ensureModalInBody(modalEl);

    const titleEl = document.getElementById("confirmModalTitle");
    const bodyEl  = document.getElementById("confirmModalBody");
    const okBtn   = document.getElementById("confirmModalOk");

    if (titleEl) titleEl.textContent = titulo || "Confirmar";
    if (bodyEl)  bodyEl.textContent  = mensaje || "¿Confirmás la acción?";

    // Evitar listeners viejos
    const newOkBtn = okBtn ? okBtn.cloneNode(true) : null;
    if (okBtn && okBtn.parentNode && newOkBtn) okBtn.parentNode.replaceChild(newOkBtn, okBtn);

    const instance = bootstrap.Modal.getOrCreateInstance(modalEl, { backdrop: true, keyboard: true });

    // Limpieza cuando se oculta
    modalEl.addEventListener("hidden.bs.modal", cleanupBackdrops, { once: true });

    if (newOkBtn) {
      newOkBtn.addEventListener("click", () => {
        try { instance.hide(); } catch (e) {}
        cleanupBackdrops();
        try { onOk && onOk(); } catch (e) {}
      }, { once: true });
    }

    // Mostrar y forzar visibilidad (por si algún contenedor lo tapaba)
    try { instance.show(); } catch (e) {}
    setTimeout(() => {
      modalEl.style.display = "block";
      modalEl.classList.add("show");
      modalEl.removeAttribute("aria-hidden");
    }, 0);
  };

  // Delegación para botones de alumnos
  function bindAlumnosHandlers() {
    document.addEventListener("click", function (ev) {
      const delBtn = ev.target.closest && ev.target.closest(".btn-eliminar-alumno");
      if (delBtn) {
        ev.preventDefault();
        const form = delBtn.closest("form");
        const hard = delBtn.dataset ? (delBtn.dataset.hard === "1") : false;
        const titulo = hard ? "Eliminar definitivo" : "Pasar a histórico";
        const msg = hard
          ? "⚠️ Esto elimina DEFINITIVAMENTE el alumno (no se puede deshacer). ¿Continuar?"
          : "Esto enviará el alumno al histórico. Podrás recuperarlo luego. ¿Continuar?";
        window.showConfirmModal(titulo, msg, () => { if (form) form.submit(); });
        return;
      }

      const editBtn = ev.target.closest && ev.target.closest(".btn-edit-alumno");
      if (editBtn) {
        ev.preventDefault();

        const modalEl = document.getElementById("modalEditarAlumno");
        const formEl  = document.getElementById("formEditarAlumno");
        if (!modalEl || !formEl || typeof bootstrap === "undefined") return;

        ensureModalInBody(modalEl);

        const d = editBtn.dataset || {};
        const id = d.id;
        if (id) formEl.action = `/alumnos/${id}/editar`;

        // Mapeo básico de dataset -> inputs (si existen)
        const map = {
          "edit-apellido": "apellido",
          "edit-nombre": "nombre",
          "edit-curso": "curso",
          "edit-dni": "dni",
          "edit-cuil": "cuil",
          "edit-fecha_nac": "fechaNac",
          "edit-lugar_nac": "lugarNac",
          "edit-domicilio": "domicilio",
          "edit-localidad": "localidad",
          "edit-telefono": "telefono",
          "edit-sexo": "sexo",
          "edit-escuela_procedencia": "escuelaProcedencia",
          "edit-sit_final": "situacionFinal",
          "edit-observaciones": "observaciones",
          "edit-madre_padre_tutor": "madrePadreTutor",
          "edit-ocupacion": "ocupacion",
          "edit-nacionalidad-input": "nacionalidad"
        };

        Object.keys(map).forEach((elId) => {
          const key = map[elId];
          const el = document.getElementById(elId);
          if (!el) return;
          const val = (d[key] !== undefined) ? d[key] : "";
          // selects vs inputs
          try { el.value = val; } catch (e) {}
        });

        // Mostrar modal
        const instance = bootstrap.Modal.getOrCreateInstance(modalEl, { backdrop: true, keyboard: true });
        modalEl.addEventListener("hidden.bs.modal", cleanupBackdrops, { once: true });
        try { instance.show(); } catch (e) {}

        return;
      }
    });

    // Escape para desbloquear siempre
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") cleanupBackdrops();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Si hay modales confirm/editar en el DOM, los movemos al body para evitar que queden ocultos
    ensureModalInBody(document.getElementById("confirmModal"));
    ensureModalInBody(document.getElementById("modalEditarAlumno"));
    bindAlumnosHandlers();

    // Si por alguna razón quedó un backdrop colgado al cargar
    cleanupBackdrops();
  });
})();
