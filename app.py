import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from datetime import datetime, timedelta
import os
import glob
import sys
import time
import json
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
    .block-container { padding-top: 2rem !important; padding-bottom: 1rem !important; }
    div[data-testid="metric-container"] {
        background-color: #1e1e1e; 
        border: 1px solid #333; 
        padding: 5% 5% 5% 10%; 
        border-radius: 12px; 
        color: white;
        transition: transform 0.2s ease-in-out, border-color 0.2s ease-in-out;
    }
    div[data-testid="metric-container"]:hover {
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
    </style>
""", unsafe_allow_html=True)

# --- COLORES GLOBALES ---
COLOR_PROFIT = '#00CC96'
COLOR_LOSS = '#FF4B4B'
COLOR_CAPITAL = '#0080FF'
COLOR_SIMULATION = '#FFA500'

# --- FUNCIÓN MATEMÁTICA PARA EL RSI ---
def compute_rsi(series, window=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=window - 1, adjust=False).mean()
    ema_down = down.ewm(com=window - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# --- 1. CARGA Y PROCESAMIENTO MULTI-ARCHIVO ---
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

# --- 2. CARGA Y PROCESAMIENTO MULTI-ARCHIVO ---
def load_data(uploaded_files=None):
    df_list = []
    
    # 1. Carga prioritaria si el usuario sube archivos
    if uploaded_files:
        for file in uploaded_files:
            try:
                src = io.BytesIO(file) if isinstance(file, (bytes, bytearray)) else file
                temp_df = pd.read_csv(src)                
                df_list.append(temp_df)
            except Exception as e:
                st.error(f"⚠️ No se pudo leer el archivo subido: {e}")
    # 2. Fallback a la carpeta local 'data'
    else:
        folder_path = os.path.join(get_base_path(), "data")
        if os.path.exists(folder_path):
            all_files = glob.glob(os.path.join(folder_path, "*.csv"))
            for file in all_files:
                try:
                    temp_df = pd.read_csv(file)
                    df_list.append(temp_df)
                except Exception as e:
                    st.warning(f"⚠️ No se pudo leer el archivo local {file}: {e}")
            
    if not df_list:
        return pd.DataFrame()
        
    df = pd.concat(df_list, ignore_index=True)
    df = df.drop_duplicates(ignore_index=True)
    
    cols_to_clean = ['Price', 'Fees', 'Commissions', 'Net Amount']
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            
    df['Trade Date'] = pd.to_datetime(df['Trade Date'], errors='coerce')
    df['Settle Date'] = pd.to_datetime(df.get('Settle Date'), errors='coerce')
    df['Date_Master'] = df['Trade Date'].combine_first(df['Settle Date'])
    if 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
        df['Date_Master'] = df['Date_Master'].combine_first(df['Timestamp'].dt.normalize())
    return df

# --- 3. GESTIÓN DE INTERFAZ DE CARGA (CSV Uploader) ---
if "csv_cache" not in st.session_state:
    st.session_state.csv_cache = {}      # nombre de archivo -> bytes, solo en memoria

def registrar_archivos(files):
    for f in files or []:
        st.session_state.csv_cache[f.name] = f.getvalue()

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

# Con archivos en caché usa esos; sin ellos cae a la carpeta data/ local
df = load_data(uploaded_files=list(st.session_state.csv_cache.values()))

if df.empty:
    st.markdown("<h2>📈 Terminal de Inversiones</h2>", unsafe_allow_html=True)
    st.info("👋 **¡Bienvenido al Terminal de Inversiones!**")
    st.markdown("""
        Cargá tus archivos CSV de transacciones de Hapi para comenzar el análisis.
        Se procesan solo durante esta sesión: al cerrar la pestaña no queda nada guardado.
    """)

    antes = len(st.session_state.csv_cache)
    registrar_archivos(st.file_uploader(
        "Arrastra y suelta tus archivos CSV aquí:",
        type=["csv"], accept_multiple_files=True, key="main_uploader"))
    if len(st.session_state.csv_cache) > antes:
        st.rerun()      # solo si entraron archivos nuevos: corta el bucle
    st.stop()
    
# --- 4. CONFIGURACIÓN INTERNA DE ANÁLISIS ---
watchlist = ["AAPL", "TSLA", "NVDA", "BTC-USD", "VOO", "MSFT", "GOOGL"]
score_threshold = 65

# --- 5. CABECERA Y FILTROS ---
c_title, c_f1 = st.columns([1.5, 1], gap="medium")
with c_title:
    st.markdown("<h2 style='margin-top: -15px;'>📈 Terminal de Inversiones</h2>", unsafe_allow_html=True)
    ultima_fecha_csv = df['Date_Master'].max().strftime('%d-%m-%Y')
    st.markdown(f"<p style='color: #888888; font-size: 0.85em; margin-top: -15px;'>Última sincronización de base de datos: <b>{ultima_fecha_csv}</b></p>", unsafe_allow_html=True)

min_date = df['Date_Master'].min().date()
max_date = datetime.today().date()
selected_date = c_f1.date_input("Fecha de Análisis de Corte:", value=max_date, min_value=min_date, max_value=max_date)
selected_date_dt = pd.to_datetime(selected_date)
df_filtered = df[(df['Date_Master'] <= selected_date_dt)].copy()

# --- 3. MOTOR FINANCIERO CORE ---
df_prime = df_filtered[df_filtered['Description'].astype(str).str.contains('PRIME', case=False, na=False)].copy()
total_prime_paid = abs(df_prime['Net Amount'].sum()) if not df_prime.empty else 0.0

mask_deposits = (df_filtered['Type'] == 'MONEY_MOVEMENTS') & (df_filtered['Description'].astype(str).str.contains('Journal', case=False, na=False))
total_depositos = df_filtered[mask_deposits]['Net Amount'].sum()

mask_other_cash = (~df_filtered['Description'].astype(str).str.contains('Journal', case=False, na=False)) & (~df_filtered['Symbol'].astype(str).str.contains('ISPAZ', case=False, na=False))
poder_compra = total_depositos + df_filtered[mask_other_cash]['Net Amount'].sum()

# DIVIDENDOS
tipo_str = df_filtered['Type'].astype(str).str.strip().str.upper()
sym_str = df_filtered['Symbol'].astype(str).str.strip().str.upper()
mask_divs = (tipo_str == 'MONEY_MOVEMENTS') & (df_filtered['Symbol'].notna()) & (sym_str != '') & (sym_str != 'NAN') & (sym_str != 'ISPAZ')
df_divs = df_filtered[mask_divs].copy()
df_divs = df_divs[~df_divs['Description'].astype(str).str.upper().str.contains('PRIME')]

# PORTAFOLIO Y TRADES
mask_adjustments = df_filtered['Type'].astype(str).str.strip().str.upper() == 'POSITION_ADJUSTMENTS'
df_trades_process = df_filtered[(df_filtered['Type'] == 'TRADES') | (mask_divs) | (mask_adjustments)].sort_values(['Timestamp', 'Date_Master'])

portfolio = {}
realized_history = [] 
total_fees_paid = df_filtered[df_filtered['Type'] == 'TRADES']['Fees'].sum() + df_filtered[df_filtered['Type'] == 'TRADES']['Commissions'].sum()

for index, row in df_trades_process.iterrows():
    sym = str(row['Symbol']).upper().strip()
    if pd.isna(sym) or sym in ['NAN', 'ISPAZ']: continue
    
    action, desc = str(row['Trade Action']).upper().strip(), str(row['Description']).upper().strip()
    qty = abs(float(row['Qty'])) if pd.notna(row['Qty']) else 0
    price, net_amt, date_trade = float(row['Price']) if pd.notna(row['Price']) else 0, row['Net Amount'], row['Date_Master']
    
    if sym not in portfolio: portfolio[sym] = {'qty': 0.0, 'total_cost': 0.0, 'avg_cost': 0.0, 'realized_pl': 0.0}

    # AJUSTES DE POSICION (splits, splits inversos, spin-offs):
    # cambian el numero de acciones sin mover el costo base. Se recalcula el precio promedio.
    if str(row['Type']).strip().upper() == 'POSITION_ADJUSTMENTS':
        raw_qty = float(row['Qty']) if pd.notna(row['Qty']) else 0.0
        direccion = str(row.get('Transfer Direction', '')).strip().upper()
        delta = -abs(raw_qty) if direccion == 'OUTGOING' else abs(raw_qty)
        portfolio[sym]['qty'] += delta
        if portfolio[sym]['qty'] > 0.0000001:
            portfolio[sym]['avg_cost'] = portfolio[sym]['total_cost'] / portfolio[sym]['qty']
        else:
            portfolio[sym].update({'qty': 0.0, 'total_cost': 0.0, 'avg_cost': 0.0})
        continue
    
    es_compra = action in ['BUY', 'DIVIDEND REINVEST', 'REINVESTMENT']
    es_venta = ('SELL' in action) or ('LIQ' in action) or ('LIQ' in desc) or ('CASH IN LIEU' in desc)
    es_cobro_div = (row['Type'] == 'MONEY_MOVEMENTS') and not es_compra and not es_venta

    if es_compra:
        portfolio[sym]['total_cost'] += (qty * price)
        portfolio[sym]['qty'] += qty
        if portfolio[sym]['qty'] > 0: portfolio[sym]['avg_cost'] = portfolio[sym]['total_cost'] / portfolio[sym]['qty']
    elif es_venta:
        if portfolio[sym]['qty'] > 0:
            pl = (price - portfolio[sym]['avg_cost']) * qty
            portfolio[sym]['realized_pl'] += pl
            realized_history.append({'Date': date_trade, 'Symbol': sym, 'Profit': pl, 'Type': 'Venta', 'Sell Price': price, 'Qty': qty})
            portfolio[sym]['qty'] -= qty
            portfolio[sym]['total_cost'] -= (portfolio[sym]['avg_cost'] * qty)
            if portfolio[sym]['qty'] < 0.01: portfolio[sym].update({'qty': 0.0, 'total_cost': 0.0})
    elif es_cobro_div:
        realized_history.append({'Date': date_trade, 'Symbol': sym, 'Profit': net_amt, 'Type': 'Dividendo'})

# MERCADO E INDICADORES TÉCNICOS
# --- 6. GESTIÓN DE CACHÉ Y DESCARGA CONCURRENTE ---
def load_local_cache():
    cache_path = get_cache_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_updated_prices": 0.0, "last_updated_fundamentals": 0.0, "historical_prices": {}, "fundamentals": {}}

def save_local_cache(cache_data):
    cache_path = get_cache_path()
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
                
        return s, {
            'pe_ratio': info.get('forwardPE') or info.get('trailingPE'),
            'target_price': info.get('targetMeanPrice'),
            'dividend_yield': (info.get('dividendYield') or 0) * 100,
            'name': info.get('longName', s),
            'sector': info.get('sector', 'N/A'),
            'quote_type': info.get('quoteType', 'EQUITY'),
            'beta': beta_val
        }
    except Exception:
        return s, {'pe_ratio': None, 'target_price': None, 'dividend_yield': 0, 'name': s, 'sector': 'N/A', 'quote_type': 'EQUITY', 'beta': 1.0}

def get_fundamental_data_cached(symbols, cache_data, force_refresh=False):
    now = time.time()
    needs_update = []
    
    # Expiración de fundamentos: 7 días
    is_expired = (now - cache_data.get("last_updated_fundamentals", 0.0)) > 604800
    
    for s in symbols:
        if force_refresh or is_expired or s not in cache_data["fundamentals"]:
            needs_update.append(s)
            
    if needs_update:
        with ThreadPoolExecutor(max_workers=min(10, len(needs_update))) as executor:
            results = executor.map(fetch_single_fundamental, needs_update)
            for s, info in results:
                cache_data["fundamentals"][s] = info
        cache_data["last_updated_fundamentals"] = now
        save_local_cache(cache_data)
        
    return {s: cache_data["fundamentals"].get(s, {'pe_ratio': None, 'target_price': None, 'dividend_yield': 0, 'name': s, 'sector': 'N/A', 'quote_type': 'EQUITY', 'beta': 1.0}) for s in symbols}

def get_bulk_market_data_cached(symbols, start_date, end_date, cache_data, force_refresh=False):
    now = time.time()
    # Expiración de precios del día: 4 horas
    is_fresh = (now - cache_data.get("last_updated_prices", 0.0)) < 14400
    all_present = all(s in cache_data["historical_prices"] for s in symbols)
    
    if is_fresh and all_present and not force_refresh:
        reconstructed = {}
        for s in symbols:
            if s in cache_data["historical_prices"]:
                data = cache_data["historical_prices"][s]
                dates = pd.to_datetime(data["dates"])
                prices = data["prices"]
                reconstructed[s.replace('.', '-')] = pd.Series(prices, index=dates)
        if reconstructed:
            return pd.DataFrame(reconstructed)
            
    if not symbols:
        return pd.DataFrame()
        
    yf_symbols = list(set([s.replace('.', '-') for s in symbols]))
    try:
        df = yf.download(yf_symbols, start=start_date, end=end_date, threads=True, progress=False)
        if not df.empty and 'Close' in df:
            close_df = df['Close']
            if isinstance(close_df, pd.Series) and len(yf_symbols) == 1:
                close_df = close_df.to_frame(name=yf_symbols[0])
                
            for s in symbols:
                s_yf = s.replace('.', '-')
                if s_yf in close_df:
                    series = close_df[s_yf].dropna()
                    cache_data["historical_prices"][s] = {
                        "dates": [d.strftime('%Y-%m-%d') for d in series.index],
                        "prices": series.tolist()
                    }
            cache_data["last_updated_prices"] = now
            save_local_cache(cache_data)
            return close_df
    except Exception:
        pass
        
    # Fallback a caché si falla la red
    reconstructed = {}
    for s in symbols:
        if s in cache_data["historical_prices"]:
            data = cache_data["historical_prices"][s]
            dates = pd.to_datetime(data["dates"])
            prices = data["prices"]
            reconstructed[s.replace('.', '-')] = pd.Series(prices, index=dates)
    return pd.DataFrame(reconstructed)

# --- 7. EJECUCIÓN DEL MOTOR DE MERCADO ---
current_prices, tech_signals, fund_data = {}, {}, {}
all_symbols = list(set([s for s, d in portfolio.items() if d['qty'] > 0] + [x['Symbol'] for x in realized_history if x['Type'] == 'Venta'] + watchlist))

if all_symbols:
    with st.spinner("Sincronizando análisis avanzado (Técnico + Fundamental)..."):
        cache_data = load_local_cache()
        force_ref = st.session_state.get('force_refresh', False)
        
        hist_closes = get_bulk_market_data_cached(
            all_symbols, 
            selected_date_dt - timedelta(days=100), 
            selected_date_dt + timedelta(days=1),
            cache_data,
            force_refresh=force_ref
        )
        fund_data = get_fundamental_data_cached(all_symbols, cache_data, force_refresh=force_ref)
        
        # Reset force refresh flag
        st.session_state.force_refresh = False
        
        for s in set(all_symbols):
            try:
                s_yf = s.replace('.', '-')
                if s_yf in hist_closes and not hist_closes[s_yf].dropna().empty:
                    series = hist_closes[s_yf].dropna()
                    current_prices[s] = series.iloc[-1]
                    
                    # RSI
                    rsi_series = compute_rsi(series)
                    rsi = rsi_series.iloc[-1] if not rsi_series.empty else 50
                    
                    # Medias Móviles (SMA)
                    sma_50 = series.rolling(window=50).mean().iloc[-1] if len(series) >= 50 else None
                    sma_200 = series.rolling(window=200).mean().iloc[-1] if len(series) >= 200 else sma_50
                    
                    trend = "Bullish" if sma_50 and sma_200 and sma_50 > sma_200 else "Bearish"
                    signal = "🟢 Comprar" if rsi < 35 else "🔴 Vender" if rsi > 70 else "🟡 Mantener"
                    tech_signals[s] = {
                        'rsi': int(rsi),
                        'signal': signal,
                        'trend': trend,
                        'sma_50': sma_50,
                        'sma_200': sma_200
                    }
                else: 
                    current_prices[s] = portfolio.get(s, {}).get('avg_cost', 0.0)
            except:
                current_prices[s] = portfolio.get(s, {}).get('avg_cost', 0.0)

# CONSTRUCCIÓN DE DATA FINAL
holdings_data = []
for sym, data in portfolio.items():
    if data['qty'] > 0:
        precio_actual = current_prices.get(sym, data['avg_cost'])
        unrealized_pl = (precio_actual - data['avg_cost']) * data['qty']
        pl_percent = (unrealized_pl / data['total_cost']) * 100 if data['total_cost'] > 0 else 0
        holdings_data.append({
            "Activo": sym,
            "Acciones": data['qty'],
            "P. Promedio": data['avg_cost'],
            "P. Actual": precio_actual,
            "Valor Total": data['qty'] * precio_actual,
            "Flotante ($)": unrealized_pl,
            "Flotante (%)": pl_percent,
            "Semáforo Técnico": tech_signals.get(sym, "Desconocido")
        })

balance_total = poder_compra + sum([d['Valor Total'] for d in holdings_data])
total_realized = sum([x['Profit'] for x in realized_history])
total_unrealized = sum([(current_prices.get(s, d['avg_cost']) - d['avg_cost']) * d['qty'] for s, d in portfolio.items() if d['qty']>0])
costo_total_plataforma = total_fees_paid + total_prime_paid
total_historico = total_realized + total_unrealized - costo_total_plataforma

# --- 4. KPIs PRINCIPALES ---
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Balance Patrimonial", f"${balance_total:,.2f}")
k2.metric("Capital Fondeado (Aportes)", f"${total_depositos:,.2f}")
k3.metric("Beneficio Neto Global", f"${total_historico:,.2f}", f"{'🟢' if total_historico >= 0 else '🔴'} Neto (Post-Costos)")
k4.metric("Poder de Compra (Liquidez)", f"${poder_compra:,.2f}")
k5.metric("Costos de Plataforma", f"${costo_total_plataforma:,.2f}", delta="Comisiones + Prime", delta_color="inverse")

st.markdown("---")

# --- 5. PESTAÑAS MAESTRAS ---
tab1, tab2, tab3, tab4 = st.tabs([
    "💼 Portafolio y Rendimiento", 
    "💸 Operatividad y Flujos de Caja", 
    "🔮 Mercado y Simulaciones",
    "🔍 Radar de Oportunidades"
])

# ==========================================
# PESTAÑA 1: PORTAFOLIO Y RENDIMIENTO
# ==========================================
with tab1:
    ventas = [x for x in realized_history if x['Type'] == 'Venta']
    if ventas:
        win_rate = (len([x for x in ventas if x['Profit'] > 0]) / len(ventas)) * 100
        st.markdown(f"🎯 **Precisión Estratégica (Win Rate):** El **{win_rate:.1f}%** de tus posiciones cerradas han sido rentables.")

    col_t, col_p = st.columns([1.8, 1])
    if holdings_data:
        df_h = pd.DataFrame(holdings_data)
        
        format_dict = {'Acciones': '{:.4f}', 'P. Promedio': '${:,.2f}', 'P. Actual': '${:,.2f}', 'Valor Total': '${:,.2f}', 'Flotante ($)': '${:,.2f}', 'Flotante (%)': '{:.2f}%'}
        def color_pl(val): return f'color: {COLOR_PROFIT}' if val > 0 else f'color: {COLOR_LOSS}' if val < 0 else 'color: gray'
        def color_sig(val): return f'color: {COLOR_PROFIT}' if 'Compra' in str(val) else f'color: {COLOR_LOSS}' if 'Vender' in str(val) else f'color: {COLOR_SIMULATION}'
        
        col_t.subheader("Estado Actual de Posiciones y Señales Algorítmicas")
        def color_pl(val): return f'color: {COLOR_PROFIT}' if val > 0 else f'color: {COLOR_LOSS}' if val < 0 else 'color: gray'
        def color_sig(val): 
            v_str = str(val)
            return f'color: {COLOR_PROFIT}' if '🟢' in v_str else f'color: {COLOR_LOSS}' if '🔴' in v_str else f'color: {COLOR_SIMULATION}'
        
        # Adaptación del tech_signal que ahora es un dict
        df_h['Semáforo Técnico'] = df_h['Activo'].apply(lambda x: f"{tech_signals.get(x, {}).get('rsi', 'N/A')} | {tech_signals.get(x, {}).get('signal', 'Desconocido')}")
        
        styled_df = df_h.style.format(format_dict).map(color_pl, subset=['Flotante ($)', 'Flotante (%)']).map(color_sig, subset=['Semáforo Técnico'])
        col_t.dataframe(styled_df, use_container_width=True, height=350)
        
        with col_p:
            st.subheader("Distribución de Cartera")
            
            # Selector de tipo de distribución
            alloc_type = st.segmented_control(
                "Agrupar por:",
                options=["Activo", "Clase de Activo", "Sector"],
                default="Activo"
            ) if hasattr(st, 'segmented_control') else st.selectbox(
                "Agrupar por:",
                ["Activo", "Clase de Activo", "Sector"]
            )
            
            # Helper para obtener clase de activo
            def get_asset_class(sym, q_type):
                sym_upper = sym.upper()
                if q_type == 'CRYPTOCURRENCY' or '-USD' in sym_upper:
                    return 'Criptomonedas'
                elif q_type == 'ETF':
                    if sym_upper in ['SLV', 'GLD', 'IAU', 'USO']:
                        return 'Commodities'
                    return 'ETFs'
                else:
                    return 'Acciones'
            
            if alloc_type == "Activo":
                fig_pie = px.pie(df_h, values='Valor Total', names='Activo', hole=0.5)
                fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='#1e1e1e', width=2)))
                fig_pie.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_pie, use_container_width=True)
                
            elif alloc_type == "Clase de Activo":
                df_h['Clase de Activo'] = df_h['Activo'].apply(lambda x: get_asset_class(x, fund_data.get(x, {}).get('quote_type', 'EQUITY')))
                df_class = df_h.groupby('Clase de Activo')['Valor Total'].sum().reset_index()
                fig_pie = px.pie(df_class, values='Valor Total', names='Clase de Activo', hole=0.5,
                                 color_discrete_sequence=[COLOR_CAPITAL, COLOR_PROFIT, COLOR_SIMULATION, COLOR_LOSS])
                fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='#1e1e1e', width=2)))
                fig_pie.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.1), margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_pie, use_container_width=True)
                
            elif alloc_type == "Sector":
                def get_sector_grouped(x):
                    clase = get_asset_class(x, fund_data.get(x, {}).get('quote_type', 'EQUITY'))
                    if clase == 'ETFs': return 'ETFs (Sin Sector)'
                    if clase == 'Criptomonedas': return 'Criptomonedas'
                    if clase == 'Commodities': return 'Commodities'
                    sec = fund_data.get(x, {}).get('sector', 'Otros/Desconocido')
                    return sec if sec and sec != 'N/A' else 'Otros/Desconocido'
                    
                df_h['Sector'] = df_h['Activo'].apply(get_sector_grouped)
                df_sector = df_h.groupby('Sector')['Valor Total'].sum().reset_index()
                fig_pie = px.pie(df_sector, values='Valor Total', names='Sector', hole=0.5)
                fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='#1e1e1e', width=2)))
                fig_pie.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.1), margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_pie, use_container_width=True)
            
        st.markdown("---")
        st.subheader("Análisis de Desempeño por Activo e Histórico")
        
        col_r1, col_r2, col_r3 = st.columns([1, 1, 1.5])
        
        with col_r1:
            df_holdings_sorted = df_h.sort_values(by='Flotante ($)', ascending=True)
            fig_unrealized = px.bar(df_holdings_sorted, x='Flotante ($)', y='Activo', orientation='h', color='Flotante ($)', color_continuous_scale=[COLOR_LOSS, COLOR_PROFIT], title="Beneficio Flotante (No Realizado)")
            st.plotly_chart(fig_unrealized, use_container_width=True)
            
        with col_r2:
            realized_data = [{"Activo": sym, "Realizado ($)": data['realized_pl']} for sym, data in portfolio.items() if data['realized_pl'] != 0]
            if realized_data:
                df_realized = pd.DataFrame(realized_data).sort_values(by='Realizado ($)', ascending=True)
                fig_realized = px.bar(df_realized, x='Realizado ($)', y='Activo', orientation='h', color='Realizado ($)', color_continuous_scale=[COLOR_LOSS, COLOR_PROFIT], title="Beneficios Tomados (Realizado)")
                st.plotly_chart(fig_realized, use_container_width=True)
            else:
                st.info("Sin ventas cerradas para evaluar.")
                
        with col_r3:
            if realized_history:
                df_evo = pd.DataFrame(realized_history).sort_values('Date')
                df_evo['Acumulado'] = df_evo['Profit'].cumsum()
                fig_evo = px.line(df_evo, x='Date', y='Acumulado', markers=True, title="Evolución del Beneficio Neto (Ventas y Dividendos)")
                fig_evo.update_traces(line_color=COLOR_PROFIT, line_width=3)
                fig_evo.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_evo, use_container_width=True)
    else:
        st.info("No tienes posiciones activas bajo estos parámetros.")

# ==========================================
# PESTAÑA 2: OPERATIVIDAD Y FLUJOS DE CAJA
# ==========================================
with tab2:
    st.subheader("💸 Análisis Mensual: Rentabilidad, Ingresos Pasivos y Costos Operativos")
    
    if not df_prime.empty:
        df_prime['Mes_Año'] = df_prime['Date_Master'].dt.to_period('M').astype(str)
        prime_m = df_prime.groupby('Mes_Año')['Net Amount'].sum().abs().reset_index(name='Suscripción Prime ($)')
    else:
        prime_m = pd.DataFrame(columns=['Mes_Año', 'Suscripción Prime ($)'])

    df_fees = df_filtered[(df_filtered['Fees'] > 0) | (df_filtered['Commissions'] > 0)].copy()
    if not df_fees.empty:
        df_fees['Mes_Año'] = df_fees['Date_Master'].dt.to_period('M').astype(str)
        df_fees['Total_Fees'] = df_fees['Fees'] + df_fees['Commissions']
        fees_m = df_fees.groupby('Mes_Año')['Total_Fees'].sum().reset_index(name='Comisiones Operativas ($)')
    else:
        fees_m = pd.DataFrame(columns=['Mes_Año', 'Comisiones Operativas ($)'])

    df_trades_all = df_filtered[df_filtered['Type'] == 'TRADES'].copy()
    if not df_trades_all.empty:
        df_trades_all['Mes_Año'] = df_trades_all['Date_Master'].dt.to_period('M').astype(str)
        trades_m = df_trades_all.groupby('Mes_Año').size().reset_index(name='Volumen de Trades')
    else:
        trades_m = pd.DataFrame(columns=['Mes_Año', 'Volumen de Trades'])

    pl_m = pd.DataFrame(realized_history)
    if not pl_m.empty:
        pl_m['Mes_Año'] = pl_m['Date'].dt.to_period('M').astype(str)
        pl_m = pl_m[pl_m['Type'] == 'Venta'].groupby('Mes_Año')['Profit'].sum().reset_index().rename(columns={'Profit': 'Rentabilidad de Ventas ($)'})
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
        df_costos = df_costos.merge(df_temp, on='Mes_Año', how='left').fillna(0)

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
    st.plotly_chart(fig_combo, use_container_width=True)

    # --- INSIGHTS DE OPERATIVIDAD ---
    meses_con_prime = len(df_costos[df_costos['Suscripción Prime ($)'] > 0])
    meses_sin_prime = len(df_costos[df_costos['Suscripción Prime ($)'] == 0])
    total_divs = df_costos['Dividendos Netos ($)'].sum()
    
    st.markdown("### 🧠 Insights de Rentabilidad y Costos")
    col_ins1, col_ins2 = st.columns(2)
    
    with col_ins1:
        if total_divs > 0:
            st.success(f"💧 **Ingresos Pasivos:** Has construido un flujo de caja que ya acumula **${total_divs:,.2f} USD** puramente en dividendos (ya descontando la retención de impuestos WHT-TAX).")
        else:
            st.info("Aún no has generado flujo de caja pasivo por dividendos.")

    with col_ins2:
        if meses_con_prime > 0 and meses_sin_prime > 0:
            df_con = df_costos[df_costos['Suscripción Prime ($)'] > 0]
            df_sin = df_costos[df_costos['Suscripción Prime ($)'] == 0]
            st.warning(f"⭐ **Eficiencia de Suscripción:** Pagaste Prime en {meses_con_prime} meses. En ellos, promediaste **{df_con['Volumen de Trades'].mean():.1f} trades/mes** y ganaste **${df_con['Rentabilidad de Ventas ($)'].mean():.2f}/mes**. Sin Prime, promediaste {df_sin['Volumen de Trades'].mean():.1f} trades y ganaste ${df_sin['Rentabilidad de Ventas ($)'].mean():.2f}/mes.")
        else:
            st.info("No hay suficientes datos comparativos entre meses con y sin Hapi Prime.")

# ==========================================
# PESTAÑA 3: MERCADO Y SIMULACIONES
# ==========================================
with tab3:
    st.subheader("📈 Crecimiento Patrimonial: Gestión Activa vs S&P 500 (VOO)")
    
    df_deposits_bench = df_filtered[(df_filtered['Type'] == 'MONEY_MOVEMENTS') & (df_filtered['Description'].astype(str).str.contains('Journal', case=False, na=False))].copy()
    
    if not df_deposits_bench.empty:
        try:
            voo_data = yf.Ticker("VOO").history(start=min_date, end=selected_date_dt + timedelta(days=1))
            voo_data.index = pd.to_datetime(voo_data.index).tz_localize(None).normalize()
            
            deposits_daily = df_deposits_bench.groupby(pd.to_datetime(df_deposits_bench['Date_Master']).dt.normalize())['Net Amount'].sum()
            voo_shares, capital_aportado, fechas, cap_list, voo_list = 0, 0, [], [], []
            
            dep_dates = sorted(deposits_daily.index)
            dep_idx = 0
            
            for date, row in voo_data.iterrows():
                date_ts = pd.Timestamp(date)
                
                daily_investment = 0
                while dep_idx < len(dep_dates) and dep_dates[dep_idx] <= date_ts:
                    daily_investment += deposits_daily[dep_dates[dep_idx]]
                    dep_idx += 1
                    
                if daily_investment != 0:
                    capital_aportado += daily_investment
                    voo_shares += daily_investment / row['Close']
                    
                fechas.append(date)
                cap_list.append(capital_aportado)
                voo_list.append(voo_shares * row['Close'])
                
            if dep_idx < len(dep_dates):
                remaining_dep = sum([deposits_daily[dep_dates[i]] for i in range(dep_idx, len(dep_dates))])
                capital_aportado += remaining_dep
                last_close = voo_data['Close'].iloc[-1] if not voo_data.empty else 1.0
                voo_shares += remaining_dep / last_close
                if len(fechas) > 0 and dep_dates[-1] > fechas[-1]:
                    fechas.append(dep_dates[-1])
                    cap_list.append(capital_aportado)
                    voo_list.append(voo_shares * last_close)
                
            df_bench = pd.DataFrame({'Fecha': fechas, 'Capital Fondeado': cap_list, 'Simulación VOO': voo_list})
            
            if realized_history:
                df_evo = pd.DataFrame(realized_history)
                df_evo['Date_Norm'] = pd.to_datetime(df_evo['Date']).dt.normalize()
                daily_profit = df_evo.groupby('Date_Norm')['Profit'].sum().reset_index()
                daily_profit['Acumulado'] = daily_profit['Profit'].cumsum()
                
                df_bench['Date_Norm'] = pd.to_datetime(df_bench['Fecha']).dt.normalize()
                df_bench = df_bench.merge(daily_profit[['Date_Norm', 'Acumulado']], on='Date_Norm', how='left')
                df_bench['Tu_Beneficio_Realizado'] = df_bench['Acumulado'].ffill().fillna(0)
            else:
                df_bench['Tu_Beneficio_Realizado'] = 0

            df_bench['Tu_Patrimonio_Realizado'] = df_bench['Capital Fondeado'] + df_bench['Tu_Beneficio_Realizado']

            fig_patrimonio = go.Figure()
            fig_patrimonio.add_trace(go.Scatter(x=df_bench['Fecha'], y=df_bench['Tu_Patrimonio_Realizado'], name='Tu Patrimonio (Base + Realizado)', line=dict(color=COLOR_PROFIT, width=3)))
            if not df_bench.empty:
                last_date = df_bench['Fecha'].iloc[-1]
                last_val = df_bench['Tu_Patrimonio_Realizado'].iloc[-1]
                fig_patrimonio.add_trace(go.Scatter(x=[last_date, max_date], y=[last_val, balance_total], mode='lines', name='Brecha de Flotante', line=dict(color=COLOR_PROFIT, dash='dot'), showlegend=False))
            fig_patrimonio.add_trace(go.Scatter(x=df_bench['Fecha'], y=df_bench['Simulación VOO'], name='Simulación Inversión VOO', line=dict(color=COLOR_SIMULATION, width=3)))
            fig_patrimonio.add_trace(go.Scatter(x=df_bench['Fecha'], y=df_bench['Capital Fondeado'], name='Línea de Capital Base', line=dict(color='gray', dash='dash')))
            fig_patrimonio.add_trace(go.Scatter(x=[max_date], y=[balance_total], mode='markers+text', name='Valoración Total Actual (Incluye Flotante)', marker=dict(color=COLOR_PROFIT, size=12, symbol='star'), text=["Balance Actual"], textposition="top left"))
            
            fig_patrimonio.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            fig_patrimonio.update_yaxes(title_text="Valoración Total (USD)")
            st.plotly_chart(fig_patrimonio, use_container_width=True)

            # INSIGHT DE BENCHMARKING
            valor_final_voo = df_bench['Simulación VOO'].iloc[-1]
            diff_voo = balance_total - valor_final_voo
            if diff_voo > 0:
                st.success(f"🏆 **Alpha Generado:** Tu gestión activa está superando al mercado (S&P 500) por una diferencia a favor de **${diff_voo:,.2f} USD**.")
            else:
                st.warning(f"⚖️ **Comparativa de Rendimiento:** Actualmente, un enfoque pasivo en el ETF VOO habría generado **${abs(diff_voo):,.2f} USD** adicionales frente a tu balance actual.")

            # --- ANÁLISIS DE RIESGO (BETA DE CARTERA) ---
            st.markdown("---")
            st.markdown("### ⚡ Análisis de Riesgo y Volatilidad (Beta de Cartera)")
            
            total_portfolio_value = sum([d['Valor Total'] for d in holdings_data])
            if total_portfolio_value > 0:
                weighted_beta = 0.0
                beta_records = []
                for d in holdings_data:
                    sym = d['Activo']
                    val = d['Valor Total']
                    weight = val / total_portfolio_value
                    b_val = fund_data.get(sym, {}).get('beta', 1.0)
                    weighted_beta += b_val * weight
                    beta_records.append({
                        "Activo": sym,
                        "Beta": b_val,
                        "Ponderación (%)": weight * 100,
                        "Contribución al Riesgo": b_val * weight
                    })
                
                c_b1, c_b2 = st.columns([1, 2], gap="medium")
                with c_b1:
                    st.metric(label="Beta Ponderada del Portafolio", value=f"{weighted_beta:.2f}")
                    
                    # Risk profile interpretation
                    if weighted_beta < 0.85:
                        st.info("🛡️ **Perfil Defensivo**: Tu cartera es significativamente menos sensible a las fluctuaciones del mercado que el S&P 500. Ideal para mitigar caídas de mercado, pero capturará menos subidas.")
                    elif 0.85 <= weighted_beta <= 1.15:
                        st.success("⚖️ **Perfil de Mercado**: Tu cartera tiene una volatilidad similar a la del S&P 500. Se moverá en línea con el mercado general.")
                    else:
                        st.warning("🚀 **Perfil Agresivo (Alto Riesgo)**: Tu cartera es más volátil que el mercado general. Amplificará tanto las ganancias en mercados alcistas como las pérdidas en correcciones.")
                        
                with c_b2:
                    df_betas = pd.DataFrame(beta_records).sort_values(by="Beta")
                    fig_betas = px.bar(df_betas, x="Beta", y="Activo", orientation="h",
                                       title="Coeficiente Beta por Activo",
                                       color="Beta",
                                       color_continuous_scale=[COLOR_CAPITAL, COLOR_SIMULATION, COLOR_LOSS])
                    fig_betas.add_vline(x=1.0, line_dash="dash", line_color="gray", annotation_text="Mercado (1.0)")
                    fig_betas.update_layout(height=280, margin=dict(t=30, b=0, l=0, r=0))
                    st.plotly_chart(fig_betas, use_container_width=True)
            else:
                st.info("No hay activos suficientes para calcular el coeficiente Beta.")

        except Exception as e:
            st.warning(f"No se pudo cargar la simulación del mercado S&P 500: {e}")

# ==========================================
# PESTAÑA 4: RADAR DE OPORTUNIDADES
# ==========================================
with tab4:
    st.subheader("🔍 Análisis Estratégico de Activos (Cartera + Watchlist)")
    
    radar_data = []
    for s in all_symbols:
        p_actual = current_prices.get(s, 0)
        fund = fund_data.get(s, {})
        tech = tech_signals.get(s, {})
        
        # Cálculo de Decisión Score (0-100)
        score = 0
        rsi = tech.get('rsi', 50)
        if rsi < 35: score += 35
        elif rsi < 45: score += 15
        
        upside = 0
        target = fund.get('target_price')
        if target and target > p_actual:
            upside = ((target - p_actual) / p_actual) * 100
            if upside > 15: score += 40
            elif upside > 5: score += 20
        
        if tech.get('trend') == "Bullish": score += 25
        
        radar_data.append({
            "Ticker": s,
            "Nombre": fund.get('name', s),
            "Precio": p_actual,
            "P/E": fund.get('pe_ratio'),
            "Div Yield": fund.get('dividend_yield'),
            "Target Analistas": target,
            "Upside Potencial": upside,
            "RSI": rsi,
            "Tendencia": tech.get('trend', 'N/A'),
            "Decision Score": score,
            "En Cartera": "✅" if portfolio.get(s, {}).get('qty', 0) > 0 else "👀"
        })
    
    df_radar = pd.DataFrame(radar_data).sort_values(by="Decision Score", ascending=False)
    
    def color_score(val):
        if val >= score_threshold: return f'background-color: {COLOR_PROFIT}; color: black; font-weight: bold'
        if val < 40: return f'color: {COLOR_LOSS}'
        return ''

    def color_upside(val):
        return f'color: {COLOR_PROFIT}' if val > 10 else f'color: {COLOR_LOSS}' if val < -5 else ''

    st.markdown(f"**Criterio de Compra:** Se sugiere analizar activos con un Score mayor a **{score_threshold}**.")
    
    styled_radar = df_radar.style.format({
        "Precio": "${:,.2f}",
        "P/E": "{:.2f}",
        "Div Yield": "{:.2f}%",
        "Target Analistas": "${:,.2f}",
        "Upside Potencial": "{:.2f}%",
        "Decision Score": "{:.0f}/100"
    }).map(color_score, subset=['Decision Score']).map(color_upside, subset=['Upside Potencial'])
    
    st.dataframe(styled_radar, use_container_width=True)
    
    st.markdown("""
    ### 💡 Guía de Interpretación
    *   **Decision Score:** Una métrica compuesta que premia la **sobreventa (RSI bajo)**, el **potencial de revalorización** según analistas y la **tendencia positiva**.
    *   **Upside Potencial:** Distancia porcentual entre el precio actual y el precio objetivo promedio de Wall Street.
    *   **P/E (Forward):** Si es significativamente menor que la media del sector, podría indicar que la empresa está infravalorada.
    """)

    #.venv\Scripts\python.exe -m pip list