# Brasil Scope V3 — REF e VEL reais

## Fontes e formatos
- CEMADEN: o catálogo lista V.vol.h5, dBZ.vol.h5 e W.vol.h5. V é velocidade; W é largura espectral. Não automatizamos downloads protegidos por CAPTCHA.
- Não foi obtido um volume CEMADEN real nesta implementação. O leitor suporta a estrutura ODIM detectada, com testes controlados; não declara suporte comprovado a outros dialetos CEMADEN.
- Inspeção legítima: `python -m backend.radar_v3.adapters.cemaden arquivo.vol.h5`. Informa grupos, datasets, shapes e atributos.
- Importação: `python -m backend.radar_v3.ingest --provider cemaden --radar almenara --file arquivo.vol.h5`. Estruturas desconhecidas são recusadas para investigação.
- ODIM: DBZH/DBZV/TH/TV e VRADH/VRADV/VRAD/VRADHC. Gain/offset, nodata/undetect, unidades, azimutes medidos e menor elevação disponível. Preserva NI/Nyquist, PRFs e wavelength, sem dealiasing adicional.
- CPTEC/SIGMA: menu público e logs por código, conforme JavaScript oficial. Em 05/09/2026 foram baixados REF e VEL de Chapecó de 22:30 UTC: PNG RGBA + PGW.
- PNG mantém paleta original; não é convertido em velocidades inventadas (`quantitative=false`, `kind=raster`). Legenda oficial acompanha o produto. PGW geográfico usa centros dos pixels; reprojeção Mercator por nearest-neighbor, preservando transparência.
- O diretório `/nowcasting/DADOS/velocidade_radial/` consultado contém setembro/2023. Não é usado como dado atual.
- Volume polar local tem prioridade; raster oficial é a alternativa. Ausência de VEL não modifica REF nem gera velocidade.

## Servidor existente
Integração em `server.py`, exclusivamente no prefixo `/api/radar/v3/`, por `integration.py`. Não cria outro Render, nem altera endpoints antigos ou WRF. h5py é a dependência adicional; numpy, requests e Pillow já existem.

Local isolado: `python -m backend.radar_v3.server`, porta 8773. Produção usa o servidor Sideral existente.

## API
- `/api/radar/v3/health`
- `/api/radar/v3/radars`
- `/api/radar/v3/products?radar=cptec-chapeco`
- `/api/radar/v3/frames?radar=cptec-chapeco&product=velocity`
- `/api/radar/v3/latest?radar=cptec-chapeco&product=reflectivity`
- `/api/radar/v3/metadata?radar=cptec-chapeco&product=velocity`
- `/api/radar/v3/metadata?id=<frameId>`
- `/api/radar/v3/frame?radar=cptec-chapeco&product=velocity&time=<ISO-8601>`
- `/api/radar/v3/data?id=<frameId>`
- `/api/radar/v3/image?id=<frameId>`

Catálogo distingue `advertisedProducts` (anunciados) de `products` (verificados). O endpoint products verifica arquivos antes de habilitar controles. Imagens anteriores a 48 horas não são apresentadas como recentes.

## Cache
Cache por fonte/radar/horário/produto. Primeira consulta busca até três imagens; reutilização por 120 segundos. Quadros adquiridos ficam por até 48 horas, limitados a 256 MB. Não promete histórico anterior ao início da coleta. Cache efêmero é perdido no reinício do Render; RADAR_V3_CACHE pode usar disco persistente existente.

Volumes geram Int16 LE, escala 0.01, sentinela -32768 e gzip. Um volume decodificado por vez; quatro buffers no navegador. WebGL NEAREST, LUT REF independente de VEL (-60 a +60 m/s). Zero de VEL é válido.

RADAR_V3_INPUT define entrada; RADAR_V3_CACHE define cache; RADAR_V3_FEEDS aponta para JSON privado de feeds HTTPS autorizados. Sem credenciais no frontend. CORS aceita Firebase e localhost.

## Testes
`python -m unittest backend.radar_v3.test_service -v`

No repositório do site: `node backend/radar_v3/test_controller.cjs` e `node backend/radar_v3/test_renderer.cjs`.

Fixtures artificiais são exclusivas dos testes. V1/V2 e WRF não foram editados; o bootstrap inicializa o controlador V3.

Fontes: [SIGMA](https://sigma.cptec.inpe.br/), [CEMADEN](https://mapainterativo.cemaden.gov.br/download/downradares.php?produto=vol_400km_3steps.vol&radar=petrolina), [ODIM 2.4](https://eumetnet.eu/wp-content/uploads/2021/07/ODIM_H5_v2.4.pdf).
