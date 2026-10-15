# METBR WRF 4 km — publicação JSON nativa

- metadata.json: horários, variáveis e contrato de publicação
- grid.json: coordenadas nativas XLAT/XLONG
- vertical-f000.npz: grade vertical nativa para o Skew-T click-to-point
- frames/fNNN.json.gz: refletividade REFL_10CM nativa e demais campos disponíveis
- metbr_wrf_4km.json: índice compatível com a interface legada

Checkpoints parciais da execução METBR mais recente podem ser publicados com publicationStatus=partial.
A publicação final é marcada complete quando todos os 43 frames F000-F042 forem validados.
A grade nativa, os perfis verticais e a refletividade REFL_10CM são obrigatórios em ambas as etapas.
