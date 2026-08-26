import { useState } from "react";
import { ChartTooltip } from "./ChartTooltip";

export interface BarSeriesSpec {
  key: string;
  label: string;
  colorVar: string;
}

export interface BarGroupDatum {
  label: string;
  values: Record<string, number>;
}

interface BarChartProps {
  groups: BarGroupDatum[];
  series: BarSeriesSpec[];
  stacked?: boolean;
  formatValue?: (v: number) => string;
  // Подпись у линий сетки/оси значений — по умолчанию совпадает с formatValue,
  // но для денег на оси разумнее сократить ("4,5 млн ₽"), см. LineChart.
  formatAxisValue?: (v: number) => string;
  height?: number;
  showLegend?: boolean;
  emptyMessage?: string;
  // "horizontal" — категории по вертикали, значения растут вправо. Читается
  // сильно лучше при длинных названиях категорий (имена коллекций) или когда
  // категорий много — см. «Выручка по коллекциям» на «Отчётах».
  orientation?: "vertical" | "horizontal";
}

const WIDTH = 680;
const SEGMENT_GAP = 2;
const H_ROW_HEIGHT = 34;
const H_MARGIN = { top: 8, right: 20, bottom: 24, left: 148 };
const V_MARGIN = { top: 16, right: 16, bottom: 40, left: 56 };

interface HoverTarget {
  groupIdx: number;
  seriesKey: string;
  x: number;
  y: number;
}

// SVG не переносит и не обрезает текст сам — грубая, но предсказуемая обрезка
// по числу символов (полное название остаётся в <title> — нативный тултип
// при наведении, и в aria-label для скринридеров).
function truncateLabel(label: string, maxChars: number): string {
  return label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label;
}

export function BarChart({
  groups,
  series,
  stacked = true,
  formatValue = String,
  formatAxisValue,
  height = 240,
  showLegend = true,
  emptyMessage = "Нет данных",
  orientation = "vertical",
}: BarChartProps) {
  const [hover, setHover] = useState<HoverTarget | null>(null);
  const formatAxis = formatAxisValue ?? formatValue;

  if (groups.length === 0) {
    return <div className="viz-empty">{emptyMessage}</div>;
  }

  const totals = groups.map((g) =>
    stacked
      ? series.reduce((sum, s) => sum + (g.values[s.key] ?? 0), 0)
      : Math.max(0, ...series.map((s) => g.values[s.key] ?? 0)),
  );
  const maxY = Math.max(1, ...totals);
  const ticks = 4;
  const tickValues = Array.from({ length: ticks + 1 }, (_, i) => (maxY / ticks) * i);

  const hoveredGroup = hover ? groups[hover.groupIdx] : null;
  const hoveredSeries = hover ? series.find((s) => s.key === hover.seriesKey) : null;

  // Высота горизонтальной раскладки считается от числа категорий, а не
  // берётся из пропа height (иначе строки либо слипаются, либо теряются).
  const effectiveHeight =
    orientation === "horizontal"
      ? H_MARGIN.top + H_MARGIN.bottom + groups.length * H_ROW_HEIGHT
      : height;

  const legend = showLegend && series.length > 1 && (
    <div className="viz-legend">
      {series.map((s) => (
        <span key={s.key} className="viz-legend-item">
          <span className="viz-legend-swatch" style={{ background: `var(${s.colorVar})` }} />
          {s.label}
        </span>
      ))}
    </div>
  );

  const tooltip = hover && hoveredGroup && hoveredSeries && (
    <ChartTooltip leftPct={(hover.x / WIDTH) * 100} topPct={(hover.y / effectiveHeight) * 100}>
      <div className="viz-tooltip-label">{hoveredGroup.label}</div>
      <div className="viz-tooltip-row">
        <span
          className="viz-tooltip-key"
          style={{ background: `var(${hoveredSeries.colorVar})` }}
        />
        <span className="viz-tooltip-value">
          {hoveredSeries.label}: {formatValue(hoveredGroup.values[hoveredSeries.key] ?? 0)}
        </span>
      </div>
    </ChartTooltip>
  );

  if (orientation === "horizontal") {
    const innerWidth = WIDTH - H_MARGIN.left - H_MARGIN.right;
    const innerHeight = effectiveHeight - H_MARGIN.top - H_MARGIN.bottom;
    const bandHeight = innerHeight / groups.length;
    const barThickness = Math.max(8, bandHeight * 0.6);
    const axisX = H_MARGIN.left;

    return (
      <div className="viz-root viz-bar-wrapper">
        <svg
          className="viz-svg"
          viewBox={`0 0 ${WIDTH} ${effectiveHeight}`}
          role="img"
          aria-label={`Столбчатая диаграмма (горизонтальная), ${groups.length} категорий, максимум ${formatValue(maxY)}`}
        >
          {tickValues.map((v, i) => {
            const x = axisX + (v / maxY) * innerWidth;
            return (
              <g key={i}>
                <line
                  x1={x}
                  x2={x}
                  y1={H_MARGIN.top}
                  y2={H_MARGIN.top + innerHeight}
                  className="viz-gridline"
                />
                <text x={x} y={effectiveHeight - 6} className="viz-axis-label" textAnchor="middle">
                  {formatAxis(v)}
                </text>
              </g>
            );
          })}
          <line
            x1={axisX}
            x2={axisX}
            y1={H_MARGIN.top}
            y2={H_MARGIN.top + innerHeight}
            className="viz-axis-line"
          />

          {groups.map((g, gi) => {
            const bandY = H_MARGIN.top + gi * bandHeight + (bandHeight - barThickness) / 2;
            const activeSeries = stacked
              ? series.filter((s) => (g.values[s.key] ?? 0) > 0)
              : series.filter((s) => s.key in g.values);
            const subBarThickness = stacked
              ? barThickness
              : barThickness / Math.max(1, activeSeries.length);
            let cumulative = 0;

            return (
              <g key={g.label}>
                {activeSeries.map((s, si) => {
                  const value = g.values[s.key] ?? 0;
                  const segWidth = (value / maxY) * innerWidth;
                  const isHovered = hover?.groupIdx === gi && hover.seriesKey === s.key;

                  const x = axisX + (stacked ? cumulative : 0);
                  const y = bandY + (stacked ? 0 : si * subBarThickness);
                  if (stacked) cumulative += segWidth;

                  const rectY = y + (stacked ? 0 : SEGMENT_GAP / 2);
                  const rectHeight = stacked
                    ? subBarThickness
                    : Math.max(1, subBarThickness - SEGMENT_GAP);
                  const rectX = stacked ? x + SEGMENT_GAP / 2 : x;
                  const rectWidth = stacked ? Math.max(0, segWidth - SEGMENT_GAP) : segWidth;

                  return (
                    <rect
                      key={s.key}
                      x={rectX}
                      y={rectY}
                      width={rectWidth}
                      height={rectHeight}
                      rx={2}
                      fill={`var(${s.colorVar})`}
                      opacity={hover && !isHovered ? 0.85 : 1}
                      tabIndex={0}
                      role="button"
                      aria-label={`${g.label}, ${s.label}: ${formatValue(value)}`}
                      onPointerEnter={() =>
                        setHover({
                          groupIdx: gi,
                          seriesKey: s.key,
                          x: rectX + rectWidth / 2,
                          y: rectY,
                        })
                      }
                      onPointerLeave={() => setHover(null)}
                      onFocus={() =>
                        setHover({
                          groupIdx: gi,
                          seriesKey: s.key,
                          x: rectX + rectWidth / 2,
                          y: rectY,
                        })
                      }
                      onBlur={() => setHover(null)}
                    />
                  );
                })}
                <text
                  x={axisX - 8}
                  y={bandY + barThickness / 2}
                  className="viz-axis-label"
                  textAnchor="end"
                  dominantBaseline="middle"
                >
                  {truncateLabel(g.label, 20)}
                  <title>{g.label}</title>
                </text>
              </g>
            );
          })}
        </svg>
        {tooltip}
        {legend}
      </div>
    );
  }

  // ---------------------------------------------------------------------
  // Вертикальная раскладка (исходное поведение).
  // ---------------------------------------------------------------------
  const innerWidth = WIDTH - V_MARGIN.left - V_MARGIN.right;
  const innerHeight = height - V_MARGIN.top - V_MARGIN.bottom;
  const baselineY = V_MARGIN.top + innerHeight;
  const bandWidth = innerWidth / groups.length;
  const barWidth = Math.max(10, bandWidth * 0.6);

  return (
    <div className="viz-root viz-bar-wrapper">
      <svg
        className="viz-svg"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Столбчатая диаграмма, ${groups.length} категорий, максимум ${formatValue(maxY)}`}
      >
        {tickValues.map((v, i) => {
          const y = baselineY - (v / maxY) * innerHeight;
          return (
            <g key={i}>
              <line
                x1={V_MARGIN.left}
                x2={WIDTH - V_MARGIN.right}
                y1={y}
                y2={y}
                className="viz-gridline"
              />
              <text
                x={V_MARGIN.left - 8}
                y={y}
                className="viz-axis-label"
                textAnchor="end"
                dominantBaseline="middle"
              >
                {formatAxis(v)}
              </text>
            </g>
          );
        })}
        <line
          x1={V_MARGIN.left}
          x2={WIDTH - V_MARGIN.right}
          y1={baselineY}
          y2={baselineY}
          className="viz-axis-line"
        />

        {groups.map((g, gi) => {
          const bandX = V_MARGIN.left + gi * bandWidth + (bandWidth - barWidth) / 2;
          const activeSeries = stacked
            ? series.filter((s) => (g.values[s.key] ?? 0) > 0)
            : series.filter((s) => s.key in g.values);
          const subBarWidth = stacked ? barWidth : barWidth / Math.max(1, activeSeries.length);
          let cumulative = 0;

          return (
            <g key={g.label}>
              {activeSeries.map((s, si) => {
                const value = g.values[s.key] ?? 0;
                const segHeight = (value / maxY) * innerHeight;
                const isHovered = hover?.groupIdx === gi && hover.seriesKey === s.key;

                let x: number;
                let y: number;
                if (stacked) {
                  y = baselineY - cumulative - segHeight;
                  cumulative += segHeight;
                  x = bandX;
                } else {
                  x = bandX + si * subBarWidth;
                  y = baselineY - segHeight;
                }

                const rectX = x + (stacked ? 0 : SEGMENT_GAP / 2);
                const rectWidth = stacked ? subBarWidth : Math.max(1, subBarWidth - SEGMENT_GAP);
                const rectY = stacked ? y + SEGMENT_GAP / 2 : y;
                const rectHeight = stacked ? Math.max(0, segHeight - SEGMENT_GAP) : segHeight;

                return (
                  <rect
                    key={s.key}
                    x={rectX}
                    y={rectY}
                    width={rectWidth}
                    height={rectHeight}
                    rx={2}
                    fill={`var(${s.colorVar})`}
                    opacity={hover && !isHovered ? 0.85 : 1}
                    tabIndex={0}
                    role="button"
                    aria-label={`${g.label}, ${s.label}: ${formatValue(value)}`}
                    onPointerEnter={() =>
                      setHover({
                        groupIdx: gi,
                        seriesKey: s.key,
                        x: rectX + rectWidth / 2,
                        y: rectY,
                      })
                    }
                    onPointerLeave={() => setHover(null)}
                    onFocus={() =>
                      setHover({
                        groupIdx: gi,
                        seriesKey: s.key,
                        x: rectX + rectWidth / 2,
                        y: rectY,
                      })
                    }
                    onBlur={() => setHover(null)}
                  />
                );
              })}
              <text
                x={bandX + barWidth / 2}
                y={height - 8}
                className="viz-axis-label"
                textAnchor="middle"
              >
                {truncateLabel(g.label, 12)}
                <title>{g.label}</title>
              </text>
            </g>
          );
        })}
      </svg>
      {tooltip}
      {legend}
    </div>
  );
}
