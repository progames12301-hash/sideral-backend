# Sideral WRF — ambiente de execução

Esta branch guarda os arquivos usados para preparar e executar as simulações regionais do WRF da Sideral Meteorologia.

O fluxo parte de campos de um modelo global, prepara a grade meteorológica regional com o WPS e então executa o WRF-ARW. Depois da integração, as saídas `wrfout` são lidas por rotinas de pós-processamento que extraem somente os campos necessários para publicação no site.

A configuração principal trabalha com grade regional de aproximadamente **4 km**. Em produtos identificados como **REFL_10CM nativo**, a refletividade publicada é obtida da variável calculada pelo próprio WRF durante a simulação — não de uma imagem de radar e não de um simples redimensionamento de outro modelo.

Os arquivos completos de saída são temporários por causa do tamanho. O que permanece publicado são versões compactas dos campos e seus metadados de rodada.

> Este é um projeto meteorológico experimental. Resolução de grade não equivale à precisão exata da posição de tempestades, e os produtos não substituem dados observacionais ou fontes oficiais.

Uma explicação pública mais completa está no README da branch principal e em `COMO-FUNCIONA-WRF.md` na branch `main`.
