# Libro de inversiones

Seguimiento de una cartera de Hapi a partir de los reportes CSV de transacciones.
El repositorio tiene dos versiones:

| Archivo | Qué es | Dónde se usa |
|---|---|---|
| `app.py` | Dashboard completo en Streamlit: precios de mercado automáticos, comparación contra el S&P 500, diagnóstico y reporte descargable. | Streamlit Community Cloud o en tu computadora |
| `index.html` | Versión liviana en un solo archivo HTML, sin backend. Los precios se ingresan a mano. | GitHub Pages o abriendo el archivo |

Ninguna de las dos guarda tus movimientos: los CSV se procesan solo durante la sesión.

## Dashboard en Streamlit (`app.py`)

### Qué muestra

- **Portafolio:** posiciones a precio de mercado (Yahoo Finance), costo promedio con
  comisiones incluidas, peso en la cuenta, variación del día, señal técnica y
  distribución por activo, clase o sector (con el efectivo).
- **Rendimiento vs Mercado:** valor diario real de la cuenta frente a una simulación
  que invierte cada aporte, el mismo día, en VOO. Rentabilidad TWR y TIR, caída
  máxima, volatilidad, resumen por año y beta de la cartera.
- **Diagnóstico:** cuánto operas, resultado según el plazo de tenencia, de dónde
  viene la ganancia, costos y balance de Hapi Prime, efectivo ocioso y una
  retrospectiva de lo que vendiste.
- **Flujos y Costos:** ventas, dividendos y comisiones por mes, y aportes por año.
- **Radar:** cartera + watchlist con tendencia (medias de 50 y 200 días), RSI,
  distancia al máximo de 52 semanas, objetivo de analistas y próxima fecha de
  resultados.
- **Alertas:** posiciones por encima de tu límite, pérdidas profundas, efectivo sin
  rendimiento, Prime activo, próximos resultados y CSV desactualizado.
- **📄 Descargar reporte:** resumen de 1–2 páginas en HTML, con precios tomados al
  momento de la descarga. Con Ctrl+P se guarda como PDF.

### Uso

1. En Hapi, descarga los reportes CSV de transacciones. Pueden ser varios periodos;
   las filas repetidas entre archivos se descartan solas.
2. Abre la app y súbelos desde la barra lateral.
3. En **⚙️ Mis Reglas y Preferencias** ajusta tu watchlist, el peso máximo por
   posición y el rendimiento de referencia del efectivo. Quedan guardados en la URL:
   guárdala en favoritos para conservarlos.

### Privacidad

- Los CSV viven solo en la memoria de tu sesión: no se escriben en disco ni se suben
  al repositorio.
- El servidor solo guarda un caché de precios públicos (`data/market_cache.json`).
- `.gitignore` bloquea `data/`, `*.csv` y planillas para que ningún movimiento se
  suba por error.

### Ejecutarlo en tu computadora

```bash
pip install -r requirements.txt
streamlit run app.py
```

Si hay una carpeta `data/` junto a `app.py` con CSV de Hapi, la app los carga sola.

### Publicarlo en Streamlit Community Cloud

Crea la app en [share.streamlit.io](https://share.streamlit.io) apuntando a este
repositorio, rama `main` y archivo `app.py`. Cada cambio que entra a `main` se
publica automáticamente.

## Versión HTML (`index.html`)

Al abrirla pide seleccionar los reportes CSV de transacciones. Los archivos se
leen con la API `FileReader` del navegador: **no se suben a ningún servidor**.
No hay peticiones de red en toda la aplicación.

Los datos viven en `sessionStorage`, así que al cerrar la pestaña se borran.
Cada sesión arranca en blanco.

Calcula:

- Posiciones abiertas con costo promedio ponderado
- Ajuste automático por splits (filas `POSITION_ADJUSTMENTS`)
- Resultado realizado por operación cerrada y acumulado en el tiempo
- Dividendos netos de retención, comisiones y costo de suscripción
- Balance patrimonial y rentabilidad sobre el capital aportado

Los precios de mercado se ingresan a mano en la tabla de posiciones. Para usarla,
abre `index.html` o visita la versión publicada en GitHub Pages.

---

Este proyecto no es asesoría de inversión.
