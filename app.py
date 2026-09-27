"""Dashboard de logística com persistência poliglota (SQLite + MongoDB).

Execução:
    streamlit run app.py

Dependências:
    pip install streamlit streamlit-folium folium plotly pandas pymongo

O endereço do MongoDB pode ser configurado pela variável MONGO_URI. Quando ela
não é informada, a aplicação usa mongodb://localhost:27017.
"""

from __future__ import annotations

import math
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import Fullscreen, MarkerCluster
from pymongo import ASCENDING, DESCENDING, GEOSPHERE, InsertOne, MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from streamlit_folium import st_folium


APP_DIR = Path(__file__).resolve().parent
SQLITE_PATH = APP_DIR / "logitech.db"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DATABASE = "geolog_db"
MONGO_COLLECTION = "telemetria"
EARTH_RADIUS_KM = 6378.1

PONTOS_APOIO = {
    "São Paulo - SP": (-23.5505, -46.6333),
    "Curitiba - PR": (-25.4284, -49.2733),
    "Porto Alegre - RS": (-30.0346, -51.2177),
    "Brasília - DF": (-15.7939, -47.8828),
    "Salvador - BA": (-12.9777, -38.5016),
    "Recife - PE": (-8.0476, -34.8770),
    "Belém - PA": (-1.4558, -48.4902),
    "Manaus - AM": (-3.1190, -60.0217),
}

FROTA_DEMO = [
    (1, "Ana Souza", "CNH-10001", "ATIVO", 101, "BRA2E19", "Volvo FH 540", "São Paulo - SP"),
    (2, "Bruno Lima", "CNH-10002", "ATIVO", 102, "MER7A42", "Scania R 450", "Curitiba - PR"),
    (3, "Carla Mendes", "CNH-10003", "FOLGA", 103, "NOR3T88", "DAF XF 530", "Porto Alegre - RS"),
    (4, "Diego Alves", "CNH-10004", "ATIVO", 104, "CEN5B11", "Mercedes Actros", "Brasília - DF"),
    (5, "Elisa Rocha", "CNH-10005", "ATIVO", 105, "BAH9I27", "Volvo VM 360", "Salvador - BA"),
    (6, "Fábio Costa", "CNH-10006", "INATIVO", 106, "REC1F63", "Iveco S-Way", "Recife - PE"),
    (7, "Gabriela Nunes", "CNH-10007", "ATIVO", 107, "AMA4Z90", "Scania G 410", "Manaus - AM"),
    (8, "Henrique Reis", "CNH-10008", "ATIVO", 108, "BEL6M35", "Volkswagen Meteor", "Belém - PA"),
]


st.set_page_config(
    page_title="LogiTrack Brasil",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)


def aplicar_estilo() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f5f7fb; color: #172033; }
        [data-testid="stMain"] h1,
        [data-testid="stMain"] h2,
        [data-testid="stMain"] h3,
        [data-testid="stMain"] p,
        [data-testid="stMain"] label,
        [data-testid="stMain"] [data-testid="stMetricLabel"] p,
        [data-testid="stMain"] [data-testid="stMetricValue"] {
            color: #172033 !important;
        }
        [data-testid="stMain"] [data-testid="stCaptionContainer"] p {
            color: #5e6b7a !important;
        }
        [data-testid="stSidebar"] { background: #10233f; }
        [data-testid="stSidebar"] * { color: #f7fafc; }
        [data-testid="stSidebar"] input { color: #10233f; }
        .hero {
            padding: 1.35rem 1.6rem; border-radius: 18px;
            background: linear-gradient(110deg, #10233f 0%, #175c73 60%, #16a085 100%);
            color: white; margin-bottom: 1.1rem;
            box-shadow: 0 12px 28px rgba(16, 35, 63, .14);
        }
        .hero h1 { margin: 0; font-size: 2rem; color: white !important; }
        .hero p { margin: .35rem 0 0 0; opacity: .88; color: white !important; }
        div[data-testid="stMetric"] {
            background: white; border: 1px solid #e4eaf1; border-radius: 14px;
            padding: .9rem 1rem; box-shadow: 0 4px 14px rgba(16, 35, 63, .05);
        }
        .status-ok { color: #16866a; font-weight: 700; }
        .status-error { color: #c2413b; font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def conectar_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar_sqlite() -> None:
    with conectar_sqlite() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS motoristas (
                id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                cnh TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status IN ('ATIVO', 'FOLGA', 'INATIVO'))
            );

            CREATE TABLE IF NOT EXISTS veiculos (
                id INTEGER PRIMARY KEY,
                placa TEXT NOT NULL UNIQUE,
                modelo TEXT NOT NULL,
                motorista_id INTEGER,
                FOREIGN KEY (motorista_id) REFERENCES motoristas(id)
                    ON UPDATE CASCADE ON DELETE SET NULL
            );
            """
        )

        total_motoristas = conn.execute("SELECT COUNT(*) FROM motoristas").fetchone()[0]
        total_veiculos = conn.execute("SELECT COUNT(*) FROM veiculos").fetchone()[0]
        if total_motoristas == 0 and total_veiculos == 0:
            conn.executemany(
                "INSERT INTO motoristas (id, nome, cnh, status) VALUES (?, ?, ?, ?)",
                [(item[0], item[1], item[2], item[3]) for item in FROTA_DEMO],
            )
            conn.executemany(
                "INSERT INTO veiculos (id, placa, modelo, motorista_id) VALUES (?, ?, ?, ?)",
                [(item[4], item[5], item[6], item[0]) for item in FROTA_DEMO],
            )


def carregar_cadastro() -> pd.DataFrame:
    consulta = """
        SELECT
            v.id AS veiculo_id,
            v.placa AS Placa,
            v.modelo AS Modelo,
            m.id AS motorista_id,
            m.nome AS Motorista,
            m.status AS Status
        FROM veiculos v
        LEFT JOIN motoristas m ON m.id = v.motorista_id
        ORDER BY v.id
    """
    with conectar_sqlite() as conn:
        return pd.read_sql_query(consulta, conn)


@st.cache_resource(show_spinner=False)
def conectar_mongo(uri: str) -> tuple[MongoClient, Collection]:
    client = MongoClient(uri, serverSelectionTimeoutMS=2500, connectTimeoutMS=2500)
    client.admin.command("ping")
    collection = client[MONGO_DATABASE][MONGO_COLLECTION]
    collection.create_index([("location", GEOSPHERE)], name="location_2dsphere")
    collection.create_index(
        [("veiculo_id", ASCENDING), ("timestamp", DESCENDING)],
        name="veiculo_timestamp",
    )
    return client, collection


def obter_mongo() -> tuple[Collection | None, str | None]:
    try:
        _, collection = conectar_mongo(MONGO_URI)
        return collection, None
    except (ServerSelectionTimeoutError, PyMongoError) as exc:
        return None, str(exc).splitlines()[0]


def popular_telemetria(collection: Collection) -> None:
    if collection.estimated_document_count() > 0:
        return

    rng = random.Random(2026)
    agora = datetime.now(timezone.utc)
    documentos: list[dict[str, Any]] = []
    for item in FROTA_DEMO:
        veiculo_id = item[4]
        latitude_base, longitude_base = PONTOS_APOIO[item[7]]
        temperatura = rng.uniform(2.5, 8.0)
        for passo in range(18):
            latitude = latitude_base + rng.uniform(-0.035, 0.035) + passo * 0.0012
            longitude = longitude_base + rng.uniform(-0.035, 0.035) + passo * 0.0010
            temperatura = max(-2.0, min(14.0, temperatura + rng.uniform(-0.45, 0.45)))
            documentos.append(
                {
                    "veiculo_id": veiculo_id,
                    "location": {
                        "type": "Point",
                        "coordinates": [round(longitude, 6), round(latitude, 6)],
                    },
                    "temperatura": round(temperatura, 2),
                    "velocidade": round(rng.uniform(38, 96), 1),
                    "timestamp": agora - timedelta(minutes=(17 - passo) * 5),
                    "origem": "carga_inicial",
                }
            )
    collection.insert_many(documentos)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def buscar_ids_geoespaciais(
    collection: Collection,
    latitude: float,
    longitude: float,
    raio_km: float,
    operador: str,
) -> set[int]:
    if operador == "$near":
        filtro = {
            "location": {
                "$near": {
                    "$geometry": {
                        "type": "Point",
                        "coordinates": [longitude, latitude],
                    },
                    "$maxDistance": raio_km * 1000,
                }
            }
        }
    else:
        filtro = {
            "location": {
                "$geoWithin": {
                    "$centerSphere": [[longitude, latitude], raio_km / EARTH_RADIUS_KM]
                }
            }
        }

    return {
        int(doc["veiculo_id"])
        for doc in collection.find(filtro, {"veiculo_id": 1, "_id": 0}).limit(10000)
        if "veiculo_id" in doc
    }


def ultimas_telemetrias(
    collection: Collection, veiculos: set[int] | None = None
) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = []
    if veiculos is not None:
        if not veiculos:
            return []
        pipeline.append({"$match": {"veiculo_id": {"$in": sorted(veiculos)}}})
    pipeline.extend(
        [
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$veiculo_id", "registro": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$registro"}},
            {"$sort": {"veiculo_id": 1}},
        ]
    )
    return list(collection.aggregate(pipeline))


def filtrar_posicoes_atuais(
    documentos: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    raio_km: float,
) -> list[dict[str, Any]]:
    resultado = []
    for doc in documentos:
        coordenadas = doc.get("location", {}).get("coordinates", [])
        if len(coordenadas) != 2:
            continue
        lon_veiculo, lat_veiculo = map(float, coordenadas)
        distancia = haversine_km(latitude, longitude, lat_veiculo, lon_veiculo)
        if distancia <= raio_km:
            doc = dict(doc)
            doc["distancia_km"] = distancia
            resultado.append(doc)
    return sorted(resultado, key=lambda item: item["distancia_km"])


def cruzar_dados(cadastro: pd.DataFrame, telemetrias: list[dict[str, Any]]) -> pd.DataFrame:
    linhas = []
    for doc in telemetrias:
        longitude, latitude = doc["location"]["coordinates"]
        linhas.append(
            {
                "veiculo_id": int(doc["veiculo_id"]),
                "Última temperatura (°C)": float(doc.get("temperatura", 0)),
                "Velocidade (km/h)": float(doc.get("velocidade", 0)),
                "Coordenadas atualizadas": f"{latitude:.5f}, {longitude:.5f}",
                "Distância (km)": float(doc.get("distancia_km", 0)),
                "Atualizado em": pd.to_datetime(doc.get("timestamp"), utc=True),
            }
        )
    telemetria_df = pd.DataFrame(linhas)
    if telemetria_df.empty:
        return pd.DataFrame(
            columns=[
                "Motorista",
                "Placa",
                "Última temperatura (°C)",
                "Velocidade (km/h)",
                "Coordenadas atualizadas",
                "Distância (km)",
                "Atualizado em",
            ]
        )
    return (
        cadastro.merge(telemetria_df, on="veiculo_id", how="inner")
        .sort_values("Distância (km)")
        [
            [
                "Motorista",
                "Placa",
                "Última temperatura (°C)",
                "Velocidade (km/h)",
                "Coordenadas atualizadas",
                "Distância (km)",
                "Atualizado em",
            ]
        ]
    )


def cor_marcador(temperatura: float, velocidade: float) -> str:
    if velocidade > 80:
        return "red"
    if temperatura > 8 or temperatura < 0:
        return "orange"
    return "green"


def construir_mapa(
    collection: Collection,
    cadastro: pd.DataFrame,
    telemetrias: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    raio_km: float,
    exibir_rotas: bool,
) -> folium.Map:
    # Não use um nome do catálogo xyzservices aqui. Versões recentes dessa
    # biblioteca chamam str.removesuffix(), disponível apenas no Python 3.9+.
    # A URL explícita mantém compatibilidade com o Python 3.8 sem perder o mapa.
    mapa = folium.Map(
        location=[latitude, longitude],
        zoom_start=6 if raio_km > 300 else 9,
        tiles=None,
        control_scale=True,
    )
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
        name="OpenStreetMap",
        overlay=False,
        control=False,
        max_zoom=19,
    ).add_to(mapa)
    Fullscreen(position="topright").add_to(mapa)
    folium.Marker(
        [latitude, longitude],
        tooltip="Localização de referência",
        popup=f"Referência: {latitude:.5f}, {longitude:.5f}",
        icon=folium.Icon(color="blue", icon="home", prefix="fa"),
    ).add_to(mapa)
    folium.Circle(
        [latitude, longitude],
        radius=raio_km * 1000,
        color="#1877a8",
        fill=True,
        fill_color="#38a3c7",
        fill_opacity=0.10,
        weight=2,
        tooltip=f"Raio de busca: {raio_km:.0f} km",
    ).add_to(mapa)

    cluster = MarkerCluster(name="Veículos localizados").add_to(mapa)
    cadastro_indexado = cadastro.set_index("veiculo_id").to_dict("index")
    cores_rota = ["#16a085", "#5b5bd6", "#d66a2c", "#8e44ad", "#087ea4"]

    for indice, doc in enumerate(telemetrias):
        veiculo_id = int(doc["veiculo_id"])
        dados = cadastro_indexado.get(veiculo_id, {})
        longitude_veiculo, latitude_veiculo = doc["location"]["coordinates"]
        temperatura = float(doc.get("temperatura", 0))
        velocidade = float(doc.get("velocidade", 0))
        popup = folium.Popup(
            f"<b>{dados.get('Placa', 'Sem placa')}</b><br>"
            f"Motorista: {dados.get('Motorista', 'Não cadastrado')}<br>"
            f"Modelo: {dados.get('Modelo', '-')}<br>"
            f"Temperatura: {temperatura:.1f} °C<br>"
            f"Velocidade: {velocidade:.1f} km/h<br>"
            f"Distância: {doc.get('distancia_km', 0):.1f} km",
            max_width=300,
        )
        folium.Marker(
            [latitude_veiculo, longitude_veiculo],
            tooltip=f"{dados.get('Placa', veiculo_id)} • {velocidade:.0f} km/h",
            popup=popup,
            icon=folium.Icon(
                color=cor_marcador(temperatura, velocidade),
                icon="truck",
                prefix="fa",
            ),
        ).add_to(cluster)

        if exibir_rotas:
            historico = list(
                collection.find(
                    {"veiculo_id": veiculo_id}, {"location": 1, "timestamp": 1}
                )
                .sort("timestamp", DESCENDING)
                .limit(20)
            )
            pontos = [
                [item["location"]["coordinates"][1], item["location"]["coordinates"][0]]
                for item in reversed(historico)
                if len(item.get("location", {}).get("coordinates", [])) == 2
            ]
            if len(pontos) > 1:
                folium.PolyLine(
                    pontos,
                    color=cores_rota[indice % len(cores_rota)],
                    weight=3,
                    opacity=0.75,
                    tooltip=f"Rota recente • {dados.get('Placa', veiculo_id)}",
                ).add_to(mapa)

    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def simular_movimentacao(collection: Collection, cadastro: pd.DataFrame) -> int:
    atuais = {int(doc["veiculo_id"]): doc for doc in ultimas_telemetrias(collection)}
    agora = datetime.now(timezone.utc)
    operacoes = []
    pontos_padrao = list(PONTOS_APOIO.values())

    for indice, veiculo_id in enumerate(cadastro["veiculo_id"].astype(int).tolist()):
        anterior = atuais.get(veiculo_id)
        if anterior:
            longitude, latitude = anterior["location"]["coordinates"]
            temperatura_anterior = float(anterior.get("temperatura", 5))
            velocidade_anterior = float(anterior.get("velocidade", 60))
        else:
            latitude, longitude = pontos_padrao[indice % len(pontos_padrao)]
            temperatura_anterior = 5.0
            velocidade_anterior = 60.0

        latitude += random.uniform(-0.012, 0.012)
        longitude += random.uniform(-0.012, 0.012)
        temperatura = max(-4.0, min(16.0, temperatura_anterior + random.uniform(-0.7, 0.7)))
        velocidade = max(0.0, min(110.0, velocidade_anterior + random.uniform(-18, 18)))
        operacoes.append(
            InsertOne(
                {
                    "veiculo_id": veiculo_id,
                    "location": {
                        "type": "Point",
                        "coordinates": [round(longitude, 6), round(latitude, 6)],
                    },
                    "temperatura": round(temperatura, 2),
                    "velocidade": round(velocidade, 1),
                    "timestamp": agora,
                    "origem": "simulador_streamlit",
                }
            )
        )
    if operacoes:
        collection.bulk_write(operacoes)
    return len(operacoes)


def carregar_historico(
    collection: Collection, veiculos: list[int], limite_por_veiculo: int = 60
) -> pd.DataFrame:
    linhas = []
    for veiculo_id in veiculos:
        docs = list(
            collection.find(
                {"veiculo_id": int(veiculo_id)},
                {"veiculo_id": 1, "temperatura": 1, "velocidade": 1, "timestamp": 1},
            )
            .sort("timestamp", DESCENDING)
            .limit(limite_por_veiculo)
        )
        linhas.extend(docs)
    if not linhas:
        return pd.DataFrame(columns=["veiculo_id", "temperatura", "timestamp"])
    resultado = pd.DataFrame(linhas)
    resultado["timestamp"] = pd.to_datetime(resultado["timestamp"], utc=True)
    return resultado.sort_values("timestamp")


def renderizar_dashboard() -> None:
    aplicar_estilo()
    inicializar_sqlite()
    cadastro = carregar_cadastro()
    collection, erro_mongo = obter_mongo()

    st.markdown(
        """
        <div class="hero">
            <h1>🚚 LogiTrack Brasil</h1>
            <p>Monitoramento unificado da frota com dados relacionais e geoespaciais.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("## Central de controle")
        ponto = st.selectbox("Ponto de apoio", ["Personalizado", *PONTOS_APOIO.keys()], index=1)
        if ponto == "Personalizado":
            latitude = st.number_input("Latitude", min_value=-34.0, max_value=6.0, value=-23.5505, format="%.6f")
            longitude = st.number_input("Longitude", min_value=-74.0, max_value=-34.0, value=-46.6333, format="%.6f")
        else:
            latitude, longitude = PONTOS_APOIO[ponto]
            st.caption(f"Referência: {latitude:.5f}, {longitude:.5f}")

        raio_km = st.slider("Raio de busca (km)", min_value=5, max_value=3000, value=250, step=5)
        operador = st.radio("Operador geoespacial", ["$near", "$geoWithin"], horizontal=True)
        exibir_rotas = st.toggle("Exibir rotas recentes", value=True)
        st.divider()

        if collection is not None:
            st.markdown('<span class="status-ok">● MongoDB conectado</span>', unsafe_allow_html=True)
            if st.button("▶ Simular Movimentação", type="primary", use_container_width=True):
                with st.spinner("Gerando novos sinais de telemetria..."):
                    quantidade = simular_movimentacao(collection, cadastro)
                st.toast(f"{quantidade} novos pontos gravados no MongoDB.", icon="✅")
                st.rerun()
        else:
            st.markdown('<span class="status-error">● MongoDB indisponível</span>', unsafe_allow_html=True)
        st.caption(f"SQLite: {SQLITE_PATH.name} • MongoDB: {MONGO_DATABASE}.{MONGO_COLLECTION}")

    if collection is None:
        st.error(
            "Não foi possível conectar ao MongoDB. Inicie o serviço local ou defina a variável "
            "MONGO_URI e recarregue a página. O cadastro SQLite foi inicializado normalmente."
        )
        with st.expander("Detalhes técnicos"):
            st.code(erro_mongo or "Erro de conexão não informado")
            st.code("docker run -d --name geolog-mongo -p 27017:27017 mongo:7")
        st.subheader("Cadastro relacional disponível")
        st.dataframe(cadastro, use_container_width=True, hide_index=True)
        return

    try:
        popular_telemetria(collection)
        ids_geograficos = buscar_ids_geoespaciais(
            collection, latitude, longitude, float(raio_km), operador
        )
        ultimos_candidatos = ultimas_telemetrias(collection, ids_geograficos)
        posicoes_filtradas = filtrar_posicoes_atuais(
            ultimos_candidatos, latitude, longitude, float(raio_km)
        )
        ultimos_globais = ultimas_telemetrias(collection)
    except PyMongoError as exc:
        st.error(f"Falha ao consultar o MongoDB: {exc}")
        return

    ativos = int((cadastro["Status"] == "ATIVO").sum())
    temperaturas = [float(doc.get("temperatura", 0)) for doc in ultimos_globais]
    alertas_velocidade = sum(float(doc.get("velocidade", 0)) > 80 for doc in ultimos_globais)
    media_temperatura = sum(temperaturas) / len(temperaturas) if temperaturas else 0.0

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Frotas ativas", ativos, help="Veículos vinculados a motoristas com status ATIVO")
    kpi2.metric("Temperatura média", f"{media_temperatura:.1f} °C")
    kpi3.metric("Alertas > 80 km/h", alertas_velocidade)
    kpi4.metric("Veículos no raio", len(posicoes_filtradas), f"raio de {raio_km} km")

    st.subheader("Mapa operacional")
    st.caption(
        f"Consulta executada com `{operador}`. Marcadores vermelhos indicam velocidade acima de 80 km/h; "
        "laranjas indicam temperatura fora da faixa de 0 a 8 °C."
    )
    mapa = construir_mapa(
        collection,
        cadastro,
        posicoes_filtradas,
        latitude,
        longitude,
        float(raio_km),
        exibir_rotas,
    )
    st_folium(mapa, use_container_width=True, height=520, returned_objects=[])

    st.subheader("Frota localizada e dados enriquecidos")
    tabela = cruzar_dados(cadastro, posicoes_filtradas)
    if tabela.empty:
        st.info("Nenhuma posição atual foi encontrada dentro do raio selecionado.")
    else:
        st.dataframe(
            tabela,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Última temperatura (°C)": st.column_config.NumberColumn(format="%.1f °C"),
                "Velocidade (km/h)": st.column_config.NumberColumn(format="%.1f km/h"),
                "Distância (km)": st.column_config.NumberColumn(format="%.1f km"),
                "Atualizado em": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm:ss"),
            },
        )

    st.subheader("Análises da operação")
    grafico1, grafico2 = st.columns([1.7, 1])
    placas_por_id = cadastro.set_index("veiculo_id")["Placa"].to_dict()
    veiculos_no_raio = [int(doc["veiculo_id"]) for doc in posicoes_filtradas]
    opcoes = veiculos_no_raio or cadastro["veiculo_id"].astype(int).tolist()
    padrao = opcoes[: min(4, len(opcoes))]

    with grafico1:
        selecionados = st.multiselect(
            "Veículos no histórico",
            options=opcoes,
            default=padrao,
            format_func=lambda item: placas_por_id.get(item, str(item)),
        )
        historico = carregar_historico(collection, selecionados)
        if not historico.empty:
            historico["Veículo"] = historico["veiculo_id"].map(placas_por_id)
            fig_temperatura = px.line(
                historico,
                x="timestamp",
                y="temperatura",
                color="Veículo",
                markers=True,
                labels={"timestamp": "Data/hora", "temperatura": "Temperatura (°C)"},
                title="Variação de temperatura por veículo",
                color_discrete_sequence=px.colors.qualitative.Safe,
            )
            fig_temperatura.add_hrect(y0=0, y1=8, fillcolor="#16a085", opacity=0.08, line_width=0)
            fig_temperatura.update_layout(hovermode="x unified", legend_title_text="Placa", height=410)
            st.plotly_chart(fig_temperatura, use_container_width=True)
        else:
            st.info("Selecione ao menos um veículo para visualizar o histórico.")

    with grafico2:
        status_df = cadastro.groupby("Status", as_index=False).size().rename(columns={"size": "Motoristas"})
        fig_status = px.pie(
            status_df,
            names="Status",
            values="Motoristas",
            hole=0.55,
            title="Status dos motoristas",
            color="Status",
            color_discrete_map={"ATIVO": "#16a085", "FOLGA": "#f2b134", "INATIVO": "#d6534d"},
        )
        fig_status.update_traces(textposition="inside", textinfo="percent+label")
        fig_status.update_layout(showlegend=False, height=410)
        st.plotly_chart(fig_status, use_container_width=True)

    with st.expander("Como a persistência poliglota funciona"):
        st.markdown(
            """
            - **SQLite (`logitech.db`)** mantém motoristas e veículos com chave estrangeira e restrições de integridade.
            - **MongoDB (`geolog_db.telemetria`)** recebe os sinais em alta frequência no formato GeoJSON e usa índice `2dsphere`.
            - A busca espacial recupera candidatos com `$near` ou `$geoWithin`; a posição mais recente é validada pelo raio e cruzada com o cadastro pelo `veiculo_id`.
            - O simulador grava um novo documento por veículo e chama `st.rerun()`, atualizando KPIs, mapa, tabela e gráficos sem reiniciar o servidor.
            """
        )


if __name__ == "__main__":
    renderizar_dashboard()
