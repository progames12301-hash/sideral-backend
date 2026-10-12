# METBR WRF 4 km — publicação JSON nativa

- `metadata.json`: horários e contrato de publicação
- `grid.json`: coordenadas geográficas nativas XLAT/XLONG, uma única vez
- `vertical-f000.npz`: grade vertical nativa completa F000 (pressão, temperatura, ponto de orvalho, altura, vento e omega) para Skew-T click-to-point; não contém PNGs nem valores interpolados de outro modelo
- `frames/fNNN.json.gz`: quadros comprimidos com refletividade nativa e demais campos disponíveis
- `metadata.json`: inventário das variáveis WRFOUT e campos disponíveis por horário
- `metbr_wrf_4km.json`: índice legado compatível, com os horários e a refletividade

Campos publicados quando presentes/deriváveis no WRFOUT: precipitação acumulada, temperatura a 2 m, umidade relativa a 2 m, vento a 10 m, água precipitável, CAPE, bulk shear 0–6 km e vorticidade relativa em 850 hPa. Cada frame informa `availableVariables` e `fieldSources`; campos ausentes não são substituídos por outros modelos.

Este produto METBR é servido diretamente desta branch, sem proxy Render e sem reutilizar quadros de outros WRF.
