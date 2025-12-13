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

