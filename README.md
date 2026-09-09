# Sideral Meteorologia — backend e modelos

Este repositório reúne parte da infraestrutura meteorológica usada pela **Sideral Meteorologia**. Aqui ficam serviços de dados, rotinas de processamento e os pipelines que alimentam produtos como previsões regionais e campos derivados.

## Sobre o nosso WRF

O WRF da Sideral **não é uma imagem inventada, um filtro sobre outro mapa ou uma simples ampliação do GFS**. Nós executamos o WRF-ARW como modelo regional, usando dados de modelos globais como condição inicial e de contorno. O modelo recebe esses campos, a descrição geográfica do domínio e integra a atmosfera em uma grade regional de aproximadamente 4 km.

De forma resumida, o processo é:

1. uma rodada global disponível é selecionada como entrada;
2. os campos atmosféricos são preparados para o domínio regional;
3. o WPS monta a grade e interpola os dados necessários;
4. `real.exe` prepara as condições do WRF;
5. `wrf.exe` executa a integração numérica;
6. os arquivos `wrfout` resultantes são pós-processados para formatos mais leves usados no site.

Na refletividade, quando o produto é identificado como **REFL_10CM nativo**, o valor vem do diagnóstico de refletividade produzido pelo próprio WRF. Ele não é uma imagem de radar observada e não deve ser interpretado como se fosse uma medição em tempo real.

A resolução de **4 km** significa espaçamento de grade. Isso não significa precisão de 4 km para a posição de cada tempestade: convecção é altamente sensível às condições iniciais e pequenos erros crescem rapidamente com o tempo de previsão.

As rodadas passam por validações automáticas de ciclo, horários, quantidade de quadros e metadados antes da publicação. Os arquivos completos do modelo são grandes e temporários; para o site, somente produtos processados e metadados necessários são mantidos.

Para uma explicação mais completa, mas sem expor detalhes operacionais desnecessários, veja [COMO-FUNCIONA-WRF.md](COMO-FUNCIONA-WRF.md).

## Transparência

A Sideral é um projeto independente e em desenvolvimento contínuo. Os produtos numéricos são experimentais e devem ser usados junto de dados observacionais, radares, satélites e fontes meteorológicas oficiais. Quando uma rodada falha ou fica incompleta, preferimos indicar a indisponibilidade a apresentar dados como se fossem uma execução válida.
