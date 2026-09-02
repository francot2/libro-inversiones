# Libro de inversiones

Dashboard de seguimiento de cartera en un solo archivo HTML. Sin backend, sin
dependencias y sin instalación.

## Cómo funciona

Al abrirlo pide seleccionar los reportes CSV de transacciones. Los archivos se
leen con la API `FileReader` del navegador: **no se suben a ningún servidor**.
No hay peticiones de red en toda la aplicación.

Los datos viven en `sessionStorage`, así que al cerrar la pestaña se borran.
Cada sesión arranca en blanco.

## Qué calcula

- Posiciones abiertas con costo promedio ponderado
- Ajuste automático por splits (filas `POSITION_ADJUSTMENTS`)
- Resultado realizado por operación cerrada y acumulado en el tiempo
- Dividendos netos de retención, comisiones y costo de suscripción
- Balance patrimonial y rentabilidad sobre el capital aportado

Los precios de mercado se ingresan a mano en la tabla de posiciones.

## Uso

Abrir `index.html`, o visitar la versión publicada en GitHub Pages.

Este proyecto no es asesoría de inversión.
