# Backend de Modelos Numéricos

Este módulo não simula dados. Ele publica rodadas locais presentes em `SIDERAL_MODEL_DATA_DIR` e também lê as branches de dados reais produzidas pelo GitHub Actions.

## Variáveis de ambiente

- `SIDERAL_MODEL_DATA_DIR`: diretório persistente com os dados processados.
- `SIDERAL_MODEL_CACHE_DIR`: cache de PNGs gerados.
- `SIDERAL_COMMON_GRID_DEGREES`: resolução comum; padrão `0.25`.
- `SIDERAL_MIN_MULTIMODEL_MEMBERS`: mínimo de modelos válidos; nunca inferior a 2.
- `SIDERAL_MODEL_CACHE_DAYS`: retenção dos produtos derivados; padrão 3 dias.
- `SIDERAL_REMOTE_MODELS_ENABLED`: habilita as fontes remotas reais; padrão `1`.
- `SIDERAL_ECMWF_DATA_URL`, `SIDERAL_ICON_DATA_URL`, `SIDERAL_GFS_DATA_URL`, `SIDERAL_AIFS_DATA_URL`: bases remotas opcionais.

Nenhuma chave de provedor deve ser armazenada neste diretório ou enviada ao frontend.

## Alimentação automática

- ECMWF IFS: branch `ecmwf-data` já produzida pelo workflow oficial do projeto;
- ICON: branch `icon-data` já produzida pelo workflow oficial do projeto;
- GFS: `.github/workflows/gfs-global-models.yml`, dados NOAA/NCEP NOMADS;
- AIFS: `.github/workflows/aifs-global-models.yml`, dados ECMWF Open Data.

GFS e AIFS usam `backend.models.publish_remote` e publicam somente JSON compactado validado. Esses processos não executam nem modificam o WRF. Chuva de 24 horas é a soma dos intervalos reais publicados; períodos incompletos não são anunciados como 24 horas.

## Estrutura aceita

```text
model_data/
  ecmwf/
    2026082700/
      manifest.json
      frames/brazil/qpf24/f024.png
      fields/brazil/qpf24/f024.npz
  gfs/
  icon/
  aifs/
```

Um campo `.npz` precisa conter:

- `lat`: eixo 1D ou grade 2D;
- `lon`: eixo 1D ou grade 2D;
- `values`: campo 2D;
- `unit`: texto escalar;
- `valid_time`: texto ISO-8601 escalar recomendado.

O `manifest.json` pode declarar `forecast_hours`, mas a API também descobre horários pelos nomes `f000`, `f003`, etc.

Campos reais podem ser publicados atomicamente com:

```bash
python -m backend.models.ingest --model gfs --run 2026082700 --product qpf24 --region brazil --fh 24 --npz campo.npz
```

## Garantias

- regridding para grade comum sem extrapolação fora do domínio;
- preservação de `NaN`;
- normalização de unidades antes de combinar modelos;
- validação de horário válido, variável, unidade e grade;
- probabilidade somente com pelo menos dois modelos válidos;
- cache derivado com escrita atômica e expiração;
- respostas 404/409/503 quando os dados reais não forem suficientes.
