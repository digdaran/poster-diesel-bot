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
  height?: number;
  showLegend?: boolean;
  emptyMessage?: string;
}

const WIDTH = 680;
const MARGIN = { top: 16, right: 16, bottom: 40, left: 56 };
const SEGMENT_GAP = 2;

interface HoverTarget {
  groupIdx: number;
  seriesKey: string;
  x: number;
  y: number;
}

export function BarChart({
  groups,
  series,
  stacked = true,
  formatValue = String,
  height = 240,
  showLegend = true,
  emptyMessage = "Нет данных",
}: BarChartProps) {
  const [hover, setHover] = useState<HoverTarget | null>(null);

  const innerWidth = WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = height - MARGIN.top - MARGIN.bottom;
  const baselineY = MARGIN.top + innerHeight;

  if (groups.length === 0) {
    return <div className="viz-empty">{emptyMessage}</div>;
  }

  const totals = groups.map((g) =>
    stacked
      ? series.reduce((sum, s) => sum + (g.values[s.key] ?? 0), 0)
      : Math.max(0, ...series.map((s) => g.values[s.key] ?? 0)),
  );
  const maxY = Math.max(1, ...totals);

  const bandWidth = innerWidth / groups.length;
  const barWidth = Math.max(10, bandWidth * 0.6);

  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => (maxY / yTicks) * i);

  const hoveredGroup = hover ? groups[hover.groupIdx] : null;
  const hoveredSeries = hover ? series.find((s) => s.key === hover.seriesKey) : null;

  return (
    <div className="viz-root viz-bar-wrapper">
      <svg
        className="viz-svg"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Столбчатая диаграмма, ${groups.length} категорий, максимум ${formatValue(maxY)}`}
      >
        {yTickValues.map((v, i) => {
          const y = baselineY - (v / maxY) * innerHeight;
          return (
            <g key={i}>
              <line
                x1={MARGIN.left}
                x2={WIDTH - MARGIN.right}
                y1={y}
                y2={y}
                className="viz-gridline"
              />
              <text
                x={MARGIN.left - 8}
                y={y}
                className="viz-axis-label"
                textAnchor="end"
                dominantBaseline="middle"
              >
                {formatValue(v)}
              </text>
            </g>
          );
        })}
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={baselineY}
          y2={baselineY}
          className="viz-axis-line"
        />

        {groups.map((g, gi) => {
          const bandX = MARGIN.left + gi * bandWidth + (bandWidth - barWidth) / 2;
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
                {g.label}
              </text>
            </g>
          );
        })}
      </svg>

      {hover && hoveredGroup && hoveredSeries && (
        <ChartTooltip leftPct={(hover.x / WIDTH) * 100} topPct={(hover.y / height) * 100}>
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
      )}

      {showLegend && series.length > 1 && (
        <div className="viz-legend">
          {series.map((s) => (
            <span key={s.key} className="viz-legend-item">
              <span className="viz-legend-swatch" style={{ background: `var(${s.colorVar})` }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
