# Brasil Scope V3/V5 — REF, VEL e volumes reais

## Fontes e formatos
- CEMADEN: o catálogo lista V.vol.h5, dBZ.vol.h5 e W.vol.h5. V é velocidade; W é largura espectral. Não automatizamos downloads protegidos por CAPTCHA.
- Não foi obtido um volume CEMADEN real nesta implementação. O leitor suporta a estrutura ODIM detectada, com testes controlados; não declara suporte comprovado a outros dialetos CEMADEN.
- Inspeção legítima: `python -m backend.radar_v3.adapters.cemaden arquivo.vol.h5`. Informa grupos, datasets, shapes e atributos.
- Importação: `python -m backend.radar_v3.ingest --provider cemaden --radar almenara --file arquivo.vol.h5`. Estruturas desconhecidas são recusadas para investigação.
- ODIM: DBZH/DBZV/TH/TV e VRADH/VRADV/VRAD/VRADHC. Gain/offset, nodata/undetect, unidades, azimutes medidos e todas as elevações físicas disponíveis. Preserva NI/Nyquist, PRFs e wavelength, sem dealiasing adicional.
- CPTEC/SIGMA: menu público e logs por código, conforme JavaScript oficial. Em 05/09/2026 foram baixados REF e VEL de Chapecó de 22:30 UTC: PNG RGBA + PGW.
- PNG mantém paleta original; não é convertido em velocidades inventadas (`quantitative=false`, `kind=raster`). A legenda genérica de vento do SIGMA não corresponde ao raster Doppler verificado; por isso não é aplicada. A escala numérica só acompanha dados polares quantitativos. PGW geográfico usa centros dos pixels; reprojeção Mercator por nearest-neighbor, preservando transparência.
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

### Volume multi-elevação para Brasil Scope V5
- `/api/radar/v3/volumes?radar=<radar>&product=reflectivity` lista volumes ODIM/HDF5 reais e suas elevações.
- `/api/radar/v3/volume?id=<volumeId>` retorna o manifesto detalhado com todos os sweeps, azimutes reais e `dataUrl` individual para cada elevação.
- `/api/radar/v3/volume?radar=<radar>&product=reflectivity&time=<ISO-8601>` busca um volume por radar/horário; sem `time`, retorna o mais recente.

O endpoint de volume não transforma PNG/CAPPI em volume 3D. Só arquivos polares HDF5/ODIM reais entram como `kind=volume`. O leitor mantém `read_volume()` para os clientes V3, que continua retornando apenas a menor elevação, enquanto `read_volume_sweeps()` preserva todas as elevações para o V5.

Catálogo distingue `advertisedProducts` (anunciados) de `products` (verificados). O endpoint products verifica arquivos antes de habilitar controles. Imagens anteriores a 48 horas não são apresentadas como recentes.

## Cache
Cache por fonte/radar/horário/produto. Primeira consulta busca até três imagens; reutilização por 120 segundos. Quadros adquiridos ficam por até 48 horas, limitados a 256 MB. Não promete histórico anterior ao início da coleta. Cache efêmero é perdido no reinício do Render; RADAR_V3_CACHE pode usar disco persistente existente.

Sweeps polares geram Int16 LE, escala 0.01, sentinela -32768 e gzip. Cada sweep recebe um `frameId`; o volume recebe um `volumeId` e um manifesto que referencia todos os seus sweeps. Um volume é decodificado por vez. WebGL usa NEAREST, LUT REF independente de VEL (-60 a +60 m/s). Zero de VEL é válido.

RADAR_V3_INPUT define entrada; RADAR_V3_CACHE define cache; RADAR_V3_FEEDS aponta para JSON privado de feeds HTTPS autorizados. `RADAR_V3_VOLUME_FILES` limita quantos H5 recentes são indexados pela API de volumes (padrão 96, máximo 576). Sem credenciais no frontend. CORS aceita Firebase e localhost.

## Testes
`python -m unittest backend.radar_v3.test_service -v`

`python -m unittest backend.radar_v3.test_volume_api -v`

No repositório do site: `node backend/radar_v3/test_controller.cjs` e `node backend/radar_v3/test_renderer.cjs`.

Fixtures artificiais são exclusivas dos testes. V1/V2 e WRF não foram editados; o bootstrap inicializa o controlador V3/V5.

Fontes: [SIGMA](https://sigma.cptec.inpe.br/), [CEMADEN](https://mapainterativo.cemaden.gov.br/download/downradares.php?produto=vol_400km_3steps.vol&radar=petrolina), [ODIM 2.4](https://eumetnet.eu/wp-content/uploads/2021/07/ODIM_H5_v2.4.pdf).
