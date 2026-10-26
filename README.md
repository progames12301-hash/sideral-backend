# Pacote de entrega — WRF SIDERAL Sudeste 7 km + WRF2 Sudeste 7 km

Este ZIP foi separado para entregar apenas os sistemas antigos de 7 km do Sudeste:
- WRF Sudeste 7 km: GFS, ICON e ECMWF, publicação horária até F040.
- WRF2 Sudeste 7 km: GFS, ICON e ECMWF, diagnósticos severos até F048.

Não inclui os WRF SIDERAL 4 km/5 km, METBR, CIM, CBR, TARC nem outros modelos do repositório.

## Atenção aos nomes antigos
Os workflows `wrf-sudeste-gfs-4km.yml`, `wrf-sudeste-icon-4km.yml` e `wrf-sudeste-ecmwf-4km.yml` têm nomes históricos enganosos: o conteúdo declara 7 km / 7000 m. Eles pertencem a este pacote de 7 km, não aos modelos novos de 4 km.

## Workflows incluídos
- `.github/workflows/wrf-sudeste-gfs-4km.yml`
- `.github/workflows/wrf-sudeste-icon-4km.yml`
- `.github/workflows/wrf-sudeste-ecmwf-4km.yml`
- `.github/workflows/wrf-regional-7km-single.yml` (runner reutilizável do WRF principal)
- `.github/workflows/wrf2-sudeste-gfs-7km.yml`
- `.github/workflows/wrf2-sudeste-icon-7km.yml`
- `.github/workflows/wrf2-sudeste-ecmwf-7km.yml`
- `.github/workflows/wrf2-sudeste-7km-reusable.yml` (runner reutilizável do WRF2)

## Branches de publicação
WRF principal: `wrf-sudeste-gfs-data`, `wrf-sudeste-icon-data`, `wrf-sudeste-ecmwf-data`.
WRF2: `wrf2-gfs-data`, `wrf2-icon-data`, `wrf2-ecmwf-data`.

## Para encerrar a operação
Para parar a geração automática no repositório operacional, desabilite ou remova os seis workflows de entrada (três WRF 7 km e três WRF2 7 km). Os dois workflows reutilizáveis não têm agenda própria: são chamados pelos workflows de entrada. Este pacote é apenas uma cópia de entrega; nada foi desativado nem excluído do branch principal.

## Dependências externas
Os workflows ainda esperam os branches de dados globais (`gfs-model-data`, `icon-data`, `ecmwf-data`), GitHub Actions com permissão de escrita e a imagem Docker `dtcenter/wps_wrf:latest`. Os segredos são herdados dos workflows originais; não há credenciais incluídas neste pacote.

Os workflows reutilizáveis foram adaptados nesta cópia para buscar os scripts deste branch: `handoff-wrf-sudeste-7km-20261026`.
