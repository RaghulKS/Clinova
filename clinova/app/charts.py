"""Plotly figures for the app. Pure functions of contract models; one accent colour plus greys."""

from __future__ import annotations

import plotly.graph_objects as go

from clinova.app import theme
from clinova.contracts import Funnel, ReplayResult, SimRunSummary, ValidationMetrics

_FONT = dict(family="Inter, -apple-system, Segoe UI, Roboto, sans-serif", size=13, color=theme.INK)


def _base(fig: go.Figure, height: int, **layout) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=_FONT,
        **layout,
    )
    return fig


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def funnel_steps(funnel: Funnel, sims: list[SimRunSummary]) -> tuple[go.Figure, list[tuple[str, int]]]:
    """Screened -> eligible -> enrolled -> completing. Enrolled and completing scale the simulated
    enrollment and completion rates to all eligible patients (an estimate, not a count)."""
    n_elig = funnel.n_eligible
    enroll = _mean([s.enroll_rate for s in sims]) if sims else None
    comp = _mean([s.completion_rate for s in sims]) if sims else None
    steps: list[tuple[str, int]] = [("Screened", funnel.n_screened), ("Eligible", n_elig)]
    if enroll is not None and comp is not None:
        enrolled = round(n_elig * enroll)
        steps += [("Would enroll", enrolled), ("Would complete", round(enrolled * comp))]
    fig = go.Figure(
        go.Funnel(
            y=[s[0] for s in steps],
            x=[s[1] for s in steps],
            textinfo="value+percent initial",
            marker=dict(color=[theme.ACCENT, "#3C8F98", "#6AAAB1", theme.ACCENT_MID][: len(steps)]),
            connector=dict(line=dict(color=theme.LINE, width=1)),
        )
    )
    return _base(fig, 250, funnelmode="stack"), steps


def exclusions(funnel: Funnel, top: int = 10) -> go.Figure:
    """Patients failing each rule on its own (independent of rule order). The biggest is the accent."""
    rows = sorted(funnel.marginal, key=lambda m: m.excluded_n, reverse=True)[:top]
    rows = list(reversed(rows))  # plotly draws bottom-up
    colors = [theme.ACCENT if i == len(rows) - 1 else theme.GREY_BAR for i in range(len(rows))]
    fig = go.Figure(
        go.Bar(
            y=[f"{m.rule_id}  {_short(m.label)}" for m in rows],
            x=[m.excluded_n for m in rows],
            orientation="h",
            marker=dict(color=colors),
            text=[f"{m.excluded_n} ({m.excluded_frac:.0%})" for m in rows],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}<br>%{x} patients fail this rule<extra></extra>",
        )
    )
    fig.update_xaxes(visible=False, range=[0, max([m.excluded_n for m in rows] + [1]) * 1.25])
    fig.update_yaxes(automargin=True, tickfont=dict(size=12))
    return _base(fig, max(150, 30 * len(rows) + 20))


def _short(label: str, n: int = 38) -> str:
    # Funnel labels end with "(I12)"; the id is shown separately.
    base = label.rsplit(" (", 1)[0] if label.endswith(")") and " (" in label else label
    return base if len(base) <= n else base[: n - 1] + "..."


def dropout_curve(sim: SimRunSummary) -> go.Figure:
    xs = list(range(1, len(sim.dropout_curve) + 1))
    fig = go.Figure(
        go.Scatter(
            x=xs,
            y=[v * 100 for v in sim.dropout_curve],
            mode="lines+markers",
            line=dict(color=theme.ACCENT, width=2.5, shape="hv"),
            marker=dict(size=6),
            hovertemplate="By visit %{x}: %{y:.0f}% dropped out<extra></extra>",
        )
    )
    fig.update_xaxes(title="Visit", dtick=1, showgrid=False)
    fig.update_yaxes(title="% dropped out", rangemode="tozero", gridcolor=theme.LINE)
    return _base(fig, 200)


def auc_compare(metrics: ValidationMetrics) -> go.Figure:
    fig = go.Figure()
    names = [t.target.value.replace("_", " ") for t in metrics.targets]
    for label, getter, color, off in (
        ("Baseline (logistic)", lambda t: t.baseline_auc, theme.GREY_DARK, -0.12),
        ("Clinova (LightGBM + simulation)", lambda t: t.auc, theme.ACCENT, 0.12),
    ):
        vals = [getter(t) for t in metrics.targets]
        fig.add_trace(
            go.Scatter(
                x=[v.value for v in vals],
                y=[i + off for i in range(len(vals))],
                mode="markers",
                name=label,
                marker=dict(size=11, color=color),
                error_x=dict(
                    type="data",
                    symmetric=False,
                    array=[v.ci_high - v.value for v in vals],
                    arrayminus=[v.value - v.ci_low for v in vals],
                    color=color,
                    thickness=2,
                ),
                hovertemplate="%{x:.2f}<extra>" + label + "</extra>",
            )
        )
    fig.update_yaxes(tickvals=list(range(len(names))), ticktext=names, autorange="reversed")
    fig.update_xaxes(title="ROC AUC on test trials (95% bootstrap interval)", range=[0.4, 1.0], gridcolor=theme.LINE)
    return _base(fig, 120 + 70 * len(names), legend=dict(orientation="h", y=1.18, x=0))


def reliability(metrics: ValidationMetrics, target_index: int = 0) -> go.Figure:
    t = metrics.targets[target_index]
    bins = [b for b in t.reliability if b.mean_pred is not None and b.frac_pos is not None]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(color=theme.GREY_BAR, dash="dash"), name="Perfect", hoverinfo="skip"))
    fig.add_trace(
        go.Scatter(
            x=[b.mean_pred for b in bins],
            y=[b.frac_pos for b in bins],
            mode="lines+markers",
            marker=dict(size=[6 + min(b.n, 200) ** 0.5 for b in bins], color=theme.ACCENT),
            line=dict(color=theme.ACCENT, width=2),
            name="Clinova",
            text=[f"{b.n} trials" for b in bins],
            hovertemplate="predicted %{x:.2f}, observed %{y:.2f}<br>%{text}<extra></extra>",
        )
    )
    fig.update_xaxes(title="Predicted probability", range=[0, 1], gridcolor=theme.LINE)
    fig.update_yaxes(title="Observed rate", range=[0, 1], gridcolor=theme.LINE)
    return _base(fig, 340, showlegend=False)


def hit_rate(metrics: ValidationMetrics) -> go.Figure | None:
    h = metrics.hit_rate
    if h is None:
        return None
    names = ["Clinova top-3 flags", "Largest-exclusion rules", "Random rules"]
    vals = [h.hit_rate, h.marginal_baseline, h.random_baseline]
    fig = go.Figure(
        go.Bar(
            x=names,
            y=[v * 100 for v in vals],
            marker=dict(color=[theme.ACCENT, theme.GREY_BAR, theme.GREY_BAR]),
            text=[f"{v:.0%}" for v in vals],
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.update_yaxes(title="% of trials where a flag hit the rule later widened", range=[0, 100], gridcolor=theme.LINE)
    return _base(fig, 300)


def replay(result: ReplayResult, measure: str = "brier") -> go.Figure:
    fig = go.Figure()
    for arm, color, dash in (("frozen", theme.GREY_DARK, "dash"), ("retrained", theme.ACCENT, "solid")):
        ys = [getattr(getattr(m, arm), measure) for m in result.months]
        fig.add_trace(
            go.Scatter(
                x=[m.month for m in result.months],
                y=ys,
                mode="lines+markers",
                name="Retrained monthly" if arm == "retrained" else f"Frozen at {result.freeze_date}",
                line=dict(color=color, width=2.5, dash=dash),
                connectgaps=False,
            )
        )
    fig.update_xaxes(title="Simulated month", gridcolor=theme.LINE)
    fig.update_yaxes(title="Brier score (lower is better)" if measure == "brier" else "ROC AUC", gridcolor=theme.LINE)
    return _base(fig, 380, legend=dict(orientation="h", y=1.12, x=0))
