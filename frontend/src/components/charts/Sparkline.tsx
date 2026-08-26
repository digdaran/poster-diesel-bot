interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  colorVar?: string;
}

// Компактный тренд без осей/тултипа — для карточки коллекции на Dashboard
// (см. DashboardPage.tsx), не полноценный LineChart: тут важна форма, а не
// точные значения (они уже есть рядом, в card-value).
export function Sparkline({
  data,
  width = 96,
  height = 28,
  colorVar = "--color-primary",
}: SparklineProps) {
  if (data.length === 0) return null;

  const hasSignal = data.some((v) => v > 0);
  const max = Math.max(1, ...data);
  const stepX = data.length > 1 ? width / (data.length - 1) : 0;
  const points = data.map((v, i) => ({
    x: data.length > 1 ? i * stepX : width / 2,
    y: height - (v / max) * (height - 2) - 1,
  }));
  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");
  const areaPath =
    points.length > 1
      ? `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${height} L ${points[0].x.toFixed(1)} ${height} Z`
      : "";

  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      {areaPath && (
        <path d={areaPath} fill={`var(${colorVar})`} opacity={hasSignal ? 0.16 : 0.06} />
      )}
      <path
        d={linePath}
        fill="none"
        stroke={`var(${colorVar})`}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={hasSignal ? 1 : 0.3}
      />
    </svg>
  );
}
