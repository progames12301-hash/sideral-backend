# WRF contínuo no GitHub Actions

O WRF é executado pelo GitHub Actions. Nenhuma parte do modelo é enviada ao
Render e o Google Cloud não executa o processamento.

Cada rodada:

1. detecta o GFS mais recente;
2. evita recalcular uma rodada já publicada;
3. gera 72 horas de previsão com quadros horários;
4. publica as imagens e o manifesto em uma GitHub Release;
5. remove releases WRF com mais de 72 horas;
6. verifica imediatamente se uma rodada nova surgiu enquanto o WRF rodava.

Os binários compilados do WRF/WPS e os geodados são reaproveitados pelo cache do
GitHub Actions. Os resultados não são adicionados ao histórico Git.
