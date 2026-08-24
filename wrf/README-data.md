# Sideral WRF data pipeline

Os arquivos WRF pesados existem apenas durante o GitHub Actions. O script `extract_wrf_json.py` converte os `wrfout_d01_*` em grades JSON gzip compactas para publicação na branch `wrf-data`.
