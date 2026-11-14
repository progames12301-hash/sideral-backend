# WRF SCM Skew-T — 27 capitais

Pipeline experimental separado dos WRFs operacionais e do Skew-T ECMWF existente. Ele produz apenas perfis verticais e PNGs; não gera mapas de precipitação ou radar.

O Single Column Model oficial do WRF usa um stencil 3×3 com bordas periódicas. A configuração usa espaçamento nominal de 3 km, mas não resolve gradientes horizontais. Portanto, não é uma previsão regional convectiva de 3 km. A inicialização e as forçantes de grande escala vêm do ECMWF IFS 0,25° via Open-Meteo Single Runs.

O workflow começa pelo piloto de Brasília, que gera F000–F048 a cada 3 horas e guarda PNGs, perfis e logs como artefato de diagnóstico, sem substituir as sondagens ECMWF operacionais. Depois de validar o piloto, workflow_dispatch com mode=full roda as 27 capitais e publica o release separado skewt-scm-live-<ciclo>.

O WRF calcula a evolução vertical da coluna; o renderizador oficial Sideral/SHARPpy cria as sondagens finais. Os arquivos wrfout completos não são publicados.
