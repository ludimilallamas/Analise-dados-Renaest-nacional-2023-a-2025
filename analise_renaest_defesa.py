
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

try:
    import duckdb
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as erro:
    raise SystemExit("Instale as dependências: py -3.11 -m pip install duckdb pandas matplotlib") from erro


PASTA_DADOS = Path(__file__).resolve().parent / "dados"
SAIDA = Path(__file__).resolve().parent / "resultados_defesa"
ANOS = [2023, 2024, 2025]
CARGA = ("CAMINHAO", "CAMINHAO-TRATOR", "VEICULO ARTICULADO", "TREMINHAO")
MESES = "Jan Fev Mar Abr Mai Jun Jul Ago Set Out Nov Dez".split()
CORES = ["#24618b", "#df8429", "#29927e"]
INFORMADO = "NOT IN ('NAO INFORMADO', 'DESCONHECIDO', 'IGNORADO')"


def fonte(nome_maio, nome_consolidado):
    original = PASTA_DADOS / "maio" / f"{nome_maio}_DadosAbertos_20260512.csv"
    consolidado = PASTA_DADOS / "consolidados_blocos" / f"renaest_{nome_consolidado}_2026_jan_maio.csv"
    if not original.is_file():
        original = original.with_suffix(".csv.gz")
    if original.is_file():
        arquivo, separador, filtro = original, ";", "TRUE"
    elif consolidado.is_file():
        arquivo, separador, filtro = consolidado, ",", "_mes_origem = '2026-05'"
    else:
        raise FileNotFoundError(f"Arquivo de {nome_maio} ausente. Procurei em:\n{original}\n{consolidado}")
    print(f"Lendo {arquivo.name}...", flush=True)
    caminho_sql = arquivo.as_posix().replace("'", "''")
    return f"read_csv('{caminho_sql}', delim='{separador}', header=true, all_varchar=true, strict_mode=false, ignore_errors=true)", filtro


def rotulo(coluna):
    return f"COALESCE(NULLIF(UPPER(strip_accents(TRIM({coluna}))), ''), 'NAO INFORMADO')"


def preparar(con):
    orig, maio = fonte("Acidentes", "acidentes")
    con.execute(f"""
        CREATE TABLE acidentes AS
        SELECT TRIM(num_acidente) AS id, TRY_CAST(ano_acidente AS INTEGER) AS ano,
               TRY_CAST(mes_acidente AS INTEGER) AS mes,
               TRY_CAST(data_acidente AS DATE) AS data,
               {rotulo('uf_acidente')} AS uf, {rotulo('tp_acidente')} AS tipo_sinistro,
               TRY_CAST(qtde_obitos AS BIGINT) AS obitos,
               TRY_CAST(qtde_envolvidos AS BIGINT) AS envolvidos
        FROM {orig} WHERE {maio} AND ano_acidente IN ('2023','2024','2025')
    """)
    registros, distintos = con.execute("SELECT count(*), count(DISTINCT NULLIF(id,'')) FROM acidentes").fetchone()
    if registros != distintos:
        raise ValueError("Há IDs duplicados ou vazios no recorte de acidentes. Verifique a extração antes de cruzar os arquivos.")
    orig, maio = fonte("TipoVeiculo", "tipo_veiculo")
    categorias = ", ".join(f"'{nome}'" for nome in CARGA)
    con.execute(f"""
        CREATE TABLE veiculos AS
        SELECT DISTINCT a.id, {rotulo('v.tipo_veiculo')} AS categoria
        FROM {orig} v JOIN acidentes a ON TRIM(v.num_acidente) = a.id
        WHERE {maio};
        CREATE TABLE carga AS SELECT * FROM acidentes
        WHERE id IN (SELECT id FROM veiculos WHERE categoria IN ({categorias}));
    """)
    if not con.execute("SELECT count(*) FROM carga").fetchone()[0]:
        raise ValueError("Nenhum sinistro de carga foi encontrado. Confira os CSVs da extração de maio.")
    orig, maio = fonte("Vitimas", "vitimas")
    con.execute(f"""
        CREATE TABLE vitimas AS
        SELECT c.id, {rotulo('v.faixa_idade')} AS idade, {rotulo('v.genero')} AS genero,
               {rotulo('v.tp_envolvido')} AS tipo_envolvido, {rotulo('v.gravidade_lesao')} AS lesao,
               {rotulo('v.ind_motorista')} AS motorista,
               {rotulo('v.equip_seguranca')} AS equipamento,
               {rotulo('v.susp_alcool')} AS alcool,
               TRY_CAST(v.qtde_envolvidos AS BIGINT) AS pessoas,
               TRY_CAST(v.qtde_obitos AS BIGINT) AS obitos
        FROM {orig} v JOIN carga c ON TRIM(v.num_acidente) = c.id WHERE {maio};
    """)


def salvar(tabela, nome):
    tabela.to_csv(SAIDA / f"{nome}.csv", index=False, encoding="utf-8-sig", sep=";", decimal=",")
    return tabela


INDICADORES = """
    count(*) AS sinistros,
    count(*) FILTER (WHERE obitos > 0) AS sinistros_com_obitos,
    sum(obitos)::BIGINT AS obitos,
    count(*) FILTER (WHERE obitos IS NULL OR obitos < 0) AS obitos_invalidos
"""


def tabelas(con):
    q = lambda sql: con.execute(sql).df()
    anual = q(f"SELECT ano, {INDICADORES} FROM carga GROUP BY ano ORDER BY ano")
    anual["variacao_sinistros_pct"] = anual.sinistros.pct_change() * 100
    anual["variacao_obitos_pct"] = anual.obitos.pct_change() * 100
    anual["proporcao_com_obitos_pct"] = 100 * anual.sinistros_com_obitos / anual.sinistros
    salvar(anual, "01_resumo_anual")
    mensal = q(f"SELECT ano, mes, {INDICADORES} FROM carga GROUP BY ano, mes ORDER BY ano, mes")
    meses = pd.MultiIndex.from_product([ANOS, range(1, 13)], names=["ano", "mes"])
    mensal = mensal.set_index(["ano", "mes"]).reindex(meses, fill_value=0).reset_index()
    salvar(mensal, "02_serie_mensal")
    resumo = []
    for ano, grupo in mensal.groupby("ano"):
        x = grupo.sinistros
        resumo.append(dict(ano=ano, media=x.mean(), mediana=x.median(), desvio_populacional=x.std(ddof=0),
                           cv_pct=100*x.std(ddof=0)/x.mean() if x.mean() else float("nan"),
                           mes_max=int(grupo.loc[x.idxmax(), "mes"]), maximo=x.max(),
                           mes_min=int(grupo.loc[x.idxmin(), "mes"]), minimo=x.min()))
    salvar(pd.DataFrame(resumo), "03_estatistica_mensal")
    uf = q(f"SELECT uf, {INDICADORES} FROM carga GROUP BY uf ORDER BY sinistros DESC, uf")
    total = int(anual.sinistros.sum())
    uf["participacao_pct"] = 100 * uf.sinistros / total
    salvar(uf, "04_sinistros_por_uf")
    categorias = ", ".join(f"'{nome}'" for nome in CARGA)
    veiculos = q(f"""
        SELECT categoria, count(*) AS sinistros FROM veiculos JOIN carga USING(id)
        WHERE categoria IN ({categorias}) GROUP BY categoria ORDER BY sinistros DESC
    """)
    veiculos["participacao_pct"] = 100 * veiculos.sinistros / total
    salvar(veiculos, "05_categorias_veiculares")
    salvar(q(f"""
        SELECT quantidade_categorias, count(*) AS sinistros FROM (
            SELECT id, count(*) AS quantidade_categorias FROM veiculos JOIN carga USING(id)
            WHERE categoria IN ({categorias}) GROUP BY id
        ) GROUP BY quantidade_categorias ORDER BY quantidade_categorias
    """), "06_sobreposicao_veicular")
    perfis = {}
    for coluna in ("genero", "idade", "tipo_envolvido", "motorista", "lesao", "equipamento", "alcool"):
        perfil = q(f"SELECT {coluna} AS categoria, sum(pessoas)::BIGINT AS envolvidos FROM vitimas GROUP BY 1 ORDER BY 2 DESC, 1")
        perfil["participacao_pct"] = 100 * perfil.envolvidos / perfil.envolvidos.sum()
        perfis[coluna] = salvar(perfil, f"07_perfil_{coluna}")

    cobertura = q(f"""
        WITH v AS (
          SELECT id, count(*) FILTER (WHERE categoria <> 'NAO INFORMADO') AS outros_tipos,
            count(*) FILTER (WHERE categoria {INFORMADO}) AS tipos_informados
          FROM veiculos GROUP BY id
        )
        SELECT a.ano, a.uf, count(*) AS geral, count(v.id) AS com_correspondencia,
          count(*) FILTER (WHERE v.id IS NULL) AS sem_correspondencia,
          count(*) FILTER (WHERE v.outros_tipos = 0) AS somente_nao_informado_ou_vazio,
          count(*) FILTER (WHERE v.tipos_informados = 0) AS somente_tipo_ausente_amplo,
          count(c.id) AS carga, count(*) FILTER (WHERE c.obitos > 0) AS carga_com_obito,
          count(*) FILTER (WHERE a.obitos > 0) AS geral_com_obito
        FROM acidentes a LEFT JOIN v USING(id) LEFT JOIN carga c USING(id)
        GROUP BY a.ano, a.uf ORDER BY a.uf, a.ano
    """)
    salvar(cobertura, "08_validacao_cobertura_uf")
    salvar(cobertura[cobertura.uf == "RJ"], "09_validacao_rj")
    salvar(q("""
        SELECT a.ano, a.mes, count(*) AS sinistros_gerais, count(c.id) AS sinistros_carga
        FROM acidentes a LEFT JOIN carga c USING(id)
        GROUP BY a.ano, a.mes ORDER BY a.ano, a.mes
    """), "16_cobertura_mensal")
    salvar(q("""
        WITH v AS (
          SELECT id, sum(pessoas) AS pessoas, sum(obitos) AS obitos FROM vitimas GROUP BY id
        )
        SELECT count(*) AS sinistros_carga,
          count(*) FILTER (WHERE data IS NULL OR year(data) != ano OR month(data) != mes) AS datas_inconsistentes,
          count(*) FILTER (WHERE v.id IS NULL) AS sem_vitimas,
          count(*) FILTER (WHERE c.envolvidos IS DISTINCT FROM v.pessoas) AS divergencias_envolvidos,
          count(*) FILTER (WHERE c.obitos IS DISTINCT FROM v.obitos) AS divergencias_obitos,
          sum(c.obitos)::BIGINT AS obitos_acidentes, sum(v.obitos)::BIGINT AS obitos_vitimas,
          (SELECT sum(pessoas)::BIGINT FROM vitimas WHERE lesao = 'OBITO') AS pessoas_classificadas_como_obito
        FROM carga c LEFT JOIN v USING(id)
    """), "10_validacao_acidentes_vitimas")
    tipos = q(f"SELECT tipo_sinistro, {INDICADORES} FROM carga GROUP BY 1 ORDER BY 2 DESC, 1")
    salvar(tipos, "11_todos_tipos_sinistro")
    return anual, mensal, uf, veiculos, perfis, tipos, cobertura


def classes(matriz):
    colunas = ["sinistros", "severidade_fatal_pct"]
    cortes = matriz[colunas].quantile([.25, .50, .75], interpolation="linear")
    for medida, classe in zip(colunas, ["classe_frequencia", "classe_severidade"]):
        # Valores exatamente iguais ao corte ficam na classe inferior.
        matriz[classe] = 1 + sum((matriz[medida] > corte).astype(int) for corte in cortes[medida])
    matriz["indice_ordinal"] = matriz.classe_frequencia * matriz.classe_severidade
    return cortes


def matriz_risco(tipos, total):
    ausentes = ["NAO INFORMADO", "DESCONHECIDO", "IGNORADO"]
    matriz = tipos.loc[~tipos.tipo_sinistro.isin(ausentes)].copy()
    if matriz.empty:
        raise ValueError("Nenhum tipo de sinistro informado para construir a matriz.")
    matriz["codigo"] = [f"T{i:02d}" for i in range(1, len(matriz)+1)]
    matriz["participacao_total_pct"] = 100 * matriz.sinistros / total
    p = matriz.sinistros_com_obitos / matriz.sinistros
    matriz["severidade_fatal_pct"] = 100 * p
    # IC de Wilson de 95%. Expressa a incerteza binomial, sem corrigir a cobertura da base.
    z = 1.959963984540054
    n = matriz.sinistros
    denominador = 1 + z*z/n
    centro = (p + z*z/(2*n)) / denominador
    margem = z * (p*(1-p)/n + z*z/(4*n*n))**.5 / denominador
    matriz["ic95_inferior_pct"] = 100 * (centro-margem).clip(lower=0)
    matriz["ic95_superior_pct"] = 100 * (centro+margem).clip(upper=1)
    cortes = classes(matriz)
    salvar(cortes.rename_axis("quantil").reset_index(), "12_cortes_quartis")
    salvar(matriz, "13_matriz_risco_indicadores")
    salvar(matriz.sort_values(["indice_ordinal", "sinistros"], ascending=False), "14_priorizacao")
    sens = matriz[matriz.sinistros > 2].copy()
    if not sens.empty:
        classes(sens)
        comparar = matriz[["tipo_sinistro", "classe_frequencia", "classe_severidade", "indice_ordinal"]].merge(
            sens[["tipo_sinistro", "classe_frequencia", "classe_severidade", "indice_ordinal"]],
            on="tipo_sinistro", suffixes=("_original", "_sem_categorias_ate_2"))
        comparar["mudou_de_celula"] = (
            (comparar.classe_frequencia_original != comparar.classe_frequencia_sem_categorias_ate_2)
            | (comparar.classe_severidade_original != comparar.classe_severidade_sem_categorias_ate_2)
        )
        salvar(comparar, "15_sensibilidade_matriz")
    return matriz


def figura(fig, nome):
    fig.savefig(SAIDA / f"{nome}.png", dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def barras_h(dados, valor, titulo, eixo, nome, percentual=False):
    fig, ax = plt.subplots(figsize=(10, max(3.3, .29*len(dados)+1.4)))
    y = range(len(dados))
    barras = ax.barh(y, dados[valor], color=CORES[0])
    ax.set_yticks(list(y), dados.categoria)
    ax.invert_yaxis()
    ax.set_xlabel(eixo)
    ax.set_title(titulo)
    ax.margins(x=.20)
    if percentual:
        textos = [f"{x:.2f}%".replace(".", ",") for x in dados[valor]]
    elif "participacao_pct" in dados:
        textos = [f"{int(n):,}".replace(",", ".") + f" ({p:.2f}%)".replace(".", ",")
                  for n, p in zip(dados[valor], dados.participacao_pct)]
    else:
        textos = [f"{int(x):,}".replace(",", ".") for x in dados[valor]]
    ax.bar_label(barras, labels=textos, padding=4, fontsize=8)
    ax.grid(axis="x", alpha=.20)
    ax.set_axisbelow(True)
    figura(fig, nome)


def graficos(anual, mensal, uf, veiculos, perfis, matriz):
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.formatter.use_locale": False})
    barras_h(uf.rename(columns={"uf": "categoria"}), "sinistros", "Sinistros envolvendo veículos de carga por UF",
             "Sinistros selecionados (2023–2025)", "grafico_01_distribuicao_uf")
    fig, eixos = plt.subplots(1, 3, figsize=(12, 4), layout="constrained")
    for ax, campo, titulo in zip(eixos, ["sinistros", "sinistros_com_obitos", "obitos"],
                                 ["Sinistros selecionados", "Sinistros com óbitos", "Óbitos de todos os envolvidos"]):
        b = ax.bar(anual.ano.astype(str), anual[campo], color=CORES)
        ax.bar_label(b, labels=[f"{int(v):,}".replace(",", ".") for v in anual[campo]], padding=3)
        ax.margins(y=.15)
        ax.set_title(titulo)
    figura(fig, "grafico_02_evolucao_anual")
    fig, ax = plt.subplots(figsize=(7, 4))
    p = anual.proporcao_com_obitos_pct
    b = ax.bar(anual.ano.astype(str), p, color=CORES)
    ax.bar_label(b, labels=[f"{x:.2f}%".replace(".", ",") for x in p], padding=3)
    ax.set_ylabel("Sinistros com pelo menos um óbito (%)")
    ax.margins(y=.2)
    figura(fig, "grafico_03_proporcao_fatal")
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 6), layout="constrained")
    for ax, coluna, titulo, cor in zip(axes, ["sinistros", "obitos"], ["Sinistros", "Óbitos"], CORES):
        ax.plot(range(len(mensal)), mensal[coluna], ".-", color=cor)
        ax.set_ylabel(titulo)
        ax.grid(alpha=.2)
    posicoes = list(range(0, len(mensal), 3))
    axes[-1].set_xticks(posicoes, [f"{MESES[int(mensal.iloc[i].mes)-1]}/{mensal.iloc[i].ano:.0f}" for i in posicoes])
    figura(fig, "grafico_04_serie_mensal")
    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    for (ano, sub), cor in zip(mensal.groupby("ano"), CORES):
        ax.plot(sub.mes, sub.sinistros, "o-", label=ano, color=cor)
    ax.set_xticks(range(1, 13), MESES)
    ax.set_ylabel("Sinistros selecionados")
    ax.legend(title="Ano")
    ax.grid(alpha=.2)
    figura(fig, "grafico_05_comparacao_meses")
    barras_h(veiculos, "sinistros", "Participação das categorias de veículos", "Sinistros com a categoria",
             "grafico_06_categorias_veiculares")
    for numero, chave, titulo in [(7, "genero", "Gênero dos envolvidos"), (8, "idade", "Faixa etária dos envolvidos"),
                                   (9, "lesao", "Gravidade da lesão dos envolvidos")]:
        barras_h(perfis[chave], "participacao_pct", titulo, "Participação no total de envolvidos (%)",
                 f"grafico_{numero:02d}_{chave}", percentual=True)

    fig, (ax, legenda) = plt.subplots(1, 2, figsize=(12, 5.8), width_ratios=[1, 1.1], layout="constrained")
    ax.imshow([[f*s for s in range(1, 5)] for f in range(1, 5)], origin="lower", cmap="YlOrRd", alpha=.68)
    for f in range(1, 5):
        for s in range(1, 5):
            codigos = matriz.loc[(matriz.classe_frequencia == f) & (matriz.classe_severidade == s), "codigo"].tolist()
            linhas = [", ".join(codigos[i:i+2]) for i in range(0, len(codigos), 2)]
            ax.text(s-1, f-1, "\n".join(linhas) or "–", ha="center", va="center", fontsize=9)
    ax.set_xticks(range(4), [f"S{i}" for i in range(1, 5)])
    ax.set_yticks(range(4), [f"F{i}" for i in range(1, 5)])
    ax.set_xlabel("Classe de severidade fatal")
    ax.set_ylabel("Classe de frequência")
    ax.set_title("Matriz de priorização relativa")
    legenda.axis("off")
    legenda.text(0, .96, "Tipos de sinistro", weight="bold", transform=legenda.transAxes)
    for i, row in enumerate(matriz.itertuples(index=False)):
        legenda.text(0, .89-i*.84/max(len(matriz)-1, 1), f"{row.codigo} — {row.tipo_sinistro.capitalize()}",
                     fontsize=8, transform=legenda.transAxes)
    figura(fig, "figura_matriz_de_risco")


def main():
    SAIDA.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="duckdb_", dir=SAIDA) as temporarios, duckdb.connect() as con:
        con.execute("SET memory_limit = '2GB'")
        con.execute("SET threads = 4")
        con.execute("SET preserve_insertion_order = false")
        con.execute("SET temp_directory = ?", [temporarios])
        preparar(con)
        print("Calculando indicadores e verificações...", flush=True)
        anual, mensal, uf, veiculos, perfis, tipos, cobertura = tabelas(con)
    total = int(anual.sinistros.sum())
    matriz = matriz_risco(tipos, total)
    graficos(anual, mensal, uf, veiculos, perfis, matriz)
    pessoas = int(perfis["genero"].envolvidos.sum())
    fatais = int(anual.sinistros_com_obitos.sum())
    mortos = int(anual.obitos.sum())
    resumo = (
        "RENAEST | extração de maio de 2026 | ocorrências de 2023 a 2025\n"
        f"Sinistros de carga: {total}\nEnvolvidos: {pessoas}\n"
        f"Sinistros com pelo menos um óbito: {fatais}\nÓbitos: {mortos}\n"
        f"Sinistros na matriz: {matriz.sinistros.sum()}\n"
        f"Excluídos apenas da matriz por tipo ausente: {total-matriz.sinistros.sum()}\n"
        f"Base geral sem correspondência veicular: {cobertura.sem_correspondencia.sum()}\n"
        f"Base geral com correspondência e somente tipo não informado/vazio: {cobertura.somente_nao_informado_ou_vazio.sum()}\n"
        f"Mesmo indicador incluindo desconhecido/ignorado: {cobertura.somente_tipo_ausente_amplo.sum()}\n\n"
        + anual.to_string(index=False) + "\n\n"
        + "A matriz compara frequências e proporções observadas por tipo. "
          "O índice é ordinal e não estima risco por viagem, causalidade ou efeito de medidas preventivas.\n"
    )
    if (total, pessoas, fatais, mortos, int(matriz.sinistros.sum())) != (267751, 415853, 7672, 8613, 210344):
        resumo += "\nATENÇÃO: os totais calculados diferem da versão do artigo. Confira a extração e as tabelas de validação.\n"
    (SAIDA / "resumo_defesa.txt").write_text(resumo, encoding="utf-8")
    print(resumo)
    print(f"Resultados salvos em: {SAIDA}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, duckdb.Error) as erro:
        sys.exit(f"Não foi possível concluir: {erro}")
