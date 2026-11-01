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

## Modelos globais descontinuados — 2026-11-01

A publicação operacional independente foi encerrada para:

- AIFS — branch histórica `aifs-data`;
- ECMWF IFS — branch histórica `ecmwf-data`;
- ECMWF HRES 9 km — branch histórica `ecmwf-hres9km-native`;
- GFS — branch histórica `gfs-model-data`;
- DWD ICON Global — branch histórica `icon-data`.

Os dados e branches foram preservados para consulta histórica; não devem ser considerados atualizados. Os workflows de publicação foram arquivados com a extensão `.yml.disabled`. A geração Skew-T global ECMWF/ICON/HRES também foi retirada da agenda. O workflow multimodelo continua publicando apenas UKMO e NEMS10.

A descontinuação desses produtos globais não desativa os WRF regionais. Os workflows regionais mantêm suas próprias agendas e obtêm os dados de entrada diretamente das fontes GFS, ICON e ECMWF. O controlador WRF deixou de disparar ou recuperar as publicações globais independentes.

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
