import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from datetime import datetime, timedelta
import os
import re
import glob
import sys
import time
import json
import html
from concurrent.futures import ThreadPoolExecutor
import io

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Dashboard de Inversiones", layout="wide", page_icon="📈", initial_sidebar_state="expanded")

# Estandarización visual global Premium (Cyber-Glow y Tipografía Inter)
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Inter', sans-serif;
    }
    .block-container { padding-top: 3rem !important; padding-bottom: 1rem !important; }
    div[data-testid="stMetric"], div[data-testid="metric-container"] {
        background-color: #1e1e1e;
        border: 1px solid #333;
        padding: 12px 14px;
        border-radius: 12px;
        color: white;
        transition: transform 0.2s ease-in-out, border-color 0.2s ease-in-out;
    }
    div[data-testid="stMetricValue"] { font-size: 1.55rem !important; }
    div[data-testid="stMetricLabel"] p { font-size: 0.82rem !important; }
    div[data-testid="stMetric"]:hover, div[data-testid="metric-container"]:hover {
        transform: translateY(-2px);
        border-color: #00CC96;
    }
    h2, h3 { color: #00CC96 !important; }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #1e1e1e;
        border-radius: 8px 8px 0px 0px;
        padding: 8px 16px;
        color: white;
        border: 1px solid #333;
    }
    .stTabs [aria-selected="true"] {
        background-color: #0e1117;
        border-color: #00CC96 #333 #0e1117 #333 !important;
        color: #00CC96 !important;
        font-weight: bold;
    }
    .alerta {
        background-color: #1e1e1e;
        border: 1px solid #333;
        border-left: 4px solid #888;
        border-radius: 10px;
        padding: 9px 14px;
        margin-bottom: 8px;
        font-size: 0.92em;
        color: #e6e6e6;
    }
    .alerta.roja { border-left-color: #FF4B4B; }
    .alerta.ambar { border-left-color: #FFA500; }
    .alerta.verde { border-left-color: #00CC96; }
    .alerta.azul { border-left-color: #0080FF; }
    </style>
""", unsafe_allow_html=True)

# --- COLORES GLOBALES ---
COLOR_PROFIT = '#00CC96'
COLOR_LOSS = '#FF4B4B'
COLOR_CAPITAL = '#0080FF'
COLOR_SIMULATION = '#FFA500'

SECTORES_ES = {
    'Technology': 'Tecnología', 'Energy': 'Energía', 'Industrials': 'Industria y defensa',
    'Communication Services': 'Comunicaciones y medios', 'Consumer Cyclical': 'Consumo cíclico',
    'Consumer Defensive': 'Consumo básico', 'Financial Services': 'Financiero', 'Healthcare': 'Salud',
    'Utilities': 'Servicios públicos', 'Real Estate': 'Inmobiliario', 'Basic Materials': 'Materiales',
}

BENCHMARK = 'VOO'
RETENCION_DIVIDENDOS = 0.30     # retención de EE.UU. a no residentes (la que ves en tus CSV)
MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

# --- FUNCIÓN MATEMÁTICA PARA EL RSI ---
def compute_rsi(series, window=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=window - 1, adjust=False).mean()
    ema_down = down.ewm(com=window - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def es_ticker_valido(sym):
    # Descarta códigos internos del broker (p. ej. 'P064398', '2015086') que Yahoo no reconoce
    return bool(re.fullmatch(r'[A-Z]{1,6}([.\-][A-Z]{1,4})?', str(sym)))

def get_asset_class(sym, q_type):
    sym_upper = sym.upper()
    if q_type == 'CRYPTOCURRENCY' or '-USD' in sym_upper:
        return 'Criptomonedas'
    elif q_type == 'ETF':
        if sym_upper in ['SLV', 'GLD', 'IAU', 'USO', 'BAR']:
            return 'Commodities'
        return 'ETFs'
    else:
        return 'Acciones'

def fecha_corta(d):
    return f"{d.day:02d}-{MESES[d.month - 1]}"

def fmt_usd(v, signo=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return '—'
    s = f"${abs(v):,.2f}"
    return f"−{s}" if v < 0 else (f"+{s}" if signo else s)

def fmt_pct(v, signo=True, dec=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return '—'
    return f"{v:+.{dec}f}%" if signo else f"{v:.{dec}f}%"

def md_seguro(texto):
    # En Markdown de Streamlit dos signos $ abren una fórmula matemática; se escapan los montos
    return texto.replace('$', '\\$')

# --- 1. RESOLUCIÓN DE RUTAS PARA PYINSTALLER ---
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_cache_path():
    base_path = get_base_path()
    cache_dir = os.path.join(base_path, 'data')
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, 'market_cache.json')

# Preferencias del usuario: viven en la URL (?wl=...&max=...&tasa=...). Así cada persona conserva
# las suyas guardando el enlace, y en la versión web nadie pisa las preferencias de otro.
DEFAULT_CONFIG = {
    "watchlist": ["AAPL", "TSLA", "NVDA", "BTC-USD", "VOO", "MSFT", "GOOGL"],
    "max_peso_posicion": 15,
    "tasa_efectivo": 4.2,
}

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    qp = st.query_params
    try:
        if qp.get('wl'):
            cfg['watchlist'] = [t.strip().upper() for t in qp['wl'].split(',') if t.strip()]
        if qp.get('max'):
            cfg['max_peso_posicion'] = min(max(int(float(qp['max'])), 5), 50)
        if qp.get('tasa'):
            cfg['tasa_efectivo'] = min(max(float(qp['tasa']), 0.0), 15.0)
    except ValueError:
        pass
    return cfg

def save_config(cfg):
    # Solo se escriben en la URL los valores que difieren de los predeterminados
    params = {}
    if cfg['watchlist'] != DEFAULT_CONFIG['watchlist']:
        params['wl'] = ','.join(cfg['watchlist'])
    if cfg['max_peso_posicion'] != DEFAULT_CONFIG['max_peso_posicion']:
        params['max'] = str(cfg['max_peso_posicion'])
    if cfg['tasa_efectivo'] != DEFAULT_CONFIG['tasa_efectivo']:
        params['tasa'] = f"{cfg['tasa_efectivo']:g}"
    if params != st.query_params.to_dict():
        st.query_params.from_dict(params)

# --- 2. CARGA Y PROCESAMIENTO MULTI-ARCHIVO ---
def parse_money(series):
    # Hapi exporta "$1,420.01", "-$24.99" o "$ -0.01" según el formato del archivo
    limpio = series.astype(str).str.replace(r'[\$,\s]', '', regex=True)
    return pd.to_numeric(limpio, errors='coerce').fillna(0.0)

def parse_dates(series):
    # Conviven dos formatos: ISO (2026-10-16) y día/mes/año (24/02/2024)
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s.where(s.str.match(r'^\d{4}-\d{2}-\d{2}', na=False)).str[:10], format='%Y-%m-%d', errors='coerce')
    dmy = pd.to_datetime(s.where(s.str.match(r'^\d{1,2}/\d{1,2}/\d{4}$', na=False)), format='%d/%m/%Y', errors='coerce')
    return iso.combine_first(dmy)

def _texto(serie, decimales=None):
    if decimales is not None:
        return serie.map(lambda v: '' if pd.isna(v) else f"{float(v):.{decimales}f}")
    return serie.map(lambda v: '' if pd.isna(v) else str(v))

def clasificar(df):
    desc = df['Description'].str.upper()
    sym = df['Symbol']
    tipo = df['Type'].str.upper()
    es_mm = tipo == 'MONEY_MOVEMENTS'
    cat = pd.Series('OTRO', index=df.index)
    cat[tipo == 'TRADES'] = 'TRADE'
    cat[tipo == 'POSITION_ADJUSTMENTS'] = 'AJUSTE'
    journal = es_mm & desc.str.contains('JOURNAL', na=False)
    cat[journal & (df['Net Amount'] >= 0)] = 'DEPOSITO'
    cat[journal & (df['Net Amount'] < 0)] = 'RETIRO'
    cat[journal & desc.str.contains('BAKKT', na=False)] = 'CRIPTO'      # traspasos con tu cuenta cripto
    cat[es_mm & desc.str.contains('PRIME', na=False)] = 'PRIME'
    cat[es_mm & (sym == 'ISPAZ')] = 'SWEEP'                              # movimientos internos al money market
    cat[es_mm & (sym != 'ISPAZ') & desc.str.contains('INTEREST', na=False)] = 'INTERES'
    cat[es_mm & desc.str.contains('LENDING', na=False)] = 'LENDING'
    cat[es_mm & (cat == 'OTRO') & (sym != '') & ~desc.str.contains('CASH IN LIEU', na=False)] = 'DIVIDENDO'
    return cat

def load_data(uploaded_files=None):
    df_list = []

    # 1. Carga prioritaria si el usuario sube archivos
    if uploaded_files:
        for i, file in enumerate(uploaded_files):
            try:
                src = io.BytesIO(file) if isinstance(file, (bytes, bytearray)) else file
                temp_df = pd.read_csv(src, dtype=str)
                temp_df['_origen'] = f"subido_{i}"
                df_list.append(temp_df)
            except Exception as e:
                st.error(f"⚠️ No se pudo leer el archivo subido: {e}")
    # 2. Fallback a la carpeta local 'data'
    else:
        folder_path = os.path.join(get_base_path(), "data")
        if os.path.exists(folder_path):
            for file in sorted(glob.glob(os.path.join(folder_path, "*.csv"))):
                try:
                    temp_df = pd.read_csv(file, dtype=str)
                    temp_df['_origen'] = os.path.basename(file)
                    df_list.append(temp_df)
                except Exception as e:
                    st.warning(f"⚠️ No se pudo leer el archivo local {file}: {e}")

    if not df_list:
        return pd.DataFrame(), 0

    df = pd.concat(df_list, ignore_index=True)
    for col in ['Price', 'Fees', 'Commissions', 'Net Amount']:
        df[col] = parse_money(df[col]) if col in df.columns else 0.0
    df['Qty'] = pd.to_numeric(df['Qty'], errors='coerce') if 'Qty' in df.columns else np.nan
    for col in ['Type', 'Account', 'Symbol', 'Description', 'Trade Action', 'Transfer Direction', 'Tag #']:
        df[col] = df[col].fillna('').astype(str).str.strip() if col in df.columns else ''
    df['Symbol'] = df['Symbol'].str.upper()

    # El Timestamp viene siempre en ISO: es la fecha más confiable
    ts = pd.to_datetime(df['Timestamp'], format='ISO8601', errors='coerce') if 'Timestamp' in df.columns else pd.Series(pd.NaT, index=df.index)
    df['Trade Date'] = parse_dates(df['Trade Date']) if 'Trade Date' in df.columns else pd.NaT
    df['Settle Date'] = parse_dates(df['Settle Date']) if 'Settle Date' in df.columns else pd.NaT
    df['Date_Master'] = ts.dt.normalize().combine_first(df['Trade Date']).combine_first(df['Settle Date'])
    df['Timestamp'] = ts.combine_first(df['Date_Master'])
    df = df.dropna(subset=['Date_Master'])

    # Duplicados entre archivos que se superponen (aunque tengan distinto formato).
    # Se conservan las filas idénticas dentro de un mismo archivo: son movimientos legítimos.
    clave = (df[['Type', 'Account', 'Symbol', 'Description', 'Trade Action']].agg('|'.join, axis=1)
             + '|' + df['Date_Master'].dt.strftime('%Y-%m-%d')
             + '|' + _texto(df['Qty'], 6) + '|' + _texto(df['Price'], 4) + '|' + _texto(df['Net Amount'], 2))
    df['_n'] = df.groupby([df['_origen'], clave]).cumcount()
    df['_clave'] = clave
    antes = len(df)
    df = df.drop_duplicates(subset=['_clave', '_n']).drop(columns=['_clave', '_n'])
    duplicados = antes - len(df)

    df['Categoria'] = clasificar(df)
    return df.sort_values('Timestamp', kind='stable').reset_index(drop=True), duplicados

# --- 3. GESTIÓN DE INTERFAZ DE CARGA (CSV Uploader) ---
if "csv_cache" not in st.session_state:
    st.session_state.csv_cache = {}      # nombre de archivo -> bytes, solo en memoria

def registrar_archivos(files):
    for f in files or []:
        st.session_state.csv_cache[f.name] = f.getvalue()

config = load_config()

with st.sidebar:
    st.markdown("### 📥 Carga de Transacciones")
    registrar_archivos(st.file_uploader(
        "Sube archivos CSV de Hapi:",
        type=["csv"], accept_multiple_files=True, key="sidebar_uploader",
        help="Arrastra aquí tus reportes CSV descargados de Hapi."))

    if st.session_state.csv_cache:
        st.caption(f"{len(st.session_state.csv_cache)} archivo(s) cargados en esta sesión")
        if st.button("Vaciar archivos cargados"):
            st.session_state.csv_cache = {}
            st.rerun()

    st.markdown("---")
    st.markdown("### 🔄 Sincronización")
    force_sync = st.button("Forzar Recarga de Precios")

    if 'force_refresh' not in st.session_state:
        st.session_state.force_refresh = False

    if force_sync:
        st.cache_data.clear()
        st.session_state.force_refresh = True
        st.rerun()

    st.markdown("---")
    st.session_state.setdefault('pref_wl', ", ".join(config['watchlist']))
    st.session_state.setdefault('pref_max', int(config['max_peso_posicion']))
    st.session_state.setdefault('pref_tasa', float(config['tasa_efectivo']))
    with st.expander("⚙️ Mis Reglas y Preferencias"):
        st.text_input("Watchlist (tickers separados por comas):", key='pref_wl')
        st.slider("Peso máximo que acepto por posición (%)", 5, 50, key='pref_max',
                  help="Se marca una alerta cuando una sola posición supera este porcentaje de tu cuenta.")
        st.number_input("Rendimiento de referencia para el efectivo (% anual)", 0.0, 15.0, step=0.1, key='pref_tasa',
                        help="Para estimar cuánto deja de ganar el efectivo ocioso (p. ej. la tasa de las letras del Tesoro).")
        config = {
            "watchlist": [t.strip().upper() for t in st.session_state.pref_wl.split(',') if t.strip()],
            "max_peso_posicion": int(st.session_state.pref_max),
            "tasa_efectivo": float(st.session_state.pref_tasa),
        }
        save_config(config)
        st.caption("Tus preferencias quedan en la dirección (URL) de la página: guárdala en favoritos para conservarlas.")
    max_peso = config['max_peso_posicion']
    tasa_efectivo = config['tasa_efectivo']

# Con archivos en caché usa esos; sin ellos cae a la carpeta data/ local
df, duplicados_removidos = load_data(uploaded_files=list(st.session_state.csv_cache.values()))

if df.empty:
    st.markdown("<h2>📈 Terminal de Inversiones</h2>", unsafe_allow_html=True)
    st.info("👋 **¡Bienvenido al Terminal de Inversiones!**")
    st.markdown("""
        Carga tus archivos CSV de transacciones de Hapi para comenzar el análisis.
        Se procesan solo durante esta sesión: al cerrar la pestaña no queda nada guardado.
    """)

    antes = len(st.session_state.csv_cache)
    registrar_archivos(st.file_uploader(
        "Arrastra y suelta tus archivos CSV aquí:",
        type=["csv"], accept_multiple_files=True, key="main_uploader"))
    if len(st.session_state.csv_cache) > antes:
        st.rerun()      # solo si entraron archivos nuevos: corta el bucle
    st.stop()

if duplicados_removidos:
    st.sidebar.caption(f"🧹 Se descartaron {duplicados_removidos} filas repetidas entre archivos que se superponen.")

# --- 4. CONFIGURACIÓN INTERNA DE ANÁLISIS ---
watchlist = config['watchlist']
score_threshold = 65

# --- 5. CABECERA Y FILTROS ---
c_title, c_f1, c_rep = st.columns([1.5, 0.9, 0.85], gap="medium", vertical_alignment="bottom")
ultima_fecha_csv = df['Date_Master'].max()
with c_title:
    st.markdown("<h2 style='margin-top: -15px;'>📈 Terminal de Inversiones</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='color: #888888; font-size: 0.85em; margin-top: -15px;'>Última sincronización de base de datos: <b>{ultima_fecha_csv.strftime('%d-%m-%Y')}</b></p>", unsafe_allow_html=True)

min_date = df['Date_Master'].min().date()
max_date = datetime.today().date()
selected_date = c_f1.date_input("Fecha de Análisis de Corte:", value=max_date, min_value=min_date, max_value=max_date, format="DD/MM/YYYY")
selected_date_dt = pd.to_datetime(selected_date)
es_fecha_actual = selected_date == max_date
slot_reporte = c_rep.empty()
df_filtered = df[(df['Date_Master'] <= selected_date_dt)].copy()

# --- 6. MOTOR FINANCIERO CORE ---
cat = df_filtered['Categoria']
total_depositos = df_filtered.loc[cat == 'DEPOSITO', 'Net Amount'].sum()
total_retiros = -df_filtered.loc[cat == 'RETIRO', 'Net Amount'].sum()
total_cripto = df_filtered.loc[cat == 'CRIPTO', 'Net Amount'].sum()
aportes_netos = total_depositos - total_retiros + total_cripto

df_prime = df_filtered[cat == 'PRIME'].copy()
total_prime_paid = -df_prime['Net Amount'].sum()

# DIVIDENDOS (montos brutos positivos y retención negativa vienen en filas separadas)
df_divs = df_filtered[cat == 'DIVIDENDO'].copy()
divs_brutos = df_divs.loc[df_divs['Net Amount'] > 0, 'Net Amount'].sum()
divs_retenidos = -df_divs.loc[df_divs['Net Amount'] < 0, 'Net Amount'].sum()
df_intereses = df_filtered[cat.isin(['INTERES', 'LENDING'])].copy()
total_intereses = df_intereses['Net Amount'].sum()

# Liquidez = efectivo + money market (los barridos internos se compensan entre sí)
poder_compra = df_filtered['Net Amount'].sum()

df_trades = df_filtered[cat == 'TRADE'].copy()
df_trades['Comision'] = df_trades['Fees'] + df_trades['Commissions']
df_trades['Monto'] = df_trades['Qty'].abs().fillna(0) * df_trades['Price']
df_trades['Es_Venta'] = df_trades['Trade Action'].str.upper().str.contains('SELL|LIQ', regex=True)
total_fees_paid = df_trades['Comision'].sum()

# PORTAFOLIO Y TRADES
def construir_portafolio(df_ops):
    """Costo promedio (con comisiones incluidas) por activo. Los lotes FIFO solo se usan para medir cuánto tiempo mantuviste cada acción."""
    portfolio, lotes, realized = {}, {}, []
    for _, row in df_ops.iterrows():
        sym = row['Symbol']
        if not sym or sym == 'ISPAZ':
            continue
        p = portfolio.setdefault(sym, {'qty': 0.0, 'total_cost': 0.0, 'avg_cost': 0.0, 'realized_pl': 0.0, 'fees': 0.0})
        lq = lotes.setdefault(sym, [])
        qty = abs(float(row['Qty'])) if pd.notna(row['Qty']) else 0.0
        price = float(row['Price'])
        fee = float(row['Fees']) + float(row['Commissions'])
        fecha = row['Date_Master']

        # AJUSTES DE POSICION (splits, splits inversos, spin-offs):
        # cambian el numero de acciones sin mover el costo base. Se recalcula el precio promedio.
        if row['Categoria'] == 'AJUSTE':
            delta = -qty if row['Transfer Direction'].upper() == 'OUTGOING' else qty
            nueva = p['qty'] + delta
            if nueva > 1e-9:
                factor = nueva / p['qty'] if p['qty'] > 1e-9 else 1.0
                p['qty'] = nueva
                p['avg_cost'] = p['total_cost'] / nueva
                for lote in lq:
                    lote[0] *= factor
            else:
                p.update({'qty': 0.0, 'total_cost': 0.0, 'avg_cost': 0.0})
                lq.clear()
            continue

        accion = row['Trade Action'].upper()
        if 'SELL' in accion or 'LIQ' in accion:
            q = min(qty, p['qty'])
            if q <= 1e-9:
                continue
            costo = p['avg_cost'] * q
            ingreso = q * price - fee
            pl = ingreso - costo
            dias_pond, restante = 0.0, q
            while restante > 1e-9 and lq:
                tomar = min(restante, lq[0][0])
                dias_pond += tomar * (fecha - lq[0][1]).days
                lq[0][0] -= tomar
                restante -= tomar
                if lq[0][0] <= 1e-9:
                    lq.pop(0)
            p['realized_pl'] += pl
            p['qty'] -= q
            p['total_cost'] -= costo
            p['fees'] += fee
            if p['qty'] < 1e-6:
                p.update({'qty': 0.0, 'total_cost': 0.0, 'avg_cost': 0.0})
                lq.clear()
            realized.append({'Date': fecha, 'Symbol': sym, 'Profit': pl, 'Type': 'Venta', 'Sell Price': price, 'Qty': q,
                             'Costo': costo, 'Ingreso': ingreso, 'Dias': dias_pond / q})
        elif accion.startswith('BUY') or accion in ['DIVIDEND REINVEST', 'REINVESTMENT']:
            if qty <= 0:
                continue
            p['total_cost'] += qty * price + fee
            p['qty'] += qty
            p['avg_cost'] = p['total_cost'] / p['qty']
            p['fees'] += fee
            lq.append([qty, fecha])
    return portfolio, realized

df_ops = df_filtered[cat.isin(['TRADE', 'AJUSTE'])]
portfolio, realized_history = construir_portafolio(df_ops)
for _, r in pd.concat([df_divs, df_intereses]).iterrows():
    realized_history.append({'Date': r['Date_Master'], 'Symbol': r['Symbol'], 'Profit': r['Net Amount'],
                             'Type': 'Dividendo' if r['Categoria'] == 'DIVIDENDO' else 'Interés'})

# MERCADO E INDICADORES TÉCNICOS
# --- 7. GESTIÓN DE CACHÉ Y DESCARGA CONCURRENTE ---
CACHE_VERSION = 2

def cache_vacio():
    return {"version": CACHE_VERSION, "last_updated_prices": 0.0, "price_start": None, "prices": {}, "sin_datos": [],
            "last_updated_fundamentals": 0.0, "fundamentals": {}}

def load_local_cache():
    data = cache_vacio()
    cache_path = get_cache_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                guardado = json.load(f)
            # Los fundamentales sirven entre versiones; los precios se vuelven a bajar con el formato nuevo
            data['fundamentals'] = guardado.get('fundamentals', {})
            data['last_updated_fundamentals'] = guardado.get('last_updated_fundamentals', 0.0)
            if guardado.get('version') == CACHE_VERSION:
                data.update(guardado)
        except Exception:
            pass
    return data

def save_local_cache(cache_data):
    cache_path = get_cache_path()
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        pass

FUND_VACIO = {'pe_ratio': None, 'target_price': None, 'dividend_yield': 0, 'name': None, 'sector': 'N/A', 'quote_type': 'EQUITY', 'beta': 1.0, 'earnings_date': None}

def fetch_single_fundamental(s):
    try:
        ticker = yf.Ticker(s.replace('.', '-'))
        info = ticker.info

        # Fallback values for beta
        beta_val = info.get('beta') or info.get('trailingBeta')
        if beta_val is None:
            if '-USD' in s.upper() or s.upper() in ['BTC-USD', 'ETH-USD']:
                beta_val = 1.5
            elif s.upper() in ['SLV', 'GLD']:
                beta_val = 0.2
            else:
                beta_val = 1.0

        proximo_reporte = None
        if info.get('quoteType', 'EQUITY') == 'EQUITY':      # ETFs y cripto no reportan resultados
            try:
                cal = ticker.calendar
                fechas = cal.get('Earnings Date') if isinstance(cal, dict) else None
                if fechas:
                    proximo_reporte = min(pd.Timestamp(f) for f in fechas).strftime('%Y-%m-%d')
            except Exception:
                pass

        return s, {
            'pe_ratio': info.get('forwardPE') or info.get('trailingPE'),
            'target_price': info.get('targetMeanPrice'),
            'dividend_yield': info.get('dividendYield') or 0,      # Yahoo ya lo entrega en %
            'fund_v': 2,
            'name': info.get('longName', s),
            'sector': info.get('sector', 'N/A'),
            'quote_type': info.get('quoteType', 'EQUITY'),
            'beta': beta_val,
            'earnings_date': proximo_reporte,
        }
    except Exception:
        return s, dict(FUND_VACIO, name=s)

def get_fundamental_data_cached(symbols, cache_data, force_refresh=False):
    now = time.time()
    needs_update = []

    # Expiración de fundamentos: 7 días
    is_expired = (now - cache_data.get("last_updated_fundamentals", 0.0)) > 604800

    for s in symbols:
        if force_refresh or is_expired or cache_data["fundamentals"].get(s, {}).get('fund_v') != 2:
            needs_update.append(s)

    if needs_update:
        with ThreadPoolExecutor(max_workers=min(10, len(needs_update))) as executor:
            results = executor.map(fetch_single_fundamental, needs_update)
            for s, info in results:
                cache_data["fundamentals"][s] = info
        cache_data["last_updated_fundamentals"] = now
        save_local_cache(cache_data)

    return {s: cache_data["fundamentals"].get(s, dict(FUND_VACIO, name=s)) for s in symbols}

def get_price_history(symbols, start_date, cache_data, force_refresh=False, permitir_descarga=True):
    """Historial diario completo (ajustado por splits) + splits y dividendos. Devuelve también si hubo una descarga fallida."""
    now = time.time()
    inicio = pd.Timestamp(start_date).strftime('%Y-%m-%d')
    fresco = (now - cache_data.get("last_updated_prices", 0.0)) < 14400      # 4 horas
    cubre = bool(cache_data.get("price_start")) and cache_data["price_start"] <= inicio
    conocidos = set(cache_data["prices"]) | set(cache_data.get("sin_datos", []))
    faltan = [s for s in symbols if s not in conocidos]
    fallo = False

    if symbols and permitir_descarga and (force_refresh or not fresco or not cubre or faltan):
        mapa = {s: s.replace('.', '-') for s in symbols}
        try:
            raw = yf.download(sorted(set(mapa.values())), start=inicio,
                              end=(datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
                              actions=True, auto_adjust=False, progress=False, threads=True, multi_level_index=True)
        except Exception:
            raw = None
        niveles = raw.columns.get_level_values(0) if raw is not None and not raw.empty else []
        if 'Close' in niveles:
            closes = raw['Close']
            splits_df = raw['Stock Splits'] if 'Stock Splits' in niveles else pd.DataFrame()
            divs_df = raw['Dividends'] if 'Dividends' in niveles else pd.DataFrame()
            sin_datos = []
            for s, s_yf in mapa.items():
                serie = closes[s_yf].dropna() if s_yf in closes.columns else pd.Series(dtype=float)
                if serie.empty:
                    sin_datos.append(s)
                    continue
                sp = splits_df[s_yf].fillna(0) if s_yf in splits_df.columns else pd.Series(dtype=float)
                dv = divs_df[s_yf].fillna(0) if s_yf in divs_df.columns else pd.Series(dtype=float)
                cache_data["prices"][s] = {
                    "dates": [d.strftime('%Y-%m-%d') for d in serie.index],
                    "close": [round(float(v), 6) for v in serie.values],
                    "splits": {d.strftime('%Y-%m-%d'): float(v) for d, v in sp[sp > 0].items()},
                    "divs": {d.strftime('%Y-%m-%d'): float(v) for d, v in dv[dv > 0].items()},
                }
            cache_data["sin_datos"] = sin_datos
            cache_data["last_updated_prices"] = now
            cache_data["price_start"] = inicio
            save_local_cache(cache_data)
        else:
            fallo = True

    # Fallback a caché si falla la red
    closes, splits, divs = {}, {}, {}
    for s in symbols:
        d = cache_data["prices"].get(s)
        if d:
            closes[s] = pd.Series(d["close"], index=pd.to_datetime(d["dates"]))
            splits[s] = d.get("splits", {})
            divs[s] = d.get("divs", {})
    return pd.DataFrame(closes).sort_index(), splits, divs, fallo

def precios_sin_ajuste(closes, splits):
    # Yahoo entrega precios ajustados por splits; se "des-ajustan" para valorar las acciones tal como las tenías ese día
    raw = closes.copy()
    for s, eventos in splits.items():
        if s not in raw.columns:
            continue
        for fecha, ratio in eventos.items():
            if ratio and ratio > 0:
                raw.loc[raw.index < pd.Timestamp(fecha), s] *= ratio
    return raw

def factor_split_posterior(eventos, fecha):
    factor = 1.0
    for f, ratio in (eventos or {}).items():
        if pd.Timestamp(f) > fecha and ratio and ratio > 0:
            factor *= ratio
    return factor

def precios_en_vivo(symbols):
    # Último precio disponible en Yahoo (~15 min de retraso en horario de mercado)
    if not symbols:
        return {}
    try:
        raw = yf.download(sorted({s.replace('.', '-') for s in symbols}), period='5d', interval='1d',
                          auto_adjust=False, progress=False, threads=True, timeout=8, multi_level_index=True)
        closes = raw['Close']
        return {s: float(closes[s.replace('.', '-')].dropna().iloc[-1]) for s in symbols
                if s.replace('.', '-') in closes.columns and not closes[s.replace('.', '-')].dropna().empty}
    except Exception:
        return {}

def indicadores_tecnicos(serie):
    serie = serie.dropna()
    if len(serie) < 15:
        return None
    precio = serie.iloc[-1]
    rsi = compute_rsi(serie).iloc[-1]
    sma_50 = serie.rolling(window=50).mean().iloc[-1] if len(serie) >= 50 else None
    sma_200 = serie.rolling(window=200).mean().iloc[-1] if len(serie) >= 200 else None
    if sma_50 is not None and sma_200 is not None:
        if precio > sma_200 and sma_50 > sma_200:
            trend = "Alcista"
        elif precio < sma_200 and sma_50 < sma_200:
            trend = "Bajista"
        else:
            trend = "Lateral"
    else:
        trend = "N/D"
    signal = "🔵 Sobrevendido" if rsi < 30 else "🟠 Sobrecomprado" if rsi > 70 else "⚪ Neutral"
    ultimo_ano = serie[serie.index >= serie.index[-1] - pd.Timedelta(days=365)]
    anio_previo = serie[serie.index.year < serie.index[-1].year]
    return {
        'rsi': int(round(rsi)) if pd.notna(rsi) else 50,
        'signal': signal,
        'trend': trend,
        'sma_50': sma_50,
        'sma_200': sma_200,
        'dist_max52': (precio / ultimo_ano.max() - 1) * 100,
        'ytd': (precio / anio_previo.iloc[-1] - 1) * 100 if not anio_previo.empty else None,
        'dia': (precio / serie.iloc[-2] - 1) * 100 if len(serie) > 1 else None,
    }

# --- 8. EJECUCIÓN DEL MOTOR DE MERCADO ---
current_prices, tech_signals, fund_data = {}, {}, {}
simbolos_cartera = [s for s, d in portfolio.items() if d['qty'] > 0]
simbolos_operados = sorted({s for s in df_ops['Symbol'].unique() if es_ticker_valido(s)})
simbolos_precio = sorted(set(simbolos_operados) | {s for s in watchlist if es_ticker_valido(s)} | {BENCHMARK})
simbolos_radar = sorted(set(simbolos_cartera) | set(watchlist))
closes = pd.DataFrame()
splits, divs_mercado = {}, {}

with st.spinner("Sincronizando análisis avanzado (Técnico + Fundamental)..."):
    cache_data = load_local_cache()
    force_ref = st.session_state.get('force_refresh', False)
    permitir = force_ref or (time.time() - st.session_state.get('fallo_descarga', 0.0) > 600)

    closes, splits, divs_mercado, fallo = get_price_history(
        simbolos_precio, pd.Timestamp(min_date) - timedelta(days=320), cache_data,
        force_refresh=force_ref, permitir_descarga=permitir)
    if fallo:
        st.session_state['fallo_descarga'] = time.time()
    fund_data = get_fundamental_data_cached([s for s in simbolos_radar if es_ticker_valido(s)], cache_data, force_refresh=force_ref)

    # Reset force refresh flag
    st.session_state.force_refresh = False

closes = closes[closes.index <= selected_date_dt] if not closes.empty else closes
closes_raw = precios_sin_ajuste(closes, splits) if not closes.empty else closes
fecha_precios = closes.index.max() if not closes.empty else None

for s in set(simbolos_cartera) | set(simbolos_radar):
    if s in closes_raw.columns and not closes_raw[s].dropna().empty:
        current_prices[s] = float(closes_raw[s].dropna().iloc[-1])
        ind = indicadores_tecnicos(closes[s])
        if ind:
            tech_signals[s] = ind
    else:
        current_prices[s] = portfolio.get(s, {}).get('avg_cost', 0.0)

if fallo and closes.empty:
    st.warning("⚠️ No se pudieron descargar precios de mercado y no hay caché local: las posiciones se valoran a su costo promedio.")
elif fallo:
    st.caption("⚠️ Sin conexión con Yahoo Finance: se usan los últimos precios guardados en caché.")

# CONSTRUCCIÓN DE DATA FINAL
holdings_data = []
for sym, data in portfolio.items():
    if data['qty'] > 0:
        precio_actual = current_prices.get(sym, data['avg_cost'])
        unrealized_pl = precio_actual * data['qty'] - data['total_cost']
        pl_percent = (unrealized_pl / data['total_cost']) * 100 if data['total_cost'] > 0 else 0
        tech = tech_signals.get(sym, {})
        holdings_data.append({
            "Activo": sym,
            "Acciones": data['qty'],
            "Costo Prom.": data['avg_cost'],
            "P. Actual": precio_actual,
            "Día (%)": tech.get('dia'),
            "Valor Total": data['qty'] * precio_actual,
            "Flotante ($)": unrealized_pl,
            "Flotante (%)": pl_percent,
            "Técnico": f"{tech['signal'].split()[0]} RSI {tech['rsi']} · {tech['trend']}" if tech else "Sin datos",
        })

valor_posiciones = sum([d['Valor Total'] for d in holdings_data])
balance_total = poder_compra + valor_posiciones
for d in holdings_data:
    d['Peso (%)'] = d['Valor Total'] / balance_total * 100 if balance_total > 0 else 0
holdings_data.sort(key=lambda d: -d['Valor Total'])
peso_caja = poder_compra / balance_total * 100 if balance_total > 0 else 0

total_realized = sum([x['Profit'] for x in realized_history if x['Type'] == 'Venta'])
total_unrealized = sum([d['Flotante ($)'] for d in holdings_data])
costo_total_plataforma = total_fees_paid + total_prime_paid
# Todo lo que no es aporte es resultado: ventas, flotante, dividendos, intereses, comisiones y Prime
total_historico = balance_total - aportes_netos

# --- 9. CURVA PATRIMONIAL Y COMPARACIÓN CONTRA EL MERCADO ---
def curva_patrimonio(df_f, precios_raw, fecha_fin):
    dias = pd.date_range(df_f['Date_Master'].min(), fecha_fin, freq='D')
    ops = df_f[df_f['Categoria'].isin(['TRADE', 'AJUSTE'])].copy()
    accion = ops['Trade Action'].str.upper()
    salida = np.where(ops['Categoria'] == 'AJUSTE', ops['Transfer Direction'].str.upper() == 'OUTGOING',
                      accion.str.contains('SELL|LIQ', regex=True))
    ops['delta'] = ops['Qty'].abs().fillna(0) * np.where(salida, -1, 1)
    pos = ops.pivot_table(index='Date_Master', columns='Symbol', values='delta', aggfunc='sum').reindex(dias).fillna(0).cumsum()
    pos = pos.where(pos.abs() > 1e-7, 0.0)

    precios = precios_raw.reindex(columns=pos.columns)
    precios = precios.reindex(precios.index.union(dias)).ffill().reindex(dias)
    # Sin cotización en Yahoo (códigos raros) o antes del primer dato: precio de tu última operación
    px_ops = ops[ops['Categoria'] == 'TRADE'].groupby(['Date_Master', 'Symbol'])['Price'].last().unstack()
    px_ops = px_ops.reindex(px_ops.index.union(dias)).ffill().reindex(dias)
    precios = precios.combine_first(px_ops.reindex(columns=pos.columns)).fillna(0.0)

    invertido = (pos * precios).sum(axis=1)
    caja = df_f.groupby('Date_Master')['Net Amount'].sum().reindex(dias).fillna(0).cumsum()
    flujo = df_f[df_f['Categoria'].isin(['DEPOSITO', 'RETIRO', 'CRIPTO'])].groupby('Date_Master')['Net Amount'].sum().reindex(dias).fillna(0)
    return pd.DataFrame({'Valor': invertido + caja, 'Invertido': invertido, 'Caja': caja, 'Flujo': flujo, 'Aportes': flujo.cumsum()})

def simular_benchmark(flujo, serie_bench, divs_bench):
    # Mismos aportes, mismas fechas, todo a VOO; dividendos reinvertidos tras la retención del 30%
    px_b = serie_bench.reindex(serie_bench.index.union(flujo.index)).ffill().reindex(flujo.index).bfill()
    if px_b.isna().all():
        return None
    dv = pd.Series({pd.Timestamp(k): v for k, v in (divs_bench or {}).items()}, dtype=float).reindex(flujo.index).fillna(0.0)
    acciones, valores = 0.0, []
    for d in flujo.index:
        p = px_b[d]
        if dv[d] > 0 and acciones > 0:
            acciones += acciones * dv[d] * (1 - RETENCION_DIVIDENDOS) / p
        if flujo[d] != 0:
            acciones += flujo[d] / p
        valores.append(acciones * p)
    return pd.Series(valores, index=flujo.index)

def beta_historica(serie, serie_bench, dias=252):
    datos = pd.concat([serie, serie_bench], axis=1).dropna().iloc[-(dias + 1):]
    if len(datos) < 60:
        return None
    r = datos.pct_change().dropna()
    varianza = r.iloc[:, 1].var()
    return float(r.iloc[:, 0].cov(r.iloc[:, 1]) / varianza) if varianza > 0 else None

def retornos_twr(valor, flujo):
    previo = valor.shift(1)
    return ((valor - flujo) / previo - 1).where(previo > 1.0).fillna(0.0)

def xirr(fechas, montos):
    montos = np.array(montos, dtype=float)
    if len(montos) < 2 or not (montos.min() < 0 < montos.max()):
        return None
    anios = np.array([(f - fechas[0]).days / 365.25 for f in fechas])
    if anios[-1] < 0.25:
        return None
    vpn = lambda tasa: np.sum(montos / (1 + tasa) ** anios)
    lo, hi = -0.99, 10.0
    if vpn(lo) * vpn(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if vpn(mid) > 0:
            lo = mid
        else:
            hi = mid
    return mid

curva = curva_patrimonio(df_filtered, closes_raw, selected_date_dt)
# El último punto se alinea con la valoración actual (puede incluir precios en caché más nuevos)
curva.iloc[-1, curva.columns.get_loc('Valor')] = balance_total
r_cuenta = retornos_twr(curva['Valor'], curva['Flujo'])
idx_cuenta = (1 + r_cuenta).cumprod()
dd_cuenta = idx_cuenta / idx_cuenta.cummax() - 1
habiles = curva.index.dayofweek < 5
vol_cuenta = r_cuenta[habiles].std() * np.sqrt(252)

serie_voo = closes[BENCHMARK] if BENCHMARK in closes.columns else pd.Series(dtype=float)
curva['VOO'] = simular_benchmark(curva['Flujo'], serie_voo, divs_mercado.get(BENCHMARK)) if not serie_voo.empty else np.nan
if curva['VOO'].notna().any():
    r_voo = retornos_twr(curva['VOO'], curva['Flujo'])
    idx_voo = (1 + r_voo).cumprod()
    dd_voo = idx_voo / idx_voo.cummax() - 1
    vol_voo = r_voo[habiles].std() * np.sqrt(252)
else:
    r_voo = idx_voo = dd_voo = None
    vol_voo = None

flujos_inversor = [(d, -m) for d, m in curva['Flujo'][curva['Flujo'] != 0].items()]
tir_cuenta = xirr([f for f, _ in flujos_inversor] + [curva.index[-1]], [m for _, m in flujos_inversor] + [balance_total])
tir_voo = xirr([f for f, _ in flujos_inversor] + [curva.index[-1]], [m for _, m in flujos_inversor] + [curva['VOO'].iloc[-1]]) if r_voo is not None else None

anios = curva.index.year
resumen_anual = pd.DataFrame({
    'Aportes netos': curva['Flujo'].groupby(anios).sum(),
    'Tu rentabilidad': ((1 + r_cuenta).groupby(anios).prod() - 1) * 100,
})
if r_voo is not None:
    resumen_anual['VOO (mismos aportes)'] = ((1 + r_voo).groupby(anios).prod() - 1) * 100
resumen_anual['Valor al cierre'] = curva['Valor'].groupby(anios).last()
_rh = pd.DataFrame(realized_history)
if not _rh.empty:
    _rh['Año'] = pd.to_datetime(_rh['Date']).dt.year
    resumen_anual['Ganancia en ventas'] = _rh[_rh['Type'] == 'Venta'].groupby('Año')['Profit'].sum()
    resumen_anual['Dividendos e intereses'] = _rh[_rh['Type'] != 'Venta'].groupby('Año')['Profit'].sum()
resumen_anual['Costos (comisiones + Prime)'] = (df_trades.groupby(df_trades['Date_Master'].dt.year)['Comision'].sum()
                                               .add(df_prime.groupby(df_prime['Date_Master'].dt.year)['Net Amount'].sum().abs(), fill_value=0))
resumen_anual = resumen_anual.fillna(0.0)
resumen_anual.index.name = 'Año'

# --- 10. DIAGNÓSTICO DE COMPORTAMIENTO ---
ventas = [x for x in realized_history if x['Type'] == 'Venta']
df_ventas = pd.DataFrame(ventas)
win_rate = (len([x for x in ventas if x['Profit'] > 0]) / len(ventas)) * 100 if ventas else None

# Prime: cuánto ahorraste en comisiones frente a lo que pagaste
periodos_prime = [(d, d + timedelta(days=30)) for d in df_prime['Date_Master']]
df_trades['Con_Prime'] = df_trades['Date_Master'].apply(lambda f: any(a <= f < b for a, b in periodos_prime))
_compras = df_trades[~df_trades['Es_Venta']]
_c_con = _compras.loc[_compras['Con_Prime'], 'Comision'].median()
_c_sin = _compras.loc[~_compras['Con_Prime'], 'Comision'].median()
dif_comision_prime = (_c_sin - _c_con) if pd.notna(_c_con) and pd.notna(_c_sin) and _c_sin > _c_con else 0.10
ahorro_prime = int(df_trades['Con_Prime'].sum()) * dif_comision_prime

# Efectivo ocioso
ultimo_ano_curva = curva[curva.index > selected_date_dt - timedelta(days=365)]
caja_promedio_12m = ultimo_ano_curva['Caja'].clip(lower=0).mean()
intereses_12m = df_intereses[df_intereses['Date_Master'] > selected_date_dt - timedelta(days=365)]['Net Amount'].sum()
rend_caja_12m = intereses_12m / caja_promedio_12m * 100 if caja_promedio_12m > 1 else 0.0
costo_oportunidad_anual = max(poder_compra, 0) * tasa_efectivo / 100

# Próximos reportes de resultados de tus posiciones (14 días)
proximos_reportes = []
for s in simbolos_cartera:
    f_rep = fund_data.get(s, {}).get('earnings_date')
    if f_rep:
        f_rep = pd.Timestamp(f_rep)
        if selected_date_dt <= f_rep <= selected_date_dt + timedelta(days=14):
            proximos_reportes.append((f_rep, s))
proximos_reportes.sort()

# Alertas según tus reglas
alertas = []
excedidas = [d for d in holdings_data if d['Peso (%)'] > max_peso]
if excedidas:
    alertas.append(('roja', "Concentración: " + ", ".join(f"<b>{d['Activo']}</b> {d['Peso (%)']:.1f}%" for d in excedidas)
                            + f" de la cuenta, por encima de tu límite de {max_peso}% por posición."))
caidas = [d for d in holdings_data if d['Flotante (%)'] <= -30]
if caidas:
    alertas.append(('ambar', "Pérdidas profundas contra tu costo: " + ", ".join(f"<b>{d['Activo']}</b> {d['Flotante (%)']:.0f}% ({fmt_usd(d['Flotante ($)'])})" for d in caidas) + "."))
if peso_caja >= 15 and rend_caja_12m < 1:
    alertas.append(('ambar', f"Efectivo de <b>${poder_compra:,.0f}</b> ({peso_caja:.0f}% de la cuenta) rindiendo ≈{rend_caja_12m:.2f}% anual. "
                             f"A {tasa_efectivo:.1f}% equivaldría a ~<b>${costo_oportunidad_anual:,.0f}/año</b>."))
if periodos_prime and periodos_prime[-1][1] > selected_date_dt:
    alertas.append(('ambar', f"Hapi Prime está activo. En total pagaste <b>${total_prime_paid:,.2f}</b> y ahorraste ~<b>${ahorro_prime:,.2f}</b> en comisiones."))
if proximos_reportes:
    alertas.append(('azul', "Próximos resultados trimestrales: " + ", ".join(f"<b>{s}</b> ({fecha_corta(f)})" for f, s in proximos_reportes) + "."))
if es_fecha_actual and (pd.Timestamp(max_date) - ultima_fecha_csv).days > 7:
    alertas.append(('azul', f"Tus transacciones llegan hasta el {ultima_fecha_csv:%d-%m-%Y}. Si operaste después, descarga el CSV más reciente de Hapi."))

# --- 11. REPORTE DESCARGABLE ---
def css_signo(v):
    return 'pos' if v and v > 0 else 'neg' if v and v < 0 else ''

def svg_lineas(df_series, colores, alto=230):
    df_series = df_series.dropna(how='all')
    if len(df_series) < 2:
        return ''
    ancho, izq, der, arr, aba = 860, 62, 14, 12, 26
    w, h = ancho - izq - der, alto - arr - aba
    # Escala con saltos redondos (1, 2, 2.5 o 5 × 10^n) y como máximo 5 tramos
    maximo = max(float(np.nanmax(df_series.values)), 1.0)
    magnitud = 10 ** np.floor(np.log10(maximo / 5))
    paso = next(m * magnitud for m in [1, 2, 2.5, 5, 10] if maximo / (m * magnitud) <= 5)
    tramos = int(np.ceil(maximo / paso))
    vmax = paso * tramos
    n = len(df_series)
    X = lambda i: izq + w * i / (n - 1)
    Y = lambda v: arr + h * (1 - v / vmax)
    out = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Evolución del valor de la cuenta">']
    for k in range(tramos + 1):
        v = paso * k
        out.append(f'<line x1="{izq}" x2="{ancho - der}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e6e9ee"/>')
        out.append(f'<text x="{izq - 8}" y="{Y(v) + 3.5:.1f}" text-anchor="end" font-size="10" fill="#6b7480">${v:,.0f}</text>')
    for i in sorted({0, n // 3, 2 * n // 3, n - 1}):
        d = df_series.index[i]
        ancla = 'start' if i == 0 else 'end' if i == n - 1 else 'middle'
        out.append(f'<text x="{X(i):.1f}" y="{alto - 8}" text-anchor="{ancla}" font-size="10" fill="#6b7480">{MESES[d.month - 1]} {d.year}</text>')
    for col, color in colores.items():
        if col not in df_series:
            continue
        pts = ' '.join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(df_series[col].values) if pd.notna(v))
        guion = ' stroke-dasharray="5 4"' if col == 'Aportes' else ''
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"{guion} stroke-linejoin="round"/>')
    out.append('</svg>')
    return ''.join(out)

CSS_REPORTE = """
:root { --ink:#1b1f24; --muted:#5f6b7a; --line:#e3e7ec; --pos:#0a8f6a; --neg:#c93a35; --accent:#0b6bcb; --soft:#f5f7fa; }
* { box-sizing: border-box; }
body { margin:0; background:#ffffff; color:var(--ink); font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; font-size:13px; line-height:1.45; }
.page { max-width:900px; margin:0 auto; padding:28px 24px 40px; }
header { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; border-bottom:2px solid var(--ink); padding-bottom:10px; flex-wrap:wrap; }
h1 { font-size:22px; margin:0; letter-spacing:-0.01em; }
.sub { color:var(--muted); font-size:12px; text-align:right; }
h2 { font-size:12px; margin:26px 0 8px; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:16px; }
.kpi { border:1px solid var(--line); border-radius:8px; padding:9px 11px; background:var(--soft); }
.kpi .l { font-size:10.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.kpi .v { font-size:17px; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }
.kpi .d { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th { text-align:left; color:var(--muted); font-weight:600; font-size:11px; border-bottom:1px solid var(--ink); padding:5px 6px; }
td { border-bottom:1px solid var(--line); padding:5px 6px; }
th.n, td.n { text-align:right; }
tr.total td { font-weight:700; border-top:1px solid var(--ink); }
.pos { color:var(--pos); } .neg { color:var(--neg); }
.dos { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.barra { display:flex; align-items:center; gap:8px; margin:4px 0; }
.barra .t { width:110px; } .barra .b { flex:1; background:var(--soft); border-radius:4px; height:10px; overflow:hidden; }
.barra .b span { display:block; height:100%; background:var(--accent); }
.barra .p { width:52px; text-align:right; font-variant-numeric:tabular-nums; }
.leyenda { display:flex; gap:16px; font-size:11.5px; color:var(--muted); margin-top:4px; }
.leyenda i { display:inline-block; width:14px; height:3px; vertical-align:middle; margin-right:5px; }
ul { margin:0; padding-left:18px; } li { margin:3px 0; }
.nota { font-size:11px; color:var(--muted); margin-top:28px; border-top:1px solid var(--line); padding-top:10px; }
@media (max-width:640px) { .kpis { grid-template-columns:repeat(2,1fr); } .dos { grid-template-columns:1fr; } .sub { text-align:left; } }
@media print { .page { padding:0; } h2 { break-after:avoid; } table, .kpis, svg { break-inside:avoid; } }
"""

def generar_reporte_html(ctx):
    posiciones = [dict(p) for p in ctx['posiciones']]
    for p in posiciones:
        p['Valor'] = p['Acciones'] * p['Precio']
        p['Flotante'] = p['Valor'] - p['Costo']
        p['Flotante%'] = p['Flotante'] / p['Costo'] * 100 if p['Costo'] > 0 else 0.0
    invertido = sum(p['Valor'] for p in posiciones)
    total = invertido + ctx['caja']
    for p in posiciones:
        p['Peso'] = p['Valor'] / total * 100 if total > 0 else 0.0
    posiciones.sort(key=lambda p: -p['Valor'])
    beneficio = total - ctx['aportes']
    flujos = ctx['flujos_inversor']
    tir = xirr([f for f, _ in flujos] + [ctx['fecha_valor']], [m for _, m in flujos] + [total])
    e = html.escape

    kpis = [
        ("Valor total de la cuenta", fmt_usd(total), f"Posiciones {fmt_usd(invertido)} · Efectivo {fmt_usd(ctx['caja'])}"),
        ("Aportes netos", fmt_usd(ctx['aportes']), "Depósitos + traspasos cripto − retiros"),
        ("Ganancia neta", f"<span class='{css_signo(beneficio)}'>{fmt_usd(beneficio, True)}</span>",
         f"{fmt_pct(beneficio / ctx['aportes'] * 100 if ctx['aportes'] > 0 else None)} sobre lo aportado"),
        ("Efectivo disponible", fmt_usd(ctx['caja']), f"{fmt_pct(ctx['caja'] / total * 100 if total > 0 else None, False)} de la cuenta"),
        ("Rentabilidad anual (TIR)", fmt_pct(tir * 100 if tir is not None else None), f"VOO con tus mismos aportes: {fmt_pct(ctx['tir_voo'] * 100 if ctx['tir_voo'] is not None else None)}"),
        ("Rentabilidad acumulada (TWR)", fmt_pct(ctx['twr']), f"VOO mismo periodo: {fmt_pct(ctx['twr_voo'])}"),
        ("Caída máxima", fmt_pct(ctx['max_dd']), f"VOO: {fmt_pct(ctx['max_dd_voo'])}"),
        ("Costos de plataforma", fmt_usd(ctx['costos']), f"Comisiones {fmt_usd(ctx['comisiones'])} · Prime {fmt_usd(ctx['prime'])}"),
    ]
    filas = ''.join(
        f"<tr><td><b>{e(p['Activo'])}</b></td><td class='n'>{p['Acciones']:,.4f}</td><td class='n'>{fmt_usd(p['Costo'] / p['Acciones'])}</td>"
        f"<td class='n'>{fmt_usd(p['Precio'])}</td><td class='n'>{fmt_usd(p['Valor'])}</td><td class='n'>{p['Peso']:.1f}%</td>"
        f"<td class='n {css_signo(p['Flotante'])}'>{fmt_usd(p['Flotante'], True)}</td><td class='n {css_signo(p['Flotante'])}'>{fmt_pct(p['Flotante%'])}</td></tr>"
        for p in posiciones)
    filas += (f"<tr><td>Efectivo</td><td></td><td></td><td></td><td class='n'>{fmt_usd(ctx['caja'])}</td>"
              f"<td class='n'>{ctx['caja'] / total * 100 if total > 0 else 0:.1f}%</td><td></td><td></td></tr>")
    flot_total = sum(p['Flotante'] for p in posiciones)
    filas += (f"<tr class='total'><td>Total</td><td></td><td></td><td></td><td class='n'>{fmt_usd(total)}</td><td class='n'>100%</td>"
              f"<td class='n {css_signo(flot_total)}'>{fmt_usd(flot_total, True)}</td><td></td></tr>")

    clases = {}
    for p in posiciones:
        grupo = ctx['sector'].get(p['Activo'], 'Otros')
        clases[grupo] = clases.get(grupo, 0) + p['Valor']
    clases['Efectivo'] = ctx['caja']
    barras = ''.join(
        f"<div class='barra'><span class='t'>{e(c)}</span><span class='b'><span style='width:{v / total * 100 if total > 0 else 0:.1f}%'></span></span>"
        f"<span class='p'>{v / total * 100 if total > 0 else 0:.1f}%</span></div>"
        for c, v in sorted(clases.items(), key=lambda kv: -kv[1]) if v > 0)

    anual = ctx['anual']
    cab_voo = "<th class='n'>VOO</th>" if 'VOO (mismos aportes)' in anual else ''
    filas_anual = ''.join(
        f"<tr><td>{a}</td><td class='n'>{fmt_usd(r['Aportes netos'])}</td>"
        f"<td class='n {css_signo(r['Tu rentabilidad'])}'>{fmt_pct(r['Tu rentabilidad'])}</td>"
        + (f"<td class='n'>{fmt_pct(r['VOO (mismos aportes)'])}</td>" if cab_voo else '')
        + f"<td class='n'>{fmt_usd(r.get('Ganancia en ventas', 0), True)}</td><td class='n'>{fmt_usd(r.get('Dividendos e intereses', 0))}</td>"
        f"<td class='n'>{fmt_usd(r['Costos (comisiones + Prime)'])}</td><td class='n'>{fmt_usd(r['Valor al cierre'])}</td></tr>"
        for a, r in anual.iterrows())

    mejores = ''.join(f"<li><b>{e(s)}</b>: <span class='{css_signo(v)}'>{fmt_usd(v, True)}</span></li>" for s, v in ctx['top_ganadores'])
    peores = ''.join(f"<li><b>{e(s)}</b>: <span class='{css_signo(v)}'>{fmt_usd(v, True)}</span></li>" for s, v in ctx['top_perdedores'])
    alertas_html = ''.join(f"<li>{a}</li>" for a in ctx['alertas']) or "<li>Sin alertas según tus reglas actuales.</li>"

    serie = ctx['serie']
    leyenda = ("<div class='leyenda'><span><i style='background:#0a8f6a'></i>Tu cuenta</span>"
               "<span><i style='background:#e08a00'></i>VOO con tus mismos aportes</span>"
               "<span><i style='background:#8a94a3'></i>Aportes acumulados</span></div>")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte de Portafolio {ctx['generado']:%d-%m-%Y}</title><style>{CSS_REPORTE}</style></head>
<body><div class="page">
<header><div><h1>Reporte de Portafolio</h1><div style="color:#5f6b7a">Cuenta Hapi · corte al {ctx['fecha_corte']:%d/%m/%Y}</div></div>
<div class="sub">Generado el {ctx['generado']:%d/%m/%Y a las %H:%M}<br>Precios: {e(ctx['fuente_precios'])}<br>Transacciones registradas hasta el {ctx['fecha_datos']:%d/%m/%Y}</div></header>
<div class="kpis">{''.join(f"<div class='kpi'><div class='l'>{l}</div><div class='v'>{v}</div><div class='d'>{d}</div></div>" for l, v, d in kpis)}</div>

<h2>Posiciones</h2>
<table><thead><tr><th>Activo</th><th class='n'>Acciones</th><th class='n'>Costo prom.</th><th class='n'>Precio</th><th class='n'>Valor</th><th class='n'>Peso</th><th class='n'>Flotante</th><th class='n'>%</th></tr></thead>
<tbody>{filas}</tbody></table>

<div class="dos">
<div><h2>Distribución por sector</h2>{barras}</div>
<div><h2>Alertas</h2><ul>{alertas_html}</ul></div>
</div>

<h2>Evolución histórica</h2>
{svg_lineas(serie, {'Aportes': '#8a94a3', 'VOO': '#e08a00', 'Valor': '#0a8f6a'})}
{leyenda}

<h2>Resumen por año</h2>
<table><thead><tr><th>Año</th><th class='n'>Aportes</th><th class='n'>Tu rentab.</th>{cab_voo}<th class='n'>Ganancia ventas</th><th class='n'>Dividendos/int.</th><th class='n'>Costos</th><th class='n'>Valor al cierre</th></tr></thead>
<tbody>{filas_anual}</tbody></table>

<div class="dos">
<div><h2>Mejores resultados realizados</h2><ul>{mejores or '<li>—</li>'}</ul></div>
<div><h2>Peores resultados realizados</h2><ul>{peores or '<li>—</li>'}</ul></div>
</div>

<h2>Ingresos y costos acumulados</h2>
<table><tbody>
<tr><td>Dividendos brutos</td><td class='n'>{fmt_usd(ctx['divs_brutos'])}</td><td>Retención de impuestos</td><td class='n neg'>{fmt_usd(-ctx['divs_retenidos'])}</td></tr>
<tr><td>Intereses y préstamo de valores</td><td class='n'>{fmt_usd(ctx['intereses'])}</td><td>Comisiones por operación</td><td class='n neg'>{fmt_usd(-ctx['comisiones'])}</td></tr>
<tr><td>Operaciones realizadas</td><td class='n'>{ctx['n_operaciones']}</td><td>Suscripción Hapi Prime</td><td class='n neg'>{fmt_usd(-ctx['prime'])}</td></tr>
</tbody></table>

<p class="nota">Rentabilidad TWR: mide la gestión sin el efecto de cuándo depositaste. TIR: rentabilidad anualizada considerando tus depósitos y sus fechas.
La comparación con VOO invierte cada uno de tus aportes el mismo día en el ETF del S&amp;P 500 y reinvierte sus dividendos tras la retención del 30%.
Los valores históricos usan precios de cierre de Yahoo Finance. Reporte informativo generado por tu Terminal de Inversiones; no constituye asesoría de inversión.</p>
</div></body></html>"""

desc_por_simbolo = df_ops.groupby('Symbol')['Description'].first()

def nombre_activo(sym):
    # Los códigos internos del broker (p. ej. 'V016459') se muestran con el nombre de la empresa
    if es_ticker_valido(sym):
        return sym
    nombre = str(desc_por_simbolo.get(sym, sym)).split(' --- ')[0].replace('***', '').strip().title()
    return nombre or sym

realized_por_activo = sorted(((nombre_activo(s), d['realized_pl']) for s, d in portfolio.items() if abs(d['realized_pl']) > 0.005), key=lambda kv: -kv[1])
clase_por_activo = {s: get_asset_class(s, fund_data.get(s, {}).get('quote_type', 'EQUITY')) for s in simbolos_cartera}
sector_por_activo = {s: clase if clase != 'Acciones' else SECTORES_ES.get(fund_data.get(s, {}).get('sector'), fund_data.get(s, {}).get('sector') or 'Otros')
                     for s, clase in clase_por_activo.items()}
serie_reporte = curva[['Valor', 'Aportes', 'VOO']]
serie_reporte = pd.concat([serie_reporte.iloc[::7], serie_reporte.iloc[[-1]]])
serie_reporte = serie_reporte[~serie_reporte.index.duplicated(keep='last')]
contexto_reporte = {
    'posiciones': [{'Activo': s, 'Acciones': portfolio[s]['qty'], 'Costo': portfolio[s]['total_cost'], 'Precio': current_prices.get(s, portfolio[s]['avg_cost'])} for s in simbolos_cartera],
    'caja': poder_compra, 'aportes': aportes_netos, 'flujos_inversor': flujos_inversor, 'fecha_valor': curva.index[-1],
    'tir_voo': tir_voo, 'twr': (idx_cuenta.iloc[-1] - 1) * 100, 'twr_voo': (idx_voo.iloc[-1] - 1) * 100 if idx_voo is not None else None,
    'max_dd': dd_cuenta.min() * 100, 'max_dd_voo': dd_voo.min() * 100 if dd_voo is not None else None,
    'costos': costo_total_plataforma, 'comisiones': total_fees_paid, 'prime': total_prime_paid,
    'sector': sector_por_activo, 'anual': resumen_anual, 'serie': serie_reporte,
    'top_ganadores': [kv for kv in realized_por_activo[:3] if kv[1] > 0],
    'top_perdedores': [kv for kv in realized_por_activo[::-1][:3] if kv[1] < 0],
    'alertas': [re.sub(r'<[^>]+>', '', a[1]) for a in alertas],
    'divs_brutos': divs_brutos, 'divs_retenidos': divs_retenidos, 'intereses': total_intereses, 'n_operaciones': len(df_trades),
    'fecha_corte': selected_date_dt, 'fecha_datos': ultima_fecha_csv,
}

def construir_reporte():
    # Se ejecuta al hacer clic: intenta actualizar los precios para que el reporte refleje ese momento
    ctx = dict(contexto_reporte, generado=datetime.now(),
               fuente_precios=f"cierre del {fecha_precios:%d/%m/%Y}" if fecha_precios is not None else "costo promedio (sin datos de mercado)")
    if es_fecha_actual:
        vivos = precios_en_vivo([p['Activo'] for p in ctx['posiciones'] if es_ticker_valido(p['Activo'])])
        if vivos:
            ctx['posiciones'] = [dict(p, Precio=vivos.get(p['Activo'], p['Precio'])) for p in ctx['posiciones']]
            ctx['fuente_precios'] = f"Yahoo Finance a las {ctx['generado']:%H:%M} (retraso aprox. 15 min)"
    return generar_reporte_html(ctx).encode('utf-8')

slot_reporte.download_button(
    "📄 Descargar reporte", data=construir_reporte, file_name=f"reporte_portafolio_{selected_date_dt:%Y%m%d}.html",
    mime="text/html", type="primary", on_click="ignore", width="stretch",
    help="Resumen de tu portafolio en este momento: posiciones, rendimiento, historial anual y costos. Ábrelo en el navegador; con Ctrl+P lo guardas como PDF.")

# --- 12. KPIs PRINCIPALES ---
k1, k2, k3 = st.columns(3)
k4, k5, k6 = st.columns(3)
k1.metric("Balance Patrimonial", f"${balance_total:,.2f}", f"{(curva['Valor'].iloc[-1] / curva['Valor'].iloc[-31] - 1) * 100:+.1f}% en 30d" if len(curva) > 31 and curva['Flujo'].iloc[-30:].sum() == 0 else None,
          help="Posiciones a precio de mercado + efectivo. La variación de 30 días se muestra si no hubo depósitos en ese lapso.")
k2.metric("Aportes Netos", f"${aportes_netos:,.2f}", help="Depósitos desde Hapi + traspasos desde tu cuenta cripto − retiros. No incluye el pago de Prime (eso es un costo).")
k3.metric("Beneficio Neto", f"${total_historico:,.2f}", f"{total_historico / aportes_netos * 100:+.1f}%" if aportes_netos > 0 else None,
          help="Valor actual de la cuenta − aportes netos. Incluye ventas, flotante, dividendos, intereses y descuenta comisiones y Prime.")
k4.metric("Rentab. Anual (TIR)", f"{tir_cuenta * 100:.1f}%" if tir_cuenta is not None else "N/D",
          f"VOO {tir_voo * 100:.1f}%" if tir_voo is not None else None, delta_color="off",
          help="Tasa interna de retorno anualizada de tus aportes. 'VOO' = mismos aportes, mismas fechas, invertidos en el S&P 500.")
k5.metric("Liquidez", f"${poder_compra:,.2f}", f"{peso_caja:.1f}% del total", delta_color="off", help="Efectivo + money market disponible para operar (poder de compra).")
k6.metric("Costos Plataforma", f"${costo_total_plataforma:,.2f}", delta="Comisiones + Prime", delta_color="inverse")

if alertas:
    cols_al = st.columns(2)
    for i, (tono, texto) in enumerate(alertas):
        cols_al[i % 2].markdown(f"<div class='alerta {tono}'>{texto}</div>", unsafe_allow_html=True)

st.markdown("---")

# --- 13. PESTAÑAS MAESTRAS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "💼 Portafolio",
    "📈 Rendimiento vs Mercado",
    "🧭 Diagnóstico",
    "💸 Flujos y Costos",
    "🔍 Radar de Oportunidades"
])

def color_pl(val):
    if val is None or pd.isna(val):
        return 'color: gray'
    return f'color: {COLOR_PROFIT}' if val > 0 else f'color: {COLOR_LOSS}' if val < 0 else 'color: gray'

# ==========================================
# PESTAÑA 1: PORTAFOLIO
# ==========================================
with tab1:
    if ventas:
        st.markdown(f"🎯 **Precisión Estratégica (Win Rate):** El **{win_rate:.1f}%** de tus ventas fueron rentables.")

    if holdings_data:
        df_h = pd.DataFrame(holdings_data)
        df_h['Día (%)'] = pd.to_numeric(df_h['Día (%)'], errors='coerce')

        format_dict = {'Acciones': '{:.4f}', 'Costo Prom.': '${:,.2f}', 'P. Actual': '${:,.2f}', 'Día (%)': '{:+.2f}%', 'Valor Total': '${:,.2f}',
                       'Peso (%)': '{:.1f}%', 'Flotante ($)': '${:,.2f}', 'Flotante (%)': '{:.2f}%'}
        def color_peso(val): return f'color: {COLOR_LOSS}; font-weight: bold' if val > max_peso else ''

        st.subheader("Estado Actual de Posiciones")
        columnas = ['Activo', 'Acciones', 'Costo Prom.', 'P. Actual', 'Día (%)', 'Valor Total', 'Peso (%)', 'Flotante ($)', 'Flotante (%)', 'Técnico']
        styled_df = (df_h[columnas].style.format(format_dict, na_rep='—')
                     .map(color_pl, subset=['Flotante ($)', 'Flotante (%)', 'Día (%)'])
                     .map(color_peso, subset=['Peso (%)']))
        st.dataframe(styled_df, height=min(420, 38 + 35 * len(df_h)), hide_index=True, placeholder="—")
        st.caption(md_seguro(f"Efectivo disponible: **${poder_compra:,.2f}** ({peso_caja:.1f}% de la cuenta). El costo promedio incluye comisiones de compra."
                             + (f" Precios al cierre del {fecha_precios:%d-%m-%Y}." if fecha_precios is not None else "")))

        col_p, col_r1, col_r3 = st.columns([1, 1, 1.3], gap="medium")
        with col_p:
            st.markdown("#### Distribución de Cartera")

            # Selector de tipo de distribución
            alloc_type = st.segmented_control(
                "Agrupar por:",
                options=["Activo", "Clase de Activo", "Sector"],
                default="Activo"
            ) or "Activo"

            df_alloc = df_h[['Activo', 'Valor Total']].copy()
            df_alloc['Clase de Activo'] = df_alloc['Activo'].map(clase_por_activo)

            def get_sector_grouped(x):
                clase = clase_por_activo.get(x, 'Acciones')
                if clase == 'ETFs': return 'ETFs (Sin Sector)'
                if clase in ['Criptomonedas', 'Commodities']: return clase
                sec = fund_data.get(x, {}).get('sector', 'Otros/Desconocido')
                return SECTORES_ES.get(sec, sec) if sec and sec != 'N/A' else 'Otros/Desconocido'

            df_alloc['Sector'] = df_alloc['Activo'].apply(get_sector_grouped)
            if poder_compra > 0:
                df_alloc = pd.concat([df_alloc, pd.DataFrame([{'Activo': 'Efectivo', 'Valor Total': poder_compra, 'Clase de Activo': 'Efectivo', 'Sector': 'Efectivo'}])], ignore_index=True)
            df_group = df_alloc.groupby(alloc_type)['Valor Total'].sum().reset_index()
            fig_pie = px.pie(df_group, values='Valor Total', names=alloc_type, hole=0.5,
                             color_discrete_sequence=[COLOR_CAPITAL, COLOR_PROFIT, COLOR_SIMULATION, COLOR_LOSS] + px.colors.qualitative.Plotly
                             if alloc_type == "Clase de Activo" else None)
            fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='#1e1e1e', width=2)))
            fig_pie.update_layout(showlegend=alloc_type != "Activo", legend=dict(orientation="h", y=-0.1), margin=dict(t=0, b=0, l=0, r=0), height=360)
            st.plotly_chart(fig_pie)

        with col_r1:
            df_holdings_sorted = df_h.sort_values(by='Flotante ($)', ascending=True)
            fig_unrealized = px.bar(df_holdings_sorted, x='Flotante ($)', y='Activo', orientation='h', color='Flotante ($)', color_continuous_scale=[COLOR_LOSS, COLOR_PROFIT], title="Beneficio Flotante (No Realizado)")
            fig_unrealized.update_layout(height=420, coloraxis_showscale=False)
            st.plotly_chart(fig_unrealized)

        with col_r3:
            if realized_history:
                df_evo = pd.DataFrame(realized_history).sort_values('Date')
                df_evo['Acumulado'] = df_evo['Profit'].cumsum()
                fig_evo = px.line(df_evo, x='Date', y='Acumulado', markers=True, title="Beneficio Realizado Acumulado (Ventas + Dividendos)")
                fig_evo.update_traces(line_color=COLOR_PROFIT, line_width=3)
                fig_evo.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_evo.update_layout(height=420)
                st.plotly_chart(fig_evo)

        st.markdown("---")
        if realized_por_activo:
            st.markdown("#### Beneficios Tomados por Activo (Realizado, neto de comisiones)")
            df_realized = pd.DataFrame(realized_por_activo, columns=['Activo', 'Realizado ($)'])
            if len(df_realized) > 24:
                df_realized = pd.concat([df_realized.head(12), df_realized.tail(12)])
                st.caption(f"Se muestran tus 12 mejores y 12 peores resultados de {len(realized_por_activo)} activos vendidos.")
            fig_realized = px.bar(df_realized, x='Activo', y='Realizado ($)', color='Realizado ($)', color_continuous_scale=[COLOR_LOSS, COLOR_PROFIT])
            fig_realized.update_layout(height=340, coloraxis_showscale=False, xaxis_title=None, margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig_realized)
    else:
        st.info("No tienes posiciones activas bajo estos parámetros.")

# ==========================================
# PESTAÑA 2: RENDIMIENTO VS MERCADO
# ==========================================
with tab2:
    st.subheader("📈 Crecimiento Patrimonial Real: Tu Cuenta vs S&P 500 (VOO)")
    st.caption("Tu línea es el valor diario real de la cuenta (posiciones a precio de mercado + efectivo). "
               "La de VOO invierte cada uno de tus aportes, el mismo día, en el ETF del S&P 500.")

    fig_patrimonio = go.Figure()
    fig_patrimonio.add_trace(go.Scatter(x=curva.index, y=curva['Valor'], name='Tu Cuenta (valor de mercado)', line=dict(color=COLOR_PROFIT, width=3)))
    if curva['VOO'].notna().any():
        fig_patrimonio.add_trace(go.Scatter(x=curva.index, y=curva['VOO'], name='Simulación: mismos aportes en VOO', line=dict(color=COLOR_SIMULATION, width=3)))
    fig_patrimonio.add_trace(go.Scatter(x=curva.index, y=curva['Aportes'], name='Línea de Capital Aportado', line=dict(color='gray', dash='dash')))
    fig_patrimonio.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), height=460)
    fig_patrimonio.update_yaxes(title_text="Valoración Total (USD)")
    st.plotly_chart(fig_patrimonio)

    m1, m2, m3, m4 = st.columns(4)
    twr_total = (idx_cuenta.iloc[-1] - 1) * 100
    m1.metric("Rentabilidad acumulada (TWR)", f"{twr_total:+.1f}%", f"VOO: {(idx_voo.iloc[-1] - 1) * 100:+.1f}%" if idx_voo is not None else None, delta_color="off",
              help="Time-Weighted Return: mide la calidad de la gestión sin importar cuándo depositaste.")
    m2.metric("Rentabilidad anual (TIR)", f"{tir_cuenta * 100:.1f}%" if tir_cuenta is not None else "N/D", f"VOO: {tir_voo * 100:.1f}%" if tir_voo is not None else None, delta_color="off")
    m3.metric("Caída máxima desde un pico", f"{dd_cuenta.min() * 100:.1f}%", f"VOO: {dd_voo.min() * 100:.1f}%" if dd_voo is not None else None, delta_color="off")
    m4.metric("Volatilidad anualizada", f"{vol_cuenta * 100:.1f}%" if pd.notna(vol_cuenta) else "N/D", f"VOO: {vol_voo * 100:.1f}%" if vol_voo is not None else None, delta_color="off")

    # INSIGHT DE BENCHMARKING
    if curva['VOO'].notna().any():
        valor_final_voo = curva['VOO'].iloc[-1]
        diff_voo = balance_total - valor_final_voo
        if diff_voo > 0:
            st.success(md_seguro(f"🏆 **Alpha Generado:** Con los mismos aportes, tu cuenta vale **${diff_voo:,.2f} USD** más que si todo hubiera ido a VOO "
                                 f"(${balance_total:,.2f} vs ${valor_final_voo:,.2f})."))
        else:
            st.warning(md_seguro(f"⚖️ **Comparativa de Rendimiento:** Con los mismos aportes, un enfoque pasivo en VOO valdría **${abs(diff_voo):,.2f} USD** más que tu cuenta hoy."))
        if vol_voo and pd.notna(vol_cuenta) and vol_cuenta > 1.5 * vol_voo:
            st.info(f"⚡ Tu cuenta fue **{vol_cuenta / vol_voo:.1f}× más volátil** que el S&P 500: el resultado vino con bastante más riesgo.")

    st.markdown("#### 🗓️ Resumen por Año")
    fmt_anual = {c: '${:,.2f}' for c in resumen_anual.columns}
    fmt_anual.update({'Tu rentabilidad': '{:+.1f}%', 'VOO (mismos aportes)': '{:+.1f}%'})
    st.dataframe(resumen_anual.style.format({k: v for k, v in fmt_anual.items() if k in resumen_anual.columns}).map(color_pl, subset=['Tu rentabilidad']))

    st.markdown("#### 🌊 Caídas desde Máximos (Drawdown)")
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(x=dd_cuenta.index, y=dd_cuenta * 100, name='Tu cuenta', fill='tozeroy', line=dict(color=COLOR_LOSS, width=1.5)))
    if dd_voo is not None:
        fig_dd.add_trace(go.Scatter(x=dd_voo.index, y=dd_voo * 100, name='VOO', line=dict(color=COLOR_SIMULATION, width=1.5)))
    fig_dd.update_layout(height=280, margin=dict(t=10, b=0, l=0, r=0), hovermode="x unified", legend=dict(orientation="h", y=1.12))
    fig_dd.update_yaxes(title_text="% bajo el máximo")
    st.plotly_chart(fig_dd)

    # --- ANÁLISIS DE RIESGO (BETA DE CARTERA) ---
    st.markdown("---")
    st.markdown("### ⚡ Análisis de Riesgo y Volatilidad (Beta de Cartera)")

    total_portfolio_value = valor_posiciones
    if total_portfolio_value > 0:
        weighted_beta = 0.0
        beta_records = []
        for d in holdings_data:
            sym = d['Activo']
            weight = d['Valor Total'] / total_portfolio_value
            # Beta propia (1 año de retornos diarios vs VOO); Yahoo a veces publica valores absurdos (p. ej. 14)
            b_val = beta_historica(closes[sym], serie_voo) if sym in closes.columns and not serie_voo.empty else None
            if b_val is None:
                b_val = min(max(fund_data.get(sym, {}).get('beta') or 1.0, -1.0), 4.0)
            weighted_beta += b_val * weight
            beta_records.append({
                "Activo": sym,
                "Beta": b_val,
                "Ponderación (%)": weight * 100,
                "Contribución al Riesgo": b_val * weight
            })

        c_b1, c_b2 = st.columns([1, 2], gap="medium")
        with c_b1:
            st.metric(label="Beta Ponderada de las Posiciones", value=f"{weighted_beta:.2f}",
                      delta=f"{weighted_beta * (1 - peso_caja / 100):.2f} incluyendo el efectivo", delta_color="off")

            # Risk profile interpretation
            riesgo_propio_alto = bool(vol_voo) and pd.notna(vol_cuenta) and vol_cuenta > 1.3 * vol_voo
            if weighted_beta < 0.85 and riesgo_propio_alto:
                st.warning(f"🎯 **Beta baja, pero riesgo alto**: tus posiciones se mueven poco con el S&P 500, aunque tu cuenta fue "
                           f"{vol_cuenta / vol_voo:.1f}× más volátil que el mercado. El riesgo viene de apuestas concretas (sectores o temas), no del mercado en general.")
            elif weighted_beta < 0.85:
                st.info("🛡️ **Perfil Defensivo**: Tu cartera es significativamente menos sensible a las fluctuaciones del mercado que el S&P 500. Ideal para mitigar caídas de mercado, pero capturará menos subidas.")
            elif 0.85 <= weighted_beta <= 1.15:
                st.success("⚖️ **Perfil de Mercado**: Tu cartera tiene una volatilidad similar a la del S&P 500. Se moverá en línea con el mercado general.")
            else:
                st.warning("🚀 **Perfil Agresivo (Alto Riesgo)**: Tu cartera es más volátil que el mercado general. Amplificará tanto las ganancias en mercados alcistas como las pérdidas en correcciones.")
            st.caption("Beta calculada con 1 año de retornos diarios contra VOO. Solo mide sensibilidad al S&P 500: "
                       "una cartera concentrada en pocos temas puede tener beta baja y aun así mucho riesgo propio.")

        with c_b2:
            df_betas = pd.DataFrame(beta_records).sort_values(by="Beta")
            fig_betas = px.bar(df_betas, x="Beta", y="Activo", orientation="h",
                               title="Coeficiente Beta por Activo",
                               color="Beta",
                               color_continuous_scale=[COLOR_CAPITAL, COLOR_SIMULATION, COLOR_LOSS])
            fig_betas.add_vline(x=1.0, line_dash="dash", line_color="gray", annotation_text="Mercado (1.0)")
            fig_betas.update_layout(height=280, margin=dict(t=30, b=0, l=0, r=0))
            st.plotly_chart(fig_betas)
    else:
        st.info("No hay activos suficientes para calcular el coeficiente Beta.")

# ==========================================
# PESTAÑA 3: DIAGNÓSTICO DE COMPORTAMIENTO
# ==========================================
with tab3:
    st.subheader("🧭 Diagnóstico: Cómo Inviertes")
    if df_trades.empty:
        st.info("Aún no hay operaciones para diagnosticar.")
    else:
        trades_12m = df_trades[df_trades['Date_Master'] > selected_date_dt - timedelta(days=365)]
        d1, d2, d3, d4, d5, d6 = st.columns(6)
        d1.metric("Operaciones totales", f"{len(df_trades)}", f"{len(trades_12m)} en 12 meses", delta_color="off")
        d2.metric("Promedio mensual (12m)", f"{len(trades_12m) / 12:.1f}")
        d3.metric("Monto mediano por operación", f"${df_trades['Monto'].median():,.0f}", f"{(df_trades['Monto'] < 50).mean() * 100:.0f}% son < $50", delta_color="off")
        d4.metric("Activos distintos operados", f"{df_trades['Symbol'].nunique()}", f"{len(simbolos_cartera)} en cartera hoy", delta_color="off")
        if ventas:
            ganadoras = df_ventas[df_ventas['Profit'] > 0]['Profit']
            perdedoras = df_ventas[df_ventas['Profit'] <= 0]['Profit']
            profit_factor = ganadoras.sum() / abs(perdedoras.sum()) if perdedoras.sum() < 0 else None
            d5.metric("Win Rate (ventas)", f"{win_rate:.0f}%", md_seguro(f"Gana prom. ${ganadoras.mean():,.0f} / pierde ${abs(perdedoras.mean()):,.0f}") if not perdedoras.empty and not ganadoras.empty else None, delta_color="off")
            d6.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor else "∞", help="Suma de ganancias ÷ suma de pérdidas en ventas. Mayor a 1.5 se considera sólido.")

        st.markdown("---")
        c_d1, c_d2 = st.columns(2, gap="medium")

        with c_d1:
            st.markdown("#### ⏱️ Resultado según cuánto tiempo mantuviste")
            if ventas:
                df_ventas['Plazo'] = pd.cut(df_ventas['Dias'], [-1, 7, 30, 90, 180, 365, 100000],
                                            labels=['≤ 1 semana', '1 sem – 1 mes', '1 – 3 meses', '3 – 6 meses', '6 – 12 meses', '> 1 año'])
                plazo = df_ventas.groupby('Plazo', observed=False).agg(Ventas=('Profit', 'size'), Invertido=('Costo', 'sum'), Resultado=('Profit', 'sum'))
                plazo['Rentabilidad (%)'] = (plazo['Resultado'] / plazo['Invertido'] * 100).where(plazo['Invertido'] > 0)
                fig_plazo = go.Figure(go.Bar(x=plazo.index.astype(str), y=plazo['Resultado'],
                                             marker_color=[COLOR_PROFIT if v >= 0 else COLOR_LOSS for v in plazo['Resultado']],
                                             text=[f"{v:+.1f}%" if pd.notna(v) else "" for v in plazo['Rentabilidad (%)']], textposition='outside'))
                fig_plazo.update_layout(height=320, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Resultado realizado (USD)")
                st.plotly_chart(fig_plazo)
                st.caption("Barras: ganancia neta realizada por plazo de tenencia (FIFO). Etiqueta: rentabilidad sobre lo invertido en esas ventas.")

        with c_d2:
            st.markdown("#### 🎯 ¿De dónde viene tu ganancia?")
            contrib = pd.Series({s: v for s, v in realized_por_activo})
            for d in holdings_data:
                contrib[d['Activo']] = contrib.get(d['Activo'], 0.0) + d['Flotante ($)']
            contrib['Dividendos e intereses'] = divs_brutos - divs_retenidos + total_intereses
            contrib['Prime'] = -total_prime_paid
            contrib = contrib.sort_values(ascending=False)
            if total_historico > 0 and not contrib.empty:
                top_sym, top_val = contrib.index[0], contrib.iloc[0]
                st.markdown(md_seguro(f"Tu ganancia neta es **${total_historico:,.2f}**. Solo **{top_sym}** aporta **${top_val:,.2f}** "
                                      f"(**{top_val / total_historico * 100:.0f}%**). Sin ese activo, el resto de tus decisiones suma **${total_historico - top_val:,.2f}**."))
            top_n = pd.concat([contrib.head(6), contrib.tail(4)]).drop_duplicates()
            fig_c = go.Figure(go.Bar(x=top_n.values, y=top_n.index, orientation='h',
                                     marker_color=[COLOR_PROFIT if v >= 0 else COLOR_LOSS for v in top_n.values]))
            fig_c.update_layout(height=320, margin=dict(t=10, b=0, l=0, r=0), yaxis=dict(autorange="reversed"), xaxis_title="USD (realizado + flotante)")
            st.plotly_chart(fig_c)

        st.markdown("---")
        c_d3, c_d4 = st.columns(2, gap="medium")
        with c_d3:
            st.markdown("#### 💰 Costos y Efectivo")
            neto_prime = ahorro_prime - total_prime_paid
            st.markdown(md_seguro(f"""
- Comisiones pagadas: **${total_fees_paid:,.2f}** · Prime: **${total_prime_paid:,.2f}** → **{costo_total_plataforma / total_historico * 100:.1f}%** de tu ganancia neta.
- Prime te ahorró ~**${ahorro_prime:,.2f}** en comisiones ({int(df_trades['Con_Prime'].sum())} operaciones × ${dif_comision_prime:.2f}). Balance de Prime: **{'+' if neto_prime >= 0 else '−'}${abs(neto_prime):,.2f}**.
- Retención de impuestos sobre dividendos: **${divs_retenidos:,.2f}** de ${divs_brutos:,.2f} brutos ({divs_retenidos / divs_brutos * 100 if divs_brutos else 0:.0f}%).
- Efectivo promedio en los últimos 12 meses: **${caja_promedio_12m:,.0f}**, con intereses cobrados por **${intereses_12m:,.2f}** (≈{rend_caja_12m:.2f}% anual).
""" if total_historico > 0 else f"Comisiones: ${total_fees_paid:,.2f} · Prime: ${total_prime_paid:,.2f}"))

        with c_d4:
            st.markdown("#### 🔁 Retrospectiva: ¿y si no hubieras vendido?")
            retro = []
            for v in ventas:
                s = v['Symbol']
                if s in closes.columns and not closes[s].dropna().empty:
                    hoy = v['Qty'] * factor_split_posterior(splits.get(s), v['Date']) * closes[s].dropna().iloc[-1]
                    retro.append({'Activo': s, 'Vendido por': v['Ingreso'], 'Valdría hoy': hoy})
            if retro:
                df_retro = pd.DataFrame(retro).groupby('Activo').sum()
                df_retro['Diferencia'] = df_retro['Valdría hoy'] - df_retro['Vendido por']
                df_retro = df_retro.sort_values('Diferencia', ascending=False)
                vista = pd.concat([df_retro.head(5), df_retro.tail(5)])
                vista = vista[~vista.index.duplicated()]
                st.dataframe(vista.style.format('${:,.2f}').map(color_pl, subset=['Diferencia']))
                st.caption(md_seguro(f"Positivo = la acción siguió subiendo después de venderla; negativo = vender te evitó una caída. "
                                     f"Neto de todas tus ventas: ${df_retro['Diferencia'].sum():,.2f}. Es una mirada en retrospectiva: el dinero de cada venta se usó en otras compras."))

        # INSIGHTS AUTOMÁTICOS
        st.markdown("---")
        st.markdown("### 🧠 Lectura Automática de tu Operativa")
        insights = []
        if ventas:
            corto = df_ventas[df_ventas['Dias'] <= 30]
            largo = df_ventas[df_ventas['Dias'] > 90]
            if not corto.empty and not largo.empty and corto['Costo'].sum() > 0 and largo['Costo'].sum() > 0:
                r_c = corto['Profit'].sum() / corto['Costo'].sum() * 100
                r_l = largo['Profit'].sum() / largo['Costo'].sum() * 100
                insights.append(f"Las ventas de posiciones que mantuviste **≤ 30 días** rindieron **{r_c:+.1f}%**; las de **más de 3 meses**, **{r_l:+.1f}%**.")
        if len(trades_12m) / 12 > 8:
            insights.append(f"Operas en promedio **{len(trades_12m) / 12:.0f} veces al mes**, con montos medianos de **${df_trades['Monto'].median():,.0f}**: mucha actividad para el tamaño de la cuenta.")
        if holdings_data:
            top3 = sum(d['Peso (%)'] for d in holdings_data[:3])
            insights.append(f"Tus 3 mayores posiciones ({', '.join(d['Activo'] for d in holdings_data[:3])}) suman **{top3:.0f}%** de la cuenta.")
        if vol_voo and pd.notna(vol_cuenta):
            insights.append(f"Volatilidad de tu cuenta: **{vol_cuenta * 100:.0f}%** anual vs **{vol_voo * 100:.0f}%** del S&P 500.")
        if total_prime_paid > ahorro_prime:
            insights.append(f"Prime costó **${total_prime_paid - ahorro_prime:,.2f}** más de lo que ahorró en comisiones (sin contar beneficios como precios en tiempo real).")
        for t in insights:
            st.markdown(md_seguro(f"- {t}"))

# ==========================================
# PESTAÑA 4: FLUJOS Y COSTOS
# ==========================================
with tab4:
    st.subheader("💸 Análisis Mensual: Rentabilidad, Ingresos Pasivos y Costos Operativos")

    if not df_prime.empty:
        df_prime['Mes_Año'] = df_prime['Date_Master'].dt.to_period('M').astype(str)
        prime_m = df_prime.groupby('Mes_Año')['Net Amount'].sum().abs().reset_index(name='Suscripción Prime ($)')
    else:
        prime_m = pd.DataFrame(columns=['Mes_Año', 'Suscripción Prime ($)'])

    if not df_trades.empty:
        df_trades['Mes_Año'] = df_trades['Date_Master'].dt.to_period('M').astype(str)
        fees_m = df_trades.groupby('Mes_Año')['Comision'].sum().reset_index(name='Comisiones Operativas ($)')
        trades_m = df_trades.groupby('Mes_Año').size().reset_index(name='Volumen de Trades')
    else:
        fees_m = pd.DataFrame(columns=['Mes_Año', 'Comisiones Operativas ($)'])
        trades_m = pd.DataFrame(columns=['Mes_Año', 'Volumen de Trades'])

    if ventas:
        pl_m = df_ventas.assign(Mes_Año=pd.to_datetime(df_ventas['Date']).dt.to_period('M').astype(str)).groupby('Mes_Año')['Profit'].sum().reset_index().rename(columns={'Profit': 'Rentabilidad de Ventas ($)'})
    else:
        pl_m = pd.DataFrame(columns=['Mes_Año', 'Rentabilidad de Ventas ($)'])

    if not df_divs.empty:
        df_divs['Mes_Año'] = df_divs['Date_Master'].dt.to_period('M').astype(str)
        div_m = df_divs.groupby('Mes_Año')['Net Amount'].sum().reset_index(name='Dividendos Netos ($)')
    else:
        div_m = pd.DataFrame(columns=['Mes_Año', 'Dividendos Netos ($)'])

    meses_unicos = sorted(df_filtered['Date_Master'].dt.to_period('M').unique().astype(str))
    df_costos = pd.DataFrame({'Mes_Año': meses_unicos})
    for df_temp in [prime_m, fees_m, pl_m, trades_m, div_m]:
        df_costos = df_costos.merge(df_temp, on='Mes_Año', how='left')
    df_costos = df_costos.fillna(0)

    fig_combo = make_subplots(specs=[[{"secondary_y": True}]])
    fig_combo.add_trace(go.Bar(x=df_costos['Mes_Año'], y=df_costos['Rentabilidad de Ventas ($)'], name='Rentabilidad de Ventas ($)', marker_color=COLOR_PROFIT), secondary_y=False)
    fig_combo.add_trace(go.Bar(x=df_costos['Mes_Año'], y=df_costos['Dividendos Netos ($)'], name='Dividendos Netos ($)', marker_color=COLOR_CAPITAL), secondary_y=False)
    fig_combo.add_trace(go.Bar(x=df_costos['Mes_Año'], y=df_costos['Comisiones Operativas ($)'], name='Comisiones Operativas ($)', marker_color=COLOR_LOSS), secondary_y=False)
    fig_combo.add_trace(go.Scatter(x=df_costos['Mes_Año'], y=df_costos['Volumen de Trades'], name='Volumen de Trades', mode='lines+markers', line=dict(color='white', width=3), marker=dict(size=8)), secondary_y=True)

    fig_combo.update_xaxes(type='category')
    for i, row in df_costos.iterrows():
        if row.get('Suscripción Prime ($)', 0) > 0:
            fig_combo.add_shape(type="rect", xref="x", yref="paper", x0=i - 0.45, y0=0, x1=i + 0.45, y1=1, fillcolor=COLOR_SIMULATION, opacity=0.12, layer="below", line_width=0)

    fig_combo.update_layout(
        hovermode="x unified",
        barmode='group',
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig_combo.update_yaxes(title_text="Capital en USD ($)", secondary_y=False)
    fig_combo.update_yaxes(title_text="N° de Operaciones Ejecutadas", secondary_y=True, showgrid=False)
    st.plotly_chart(fig_combo)
    st.caption("Las franjas naranjas marcan los meses en que pagaste Hapi Prime.")

    # --- INSIGHTS DE OPERATIVIDAD ---
    meses_con_prime = len(df_costos[df_costos['Suscripción Prime ($)'] > 0])
    meses_sin_prime = len(df_costos[df_costos['Suscripción Prime ($)'] == 0])
    total_divs = df_costos['Dividendos Netos ($)'].sum()

    st.markdown("### 🧠 Insights de Rentabilidad y Costos")
    col_ins1, col_ins2 = st.columns(2)

    with col_ins1:
        if total_divs > 0:
            st.success(md_seguro(f"💧 **Ingresos Pasivos:** Acumulas **${total_divs:,.2f} USD** netos en dividendos (se retuvieron ${divs_retenidos:,.2f} de impuestos) "
                                 f"y **${total_intereses:,.2f}** en intereses y préstamo de valores."))
        else:
            st.info("Aún no has generado flujo de caja pasivo por dividendos.")

    with col_ins2:
        if meses_con_prime > 0 and meses_sin_prime > 0:
            df_con = df_costos[df_costos['Suscripción Prime ($)'] > 0]
            df_sin = df_costos[df_costos['Suscripción Prime ($)'] == 0]
            st.warning(md_seguro(f"⭐ **Eficiencia de Suscripción:** Pagaste Prime en {meses_con_prime} meses (${total_prime_paid:,.2f}) y ahorraste ~${ahorro_prime:,.2f} en comisiones. "
                                 f"Con Prime promediaste **{df_con['Volumen de Trades'].mean():.1f} trades/mes**; sin Prime, {df_sin['Volumen de Trades'].mean():.1f}. "
                                 f"Para que Prime se pague solo con comisiones necesitarías ~**{9.99 / dif_comision_prime:.0f} operaciones al mes**."))
        else:
            st.info("No hay suficientes datos comparativos entre meses con y sin Hapi Prime.")

    st.markdown("#### 🏦 Aportes por Año")
    flujos_ext = df_filtered[df_filtered['Categoria'].isin(['DEPOSITO', 'RETIRO', 'CRIPTO'])].copy()
    if not flujos_ext.empty:
        flujos_ext['Año'] = flujos_ext['Date_Master'].dt.year
        tabla_aportes = flujos_ext.pivot_table(index='Año', columns='Categoria', values='Net Amount', aggfunc='sum', fill_value=0)
        tabla_aportes = tabla_aportes.rename(columns={'DEPOSITO': 'Depósitos', 'RETIRO': 'Retiros', 'CRIPTO': 'Traspasos cripto'})
        tabla_aportes['Neto'] = tabla_aportes.sum(axis=1)
        st.dataframe(tabla_aportes.style.format('${:,.2f}'))

# ==========================================
# PESTAÑA 5: RADAR DE OPORTUNIDADES
# ==========================================
with tab5:
    st.subheader("🔍 Análisis Estratégico de Activos (Cartera + Watchlist)")

    radar_data = []
    for s in simbolos_radar:
        p_actual = current_prices.get(s, 0)
        fund = fund_data.get(s, {})
        tech = tech_signals.get(s, {})

        # Cálculo de Decision Score (0-100): tendencia primero; un RSI bajo solo suma si no está en tendencia bajista
        score = 0
        rsi = tech.get('rsi', 50)
        tendencia = tech.get('trend', 'N/D')
        if tendencia == "Alcista": score += 35
        elif tendencia == "Lateral": score += 15
        if tendencia in ["Alcista", "Lateral"]:
            if rsi <= 45: score += 25
            elif rsi <= 70: score += 10

        upside = None
        target = fund.get('target_price')
        if target and p_actual:
            upside = ((target - p_actual) / p_actual) * 100
            if upside > 15: score += 25
            elif upside > 5: score += 12

        if fund.get('pe_ratio') and fund.get('pe_ratio') > 0: score += 15

        f_rep = fund.get('earnings_date')
        f_rep = pd.Timestamp(f_rep) if f_rep and pd.Timestamp(f_rep) >= selected_date_dt else None

        radar_data.append({
            "Ticker": s,
            "Nombre": fund.get('name') or s,
            "Precio": p_actual,
            "Día (%)": tech.get('dia'),
            "YTD (%)": tech.get('ytd'),
            "vs Máx 52s (%)": tech.get('dist_max52'),
            "RSI": rsi,
            "Tendencia": tech.get('trend', 'N/D'),
            "P/E": fund.get('pe_ratio'),
            "Div Yield": fund.get('dividend_yield'),
            "Target Analistas": target,
            "Upside Potencial": upside,
            "Próx. Resultados": f_rep.strftime('%d-%m-%Y') if f_rep is not None else '—',
            "Decision Score": score,
            "En Cartera": "✅" if portfolio.get(s, {}).get('qty', 0) > 0 else "👀"
        })

    df_radar = pd.DataFrame(radar_data).sort_values(by="Decision Score", ascending=False)
    for col in ["Precio", "Día (%)", "YTD (%)", "vs Máx 52s (%)", "P/E", "Div Yield", "Target Analistas", "Upside Potencial"]:
        df_radar[col] = pd.to_numeric(df_radar[col], errors='coerce')      # None -> NaN para que se muestre "—"

    def color_score(val):
        if val >= score_threshold: return f'background-color: {COLOR_PROFIT}; color: black; font-weight: bold'
        if val < 40: return f'color: {COLOR_LOSS}'
        return ''

    def color_upside(val):
        if val is None or pd.isna(val): return ''
        return f'color: {COLOR_PROFIT}' if val > 10 else f'color: {COLOR_LOSS}' if val < -5 else ''

    st.markdown(f"**Criterio de análisis:** activos con Score mayor a **{score_threshold}** merecen una revisión más profunda (no es una orden de compra).")

    styled_radar = df_radar.style.format({
        "Precio": "${:,.2f}",
        "Día (%)": "{:+.2f}%",
        "YTD (%)": "{:+.1f}%",
        "vs Máx 52s (%)": "{:+.1f}%",
        "P/E": "{:.2f}",
        "Div Yield": "{:.2f}%",
        "Target Analistas": "${:,.2f}",
        "Upside Potencial": "{:.2f}%",
        "Decision Score": "{:.0f}/100"
    }, na_rep='—').map(color_score, subset=['Decision Score']).map(color_upside, subset=['Upside Potencial']).map(color_pl, subset=['Día (%)', 'YTD (%)'])

    st.dataframe(styled_radar, hide_index=True, placeholder="—")

    st.markdown("""
    ### 💡 Guía de Interpretación
    *   **Decision Score:** Tendencia (alcista 35 / lateral 15 / bajista 0) + retroceso con RSI ≤ 45 dentro de una tendencia no bajista (25) + **potencial según analistas** (hasta 25) + empresa con utilidades, P/E positivo (15). Una acción en tendencia bajista no suma por estar "barata": así se evita perseguir caídas.
    *   **RSI:** Por debajo de 30 indica sobreventa y por encima de 70 sobrecompra. Por sí solo no es señal de compra o venta: en una tendencia bajista el RSI puede quedarse bajo durante meses.
    *   **vs Máx 52s:** Distancia al precio más alto del último año.
    *   **Upside Potencial:** Distancia porcentual entre el precio actual y el precio objetivo promedio de Wall Street.
    *   **P/E (Forward):** Si es significativamente menor que la media del sector, podría indicar que la empresa está infravalorada.
    """)
