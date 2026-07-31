# handlers/dashboard.py
import logging
import io
import os
import numpy as np
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext

import database

logger = logging.getLogger(__name__)
router = Router()

# ─── Constantes ───────────────────────────────────────────────────────────────

MESES_PT = [
    "", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
    "Jul", "Ago", "Set", "Out", "Nov", "Dez"
]

MESES_PT_FULL = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]

PALETTE = [
    "#4C9BE8", "#E8834C", "#4CE8A0", "#E84C6B", "#A04CE8",
    "#E8D44C", "#4CE8D4", "#E84CA0", "#7BE84C", "#4C6BE8",
]

PROJECTION_COLOR = "#888888"
OUTLIER_MARKER_COLOR = "#FFD700"

# ─── Helpers de data ──────────────────────────────────────────────────────────

def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except Exception:
            return None
    return None


def _ref_date(item: dict) -> date | None:
    """Mesma regra de pertencimento usada no relatório mensal."""
    for key in ("data_vencimento", "vencimento", "data_venc", "venc", "vencimento_parcela"):
        d = _to_date(item.get(key))
        if d:
            return d
    for key in ("data_pagamento", "data_pagamento_date", "pagamento", "data_pago"):
        d = _to_date(item.get(key))
        if d:
            return d
    for key in ("data_transacao", "transacao", "data_transacao_date", "data"):
        d = _to_date(item.get(key))
        if d:
            return d
    return None


# ─── Coleta e agregação de dados ──────────────────────────────────────────────

async def _get_trend_data(user_id: str, months_back: int) -> dict:
    """
    Retorna um dict:
      {
        "months": [(ano, mes), ...],          # ordenado do mais antigo ao mais recente
        "by_category": {
          "Categoria": {
            (ano, mes): float,                # valor real (já aplicado 50% para 'ambos')
            ...
          }
        },
        "outliers": {
          "Categoria": {(ano, mes): True, ...}  # meses identificados como outlier
        }
      }
    """
    hoje = date.today()
    # período de coleta
    start = hoje - relativedelta(months=months_back - 1)
    start = date(start.year, start.month, 1)

    # buscar todas as transações de despesa do usuário no período
    async with database.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT *
            FROM transacoes
            WHERE tipo = 'despesa'
              AND (
                telegram_user_id = $1
                OR escopo = 'ambos'
              )
              AND COALESCE(data_vencimento, data_transacao) >= $2
        """, str(user_id), start)

    items = [dict(r) for r in rows]

    # montar lista de meses do período
    months = []
    cur = date(start.year, start.month, 1)
    while cur <= date(hoje.year, hoje.month, 1):
        months.append((cur.year, cur.month))
        cur += relativedelta(months=1)

    # agregar por categoria e mês
    by_category: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))

    for item in items:
        d = _ref_date(item)
        if not d:
            continue
        key = (d.year, d.month)
        if key not in months:
            continue

        cat = (item.get("categoria_text") or "Outros").strip().title()
        val = float(item.get("valor") or 0.0)

        # aplicar regra de 50% para despesas compartilhadas
        if item.get("escopo") == "ambos":
            val *= 0.5

        by_category[cat][key] += val

    # garantir que todos os meses existam para cada categoria (com 0 se não houver gasto)
    for cat in by_category:
        for m in months:
            if m not in by_category[cat]:
                by_category[cat][m] = 0.0

    # detectar outliers por categoria usando IQR
    outliers: dict[str, dict[tuple, bool]] = defaultdict(dict)
    for cat, month_vals in by_category.items():
        values = [v for v in month_vals.values() if v > 0]
        if len(values) < 3:
            continue  # poucos dados, não detectar outlier
        q1 = float(np.percentile(values, 25))
        q3 = float(np.percentile(values, 75))
        iqr = q3 - q1
        upper_fence = q3 + 1.5 * iqr
        for m, v in month_vals.items():
            if v > upper_fence and iqr > 0:
                outliers[cat][m] = True

    return {
        "months": months,
        "by_category": {k: dict(v) for k, v in by_category.items()},
        "outliers": {k: dict(v) for k, v in outliers.items()},
    }


def _project_next_months(
    months: list[tuple],
    month_vals: dict[tuple, float],
    outliers_cat: dict[tuple, bool],
    n_proj: int = 3,
) -> list[float]:
    """
    Projeta os próximos n_proj meses para uma categoria.

    Estratégia:
      1. Remove outliers dos dados históricos.
      2. Calcula a mediana dos valores não-outlier (base robusta).
      3. Aplica uma tendência linear leve (regressão simples) sobre os dados limpos
         para capturar se os gastos estão subindo ou caindo.
      4. Retorna a projeção como mediana + ajuste de tendência por mês futuro.
    """
    # dados limpos (sem outliers e sem zeros que representem meses sem gasto)
    clean = [
        (i, month_vals.get(m, 0.0))
        for i, m in enumerate(months)
        if not outliers_cat.get(m, False)
    ]

    non_zero_clean = [(i, v) for i, v in clean if v > 0]

    if not non_zero_clean:
        return [0.0] * n_proj

    values_clean = [v for _, v in non_zero_clean]
    median_base = float(np.median(values_clean))

    # tendência linear simples (slope) sobre os dados limpos
    if len(non_zero_clean) >= 3:
        xs = np.array([i for i, _ in non_zero_clean], dtype=float)
        ys = np.array([v for _, v in non_zero_clean], dtype=float)
        # normalizar xs para evitar extrapolação exagerada
        xs_norm = xs - xs.mean()
        slope = float(np.polyfit(xs_norm, ys, 1)[0])
        # limitar slope a ±20% da mediana por mês para não extrapolar demais
        max_slope = median_base * 0.20
        slope = max(min(slope, max_slope), -max_slope)
    else:
        slope = 0.0

    last_idx = len(months) - 1
    projections = []
    for step in range(1, n_proj + 1):
        proj = median_base + slope * step
        proj = max(proj, 0.0)  # não projetar valores negativos
        projections.append(round(proj, 2))

    return projections


# ─── Geração do gráfico ───────────────────────────────────────────────────────

def _build_chart(trend_data: dict, months_back: int, user_label: str = "") -> tuple[bytes, str]:
    """
    Gera o gráfico de tendência por categoria.
    Retorna (png_bytes, html_str).
    Usa matplotlib para PNG (sem dependência de Chrome/kaleido).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import io

    MESES_PT_ABREV = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                      "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    historico = trend_data.get("historico", {})
    projecao  = trend_data.get("projecao", {})
    outliers  = trend_data.get("outliers", {})
    meses_hist = trend_data.get("meses_historico", [])
    meses_proj = trend_data.get("meses_projecao", [])

    if not historico:
        raise RuntimeError("Sem dados suficientes para gerar o gráfico.")

    # ── Labels ──────────────────────────────────────────────────────────────
    def _label(ym):
        return f"{MESES_PT_ABREV[ym[1]]}/{str(ym[0])[2:]}"

    labels_hist = [_label(m) for m in meses_hist]
    labels_proj = [_label(m) for m in meses_proj]
    all_labels   = labels_hist + labels_proj
    n_hist = len(labels_hist)
    n_proj = len(labels_proj)
    x_hist = list(range(n_hist))
    x_proj = list(range(n_hist, n_hist + n_proj))

    # ── Cores ────────────────────────────────────────────────────────────────
    PALETTE = [
        "#7C83FD", "#FD7C83", "#7CFD9A", "#FDD97C",
        "#C47CFD", "#7CDFFD", "#FD9E7C", "#B0FD7C",
    ]

    # ── Figura ───────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor("#1E1E2E")
    ax.set_facecolor("#1E1E2E")

    # Área de separação histórico / projeção
    if x_proj:
        ax.axvspan(n_hist - 0.5, n_hist + n_proj - 0.5, alpha=0.08, color="#FFFFFF")
        ax.axvline(x=n_hist - 0.5, color="#AAAAAA", linestyle="--", linewidth=1, alpha=0.5)
        ax.text(n_hist - 0.5 + 0.05, ax.get_ylim()[1] if ax.get_ylim()[1] != 1.0 else 1,
                "  Projeção →", color="#AAAAAA", fontsize=9, va="top")

    legend_patches = []
    for idx, (cat, valores) in enumerate(historico.items()):
        cor = PALETTE[idx % len(PALETTE)]

        # Histórico
        y_hist = [valores.get(m, 0) for m in meses_hist]
        ax.plot(x_hist, y_hist, color=cor, linewidth=2, marker="o", markersize=5)

        # Outliers com estrela
        cat_outliers = outliers.get(cat, [])
        for i, m in enumerate(meses_hist):
            if m in cat_outliers:
                ax.plot(x_hist[i], y_hist[i], marker="*", color="#FFD700",
                        markersize=14, zorder=5)

        # Projeção (tracejado)
        if cat in projecao and x_proj:
            y_proj = [projecao[cat].get(m, 0) for m in meses_proj]
            ax.plot([x_hist[-1]] + x_proj,
                    [y_hist[-1]] + y_proj,
                    color=cor, linewidth=1.5, linestyle="--", alpha=0.7)

        legend_patches.append(mpatches.Patch(color=cor, label=cat.title()))

    # ── Eixos e grade ────────────────────────────────────────────────────────
    ax.set_xticks(list(range(len(all_labels))))
    ax.set_xticklabels(all_labels, rotation=30, ha="right", color="#CCCCCC", fontsize=9)
    ax.yaxis.set_tick_params(labelcolor="#CCCCCC")
    ax.tick_params(colors="#CCCCCC")
    ax.grid(axis="y", color="#333355", linewidth=0.5, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333355")

    # ── Título e legenda ─────────────────────────────────────────────────────
    titulo = f"Tendência por Categoria — últimos {months_back} meses"
    if user_label:
        titulo += f"  |  {user_label}"
    ax.set_title(titulo, color="#FFFFFF", fontsize=13, pad=14)
    ax.legend(handles=legend_patches, loc="upper left",
              facecolor="#2A2A3E", edgecolor="#555577",
              labelcolor="#CCCCCC", fontsize=9)

    # Nota de rodapé
    fig.text(0.01, 0.01, "⭐ = gasto sazonal/excepcional (excluído da projeção)",
             color="#AAAAAA", fontsize=8)

    plt.tight_layout()

    # ── Exportar PNG ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    png_bytes = buf.read()

    # ── HTML interativo (plotly) ──────────────────────────────────────────────
    try:
        import plotly.graph_objs as go

        fig_plotly = go.Figure()
        for idx, (cat, valores) in enumerate(historico.items()):
            cor = PALETTE[idx % len(PALETTE)]
            y_hist = [valores.get(m, 0) for m in meses_hist]
            fig_plotly.add_trace(go.Scatter(
                x=labels_hist, y=y_hist, name=cat.title(),
                mode="lines+markers", line=dict(color=cor, width=2)
            ))
            if cat in projecao and labels_proj:
                y_proj = [projecao[cat].get(m, 0) for m in meses_proj]
                fig_plotly.add_trace(go.Scatter(
                    x=[labels_hist[-1]] + labels_proj,
                    y=[y_hist[-1]] + y_proj,
                    name=f"{cat.title()} (proj.)",
                    mode="lines", line=dict(color=cor, width=1.5, dash="dash"),
                    showlegend=False
                ))

        fig_plotly.update_layout(
            template="plotly_dark",
            title=titulo,
            paper_bgcolor="#1E1E2E",
            plot_bgcolor="#1E1E2E",
        )
        html_str = fig_plotly.to_html(full_html=True, include_plotlyjs="cdn")
    except Exception:
        html_str = "<html><body><p>HTML interativo indisponível.</p></body></html>"

    return png_bytes, html_str


def _build_html(trend_data: dict, months_back: int) -> str:
    """
    Gera o HTML interativo (string) para envio como documento.
    Reutiliza a mesma figura do gráfico, mas com write_html.
    """
    try:
        import plotly.graph_objs as go
        import plotly.io as pio
    except ImportError:
        raise RuntimeError("plotly não instalado.")

    # Recriar a figura (mesma lógica de _build_chart, mas retornando HTML)
    # Para evitar duplicação de código, geramos a figura e exportamos como HTML
    months = trend_data["months"]
    by_category = trend_data["by_category"]
    outliers = trend_data["outliers"]

    hoje = date.today()
    future_months = []
    for step in range(1, 4):
        fm = date(hoje.year, hoje.month, 1) + relativedelta(months=step)
        future_months.append((fm.year, fm.month))

    all_months = months + future_months
    x_labels = [f"{MESES_PT[m]}/{str(a)[2:]}" for a, m in all_months]
    n_hist = len(months)

    cat_totals = {
        cat: sum(v for v in vals.values())
        for cat, vals in by_category.items()
    }
    sorted_cats = sorted(cat_totals, key=lambda c: -cat_totals[c])
    top_cats = sorted_cats[:8]

    fig = go.Figure()

    fig.add_vrect(
        x0=x_labels[n_hist - 1],
        x1=x_labels[-1],
        fillcolor="rgba(200,200,200,0.10)",
        line_width=0,
        annotation_text="Projeção",
        annotation_position="top left",
        annotation_font_color="#888888",
        annotation_font_size=12,
    )

    for idx, cat in enumerate(top_cats):
        color = PALETTE[idx % len(PALETTE)]
        vals = by_category[cat]
        outliers_cat = outliers.get(cat, {})
        y_hist = [vals.get(m, 0.0) for m in months]
        y_proj = _project_next_months(months, vals, outliers_cat, n_proj=3)

        fig.add_trace(go.Scatter(
            x=x_labels[:n_hist], y=y_hist,
            mode="lines+markers", name=cat,
            line=dict(color=color, width=2.5),
            marker=dict(size=7, color=color),
            legendgroup=cat,
            hovertemplate=f"<b>{cat}</b><br>%{{x}}<br>R$ %{{y:,.2f}}<extra></extra>",
        ))

        outlier_x = [x_labels[i] for i, m in enumerate(months) if outliers_cat.get(m)]
        outlier_y = [vals.get(m, 0.0) for m in months if outliers_cat.get(m)]
        if outlier_x:
            fig.add_trace(go.Scatter(
                x=outlier_x, y=outlier_y,
                mode="markers", name=f"{cat} (exceção)",
                marker=dict(size=14, color=OUTLIER_MARKER_COLOR, symbol="star",
                            line=dict(color=color, width=1.5)),
                legendgroup=cat, showlegend=True,
                hovertemplate=f"<b>{cat}</b> ⚠️ Gasto excepcional<br>%{{x}}<br>R$ %{{y:,.2f}}<extra></extra>",
            ))

        proj_x = [x_labels[n_hist - 1]] + [x_labels[n_hist + i] for i in range(3)]
        proj_y = [y_hist[-1]] + y_proj
        fig.add_trace(go.Scatter(
            x=proj_x, y=proj_y,
            mode="lines+markers", name=f"{cat} (proj.)",
            line=dict(color=color, width=2, dash="dot"),
            marker=dict(size=6, color=color, symbol="circle-open"),
            legendgroup=cat, showlegend=False,
            hovertemplate=f"<b>{cat}</b> 📈 Projeção<br>%{{x}}<br>R$ %{{y:,.2f}}<extra></extra>",
        ))

    total_periodo = sum(sum(v.values()) for v in by_category.values())
    media_mensal = total_periodo / len(months) if months else 0
    maior_cat = top_cats[0] if top_cats else "-"
    maior_cat_val = cat_totals.get(maior_cat, 0)

    month_totals = defaultdict(float)
    for cat, vals in by_category.items():
        for m, v in vals.items():
            month_totals[m] += v
    if month_totals:
        pior_mes = max(month_totals, key=lambda m: month_totals[m])
        pior_mes_label = f"{MESES_PT_FULL[pior_mes[1]]}/{pior_mes[0]}"
        pior_mes_val = month_totals[pior_mes]
    else:
        pior_mes_label = "-"
        pior_mes_val = 0

    def fmt_brl(v):
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    fig.update_layout(
        title=dict(
            text=(
                f"<b>📈 Tendência de Gastos por Categoria</b><br>"
                f"<span style='font-size:13px;color:#888'>Últimos {months_back} meses + projeção 3 meses</span>"
            ),
            font=dict(size=22, color="#FFFFFF"),
            x=0.5, xanchor="center", y=0.97,
        ),
        paper_bgcolor="#1E1E2E",
        plot_bgcolor="#1E1E2E",
        font=dict(color="#CCCCCC", family="Segoe UI, Arial"),
        legend=dict(
            bgcolor="rgba(30,30,46,0.85)", bordercolor="#444", borderwidth=1,
            font=dict(size=12), orientation="h",
            yanchor="bottom", y=-0.28, xanchor="center", x=0.5,
        ),
        xaxis=dict(gridcolor="#2E2E3E", linecolor="#444", tickfont=dict(size=12), tickangle=-30),
        yaxis=dict(gridcolor="#2E2E3E", linecolor="#444", tickprefix="R$ ",
                   tickfont=dict(size=12), tickformat=",.0f"),
        margin=dict(t=160, b=180, l=90, r=50),
        width=1200, height=680,
        annotations=[
            dict(x=0.0, y=1.17, xref="paper", yref="paper",
                 text=(f"<b style='font-size:12px;color:#888'>💸 TOTAL NO PERÍODO</b><br>"
                       f"<b style='font-size:18px;color:#4C9BE8'>{fmt_brl(total_periodo)}</b>"),
                 showarrow=False, align="left",
                 bgcolor="rgba(76,155,232,0.12)", bordercolor="#4C9BE8", borderwidth=1, borderpad=10),
            dict(x=0.26, y=1.17, xref="paper", yref="paper",
                 text=(f"<b style='font-size:12px;color:#888'>📊 MÉDIA MENSAL</b><br>"
                       f"<b style='font-size:18px;color:#4CE8A0'>{fmt_brl(media_mensal)}</b>"),
                 showarrow=False, align="left",
                 bgcolor="rgba(76,232,160,0.12)", bordercolor="#4CE8A0", borderwidth=1, borderpad=10),
            dict(x=0.52, y=1.17, xref="paper", yref="paper",
                 text=(f"<b style='font-size:12px;color:#888'>🏷️ MAIOR CATEGORIA</b><br>"
                       f"<b style='font-size:18px;color:#E8834C'>{maior_cat}</b>"
                       f"<span style='font-size:13px;color:#888'> {fmt_brl(maior_cat_val)}</span>"),
                 showarrow=False, align="left",
                 bgcolor="rgba(232,131,76,0.12)", bordercolor="#E8834C", borderwidth=1, borderpad=10),
            dict(x=0.78, y=1.17, xref="paper", yref="paper",
                 text=(f"<b style='font-size:12px;color:#888'>📅 MÊS MAIS CARO</b><br>"
                       f"<b style='font-size:18px;color:#E84C6B'>{pior_mes_label}</b>"
                       f"<span style='font-size:13px;color:#888'> {fmt_brl(pior_mes_val)}</span>"),
                 showarrow=False, align="left",
                 bgcolor="rgba(232,76,107,0.12)", bordercolor="#E84C6B", borderwidth=1, borderpad=10),
            dict(x=0.5, y=-0.40, xref="paper", yref="paper",
                 text="⭐ Gastos excepcionais (outliers) detectados automaticamente e excluídos da projeção",
                 showarrow=False, align="center", font=dict(size=11, color="#888888")),
        ],
    )

    html_str = pio.to_html(fig, full_html=True, include_plotlyjs="cdn")
    return html_str


# ─── Handlers ─────────────────────────────────────────────────────────────────

def _period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="3 meses", callback_data="trend:3"),
            InlineKeyboardButton(text="6 meses", callback_data="trend:6"),
            InlineKeyboardButton(text="12 meses", callback_data="trend:12"),
        ]
    ])


@router.message(F.text == "📈 Tendência")
async def open_trend_menu(message: Message):
    await message.answer(
        "📈 *Tendência de Gastos por Categoria*\n\n"
        "Selecione o período de análise:",
        parse_mode="Markdown",
        reply_markup=_period_keyboard(),
    )


@router.callback_query(F.data.startswith("trend:"))
async def handle_trend_period(callback: CallbackQuery):
    _, months_str = callback.data.split(":")
    months_back = int(months_str)
    user_id = str(callback.from_user.id)

    await callback.message.answer(
        f"⏳ Gerando análise dos últimos *{months_back} meses*...",
        parse_mode="Markdown",
    )
    await callback.answer()

    try:
        trend_data = await _get_trend_data(user_id, months_back)

        if not trend_data["by_category"]:
            await callback.message.answer(
                "📭 Nenhuma despesa encontrada no período selecionado.",
                parse_mode="Markdown",
            )
            return

        # ── Gerar PNG ──
        img_bytes = _build_chart(trend_data, months_back, user_label=callback.from_user.first_name or "")
        photo = BufferedInputFile(img_bytes, filename="tendencia.png")
        await callback.message.answer_photo(
            photo=photo,
            caption=(
                f"📈 *Tendência — últimos {months_back} meses*\n"
                f"Linhas tracejadas = projeção dos próximos 3 meses\n"
                f"⭐ = gasto excepcional excluído da projeção"
            ),
            parse_mode="Markdown",
        )

        # ── Gerar HTML interativo ──
        html_str = _build_html(trend_data, months_back)
        html_bytes = html_str.encode("utf-8")
        html_file = BufferedInputFile(html_bytes, filename=f"tendencia_{months_back}m.html")
        await callback.message.answer_document(
            document=html_file,
            caption="📊 Versão interativa — abra no navegador para explorar os dados.",
        )

        # ── Resumo textual de outliers detectados ──
        outlier_lines = []
        for cat, out_months in trend_data["outliers"].items():
            for (ano, mes) in out_months:
                val = trend_data["by_category"][cat].get((ano, mes), 0)
                outlier_lines.append(
                    f"  ⭐ *{cat}* em {MESES_PT_FULL[mes]}/{ano}: "
                    f"_{_fmt_brl(val)}_ _(gasto excepcional — excluído da projeção)_"
                )

        if outlier_lines:
            msg = "🔍 *Gastos excepcionais detectados:*\n\n" + "\n".join(outlier_lines)
            await callback.message.answer(msg, parse_mode="Markdown")

    except RuntimeError as e:
        logger.exception("Erro ao gerar dashboard de tendência")
        await callback.message.answer(
            f"❌ Erro ao gerar o gráfico:\n`{e}`",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Erro inesperado no dashboard de tendência")
        await callback.message.answer(
            "❌ Erro inesperado ao gerar o relatório. Tente novamente.",
        )


def _fmt_brl(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")