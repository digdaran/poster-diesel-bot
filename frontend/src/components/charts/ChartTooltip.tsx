import type { ReactNode } from "react";

interface ChartTooltipProps {
  leftPct: number;
  topPct: number;
  children: ReactNode;
}

// Позиционируется в процентах от .viz-root — тот же viewBox-масштаб, что и
// у SVG-графика, поэтому не «уезжает» при ресайзе контейнера.
export function ChartTooltip({ leftPct, topPct, children }: ChartTooltipProps) {
  return (
    <div
      className="viz-tooltip"
      style={{
        left: `${leftPct}%`,
        top: `${topPct}%`,
      }}
    >
      {children}
    </div>
  );
}
