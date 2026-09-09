# Dados do WRF da Sideral

Os arquivos completos produzidos pelo WRF (`wrfout`) são grandes e existem apenas durante o processamento. Depois que uma simulação termina, o pós-processamento lê os campos necessários e cria grades compactas para a aplicação web.

Esse passo não cria nem "melhora" artificialmente a previsão: ele apenas transforma a saída do modelo em um formato menor e mais rápido de carregar.

Nos produtos marcados como **REFL_10CM nativo**, a refletividade é extraída diretamente do diagnóstico de refletividade presente na saída do WRF. Junto dos campos são publicados metadados de rodada, horário de validade e origem da variável para permitir conferir o que está sendo exibido.

A infraestrutura interna e detalhes que não são necessários para interpretar meteorologicamente os produtos não são documentados aqui. A metodologia geral e as limitações do sistema são descritas na documentação pública da branch `main`.
