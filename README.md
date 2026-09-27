# LogiTrack Brasil - Dashboard de Entregas

Dashboard de monitoramento de frota desenvolvido com Streamlit e persistência
poliglota:

- **SQLite (`logitech.db`)**: motoristas e veículos com integridade referencial.
- **MongoDB (`geolog_db.telemetria`)**: posições GeoJSON, temperatura, velocidade
  e histórico de telemetria.
- **Folium**: mapa, raio de busca, veículos e rotas recentes.
- **Plotly**: indicadores e gráficos analíticos.

## Requisitos

- Python 3.8 ou superior
- MongoDB local ou Docker

## Instalação

```bash
python -m pip install -r requirements.txt
```

Inicie o MongoDB. Com Docker:

```bash
docker run -d --name geolog-mongo -p 27017:27017 mongo:7
```

Se o MongoDB estiver em outro endereço, configure a variável `MONGO_URI`:

```powershell
$env:MONGO_URI="mongodb://usuario:senha@servidor:27017"
```

## Executar

Na pasta do projeto, rode:

```bash
streamlit run app.py
```

O navegador abrirá normalmente em `http://localhost:8501`.

Na primeira execução, a aplicação:

1. cria e popula as tabelas `motoristas` e `veiculos` no SQLite, caso estejam vazias;
2. cria a coleção `geolog_db.telemetria` e seus índices no MongoDB;
3. adiciona um histórico de demonstração quando a coleção estiver vazia.

Use o botão **Simular Movimentação** para gravar novos pontos GPS no MongoDB e
atualizar automaticamente o mapa, os KPIs, a tabela e os gráficos.

## Arquivos principais

- `app.py`: aplicação Streamlit completa;
- `logitech.db`: cadastro SQLite inicial;
- `output/pdf/relatorio_tecnico_persistencia_poliglota.pdf`: relatório de arquitetura.
