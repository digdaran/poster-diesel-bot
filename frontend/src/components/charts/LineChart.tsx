import { useMemo, useRef, useState } from "react";
import { ChartTooltip } from "./ChartTooltip";

export interface LineChartPoint {
  x: string;
  y: number;
}

interface LineChartProps {
  data: LineChartPoint[];
  formatValue?: (v: number) => string;
  formatX?: (x: string) => string;
  colorVar?: string;
  height?: number;
  emptyMessage?: string;
}

const WIDTH = 680;
const MARGIN = { top: 16, right: 16, bottom: 28, left: 56 };

export function LineChart({
  data,
  formatValue = String,
  formatX = (x) => x,
  colorVar = "--series-1",
  height = 240,
  emptyMessage = "Нет данных за выбранный период",
}: LineChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const innerWidth = WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = height - MARGIN.top - MARGIN.bottom;
  const baselineY = MARGIN.top + innerHeight;

  const maxY = useMemo(() => Math.max(1, ...data.map((d) => d.y)), [data]);
  const stepX = data.length > 1 ? innerWidth / (data.length - 1) : 0;

  const points = useMemo(
    () =>
      data.map((d, i) => ({
        px: MARGIN.left + (data.length > 1 ? i * stepX : innerWidth / 2),
        py: baselineY - (d.y / maxY) * innerHeight,
        label: d.x,
        value: d.y,
      })),
    [data, stepX, maxY, innerWidth, innerHeight, baselineY],
  );

  if (data.length === 0) {
    return <div className="viz-empty">{emptyMessage}</div>;
  }

  const hasLine = points.length > 1;
  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.px.toFixed(1)} ${p.py.toFixed(1)}`)
    .join(" ");
  const areaPath = hasLine
    ? `${linePath} L ${points[points.length - 1].px.toFixed(1)} ${baselineY.toFixed(1)} L ${points[0].px.toFixed(1)} ${baselineY.toFixed(1)} Z`
    : "";

  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => (maxY / yTicks) * i);

  // Показываем не больше ~8 подписей по оси X, чтобы они не наезжали друг на друга.
  const xLabelStride = Math.max(1, Math.ceil(points.length / 8));

  function updateHoverFromClientX(clientX: number) {
    if (!svgRef.current || points.length === 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = WIDTH / rect.width;
    const localX = (clientX - rect.left) * scaleX;
    const idx = points.length > 1 ? Math.round((localX - MARGIN.left) / stepX) : 0;
    setHoverIndex(Math.min(Math.max(idx, 0), points.length - 1));
  }

  const hovered = hoverIndex !== null ? points[hoverIndex] : hasLine ? null : points[0];

  return (
    <div className="viz-root viz-line-wrapper">
      <svg
        ref={svgRef}
        className="viz-svg"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Линейный график, ${points.length} точек, максимум ${formatValue(maxY)}`}
        onPointerMove={(e) => updateHoverFromClientX(e.clientX)}
        onPointerLeave={() => setHoverIndex(null)}
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

        {points.map((p, i) =>
          i % xLabelStride === 0 || i === points.length - 1 ? (
            <text key={i} x={p.px} y={height - 8} className="viz-axis-label" textAnchor="middle">
              {formatX(p.label)}
            </text>
          ) : null,
        )}

        {hasLine && <path d={areaPath} fill={`var(${colorVar})`} opacity={0.14} stroke="none" />}
        {hasLine && (
          <path
            d={linePath}
            fill="none"
            stroke={`var(${colorVar})`}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
        {!hasLine && <circle cx={points[0].px} cy={points[0].py} r={4} fill={`var(${colorVar})`} />}

        {hoverIndex !== null && (
          <line
            x1={points[hoverIndex].px}
            x2={points[hoverIndex].px}
            y1={MARGIN.top}
            y2={baselineY}
            className="viz-crosshair"
          />
        )}
        {hovered && (
          <circle
            cx={hovered.px}
            cy={hovered.py}
            r={4}
            fill={`var(${colorVar})`}
            stroke="var(--color-surface)"
            strokeWidth={2}
          />
        )}
      </svg>

      {hovered && (
        <ChartTooltip leftPct={(hovered.px / WIDTH) * 100} topPct={(hovered.py / height) * 100}>
          <div className="viz-tooltip-label">{formatX(hovered.label)}</div>
          <div className="viz-tooltip-row">
            <span className="viz-tooltip-key" style={{ background: `var(${colorVar})` }} />
            <span className="viz-tooltip-value">{formatValue(hovered.value)}</span>
          </div>
        </ChartTooltip>
      )}
    </div>
  );
}
