# Algoritmos finais Renaest

Reprodução dos gráficos, tabelas, verificações de cobertura e matriz de priorização do estudo de sinistros envolvendo veículos de carga no Brasil entre 2023 e 2025.

A pasta `dados/maio` contém os quatro CSVs originais da extração de 12 de maio de 2026, compactados individualmente com gzip. O script lê os arquivos compactados diretamente e usa as tabelas Acidentes, TipoVeiculo e Vitimas. A tabela Localidade integra a extração arquivada.

## Executar no Windows

Com Python 3.11 instalado, abra o terminal nesta pasta e execute:

```powershell
py -3.11 -m pip install duckdb pandas matplotlib
py -3.11 analise_renaest_defesa.py
```

Os gráficos em PNG, as tabelas em CSV e o resumo em texto ficam em `resultados_defesa`. Para obter os CSVs ao clonar o repositório, instale o Git LFS e execute `git lfs pull`.
