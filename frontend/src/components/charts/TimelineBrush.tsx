import { useMemo, useRef, useState } from "react";

export interface TimelineBrushPoint {
  x: string;
  y: number;
}

interface TimelineBrushProps {
  data: TimelineBrushPoint[];
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  height?: number;
}

const WIDTH = 640;
const MARGIN = { top: 6, right: 8, bottom: 6, left: 8 };
const HANDLE_HIT = 10;

type DragMode = "move" | "resize-left" | "resize-right" | "create";

interface DragState {
  mode: DragMode;
  originFromIdx: number;
  originToIdx: number;
  startIdx: number;
}

export function TimelineBrush({ data, from, to, onChange, height = 48 }: TimelineBrushProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [draft, setDraft] = useState<{ fromIdx: number; toIdx: number } | null>(null);

  const innerWidth = WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = height - MARGIN.top - MARGIN.bottom;
  const baselineY = MARGIN.top + innerHeight;

  const points = useMemo(() => {
    if (data.length === 0) return [];
    const maxY = Math.max(1, ...data.map((d) => d.y));
    const stepX = data.length > 1 ? innerWidth / (data.length - 1) : 0;
    return data.map((d, i) => ({
      px: MARGIN.left + (data.length > 1 ? i * stepX : innerWidth / 2),
      py: baselineY - (d.y / maxY) * innerHeight,
      date: d.x,
    }));
  }, [data, innerWidth, innerHeight, baselineY]);

  function indexForDate(iso: string, fallback: number): number {
    if (!iso || points.length === 0) return fallback;
    const idx = points.findIndex((p) => p.date >= iso);
    return idx === -1 ? points.length - 1 : idx;
  }

  function pxToIndex(px: number): number {
    if (points.length <= 1) return 0;
    const stepX = innerWidth / (points.length - 1);
    return Math.min(Math.max(Math.round((px - MARGIN.left) / stepX), 0), points.length - 1);
  }

  function clientXToPx(clientX: number): number {
    const rect = svgRef.current!.getBoundingClientRect();
    return (clientX - rect.left) * (WIDTH / rect.width);
  }

  if (points.length === 0) {
    return null;
  }

  const fromIdx = draft ? draft.fromIdx : indexForDate(from, 0);
  const toIdx = draft ? draft.toIdx : indexForDate(to, points.length - 1);
  const selStartPx = points[fromIdx].px;
  const selEndPx = points[toIdx].px;

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.px.toFixed(1)} ${p.py.toFixed(1)}`)
    .join(" ");
  const areaPath =
    points.length > 1
      ? `${linePath} L ${points[points.length - 1].px.toFixed(1)} ${baselineY.toFixed(1)} L ${points[0].px.toFixed(1)} ${baselineY.toFixed(1)} Z`
      : "";

  function handlePointerDown(e: React.PointerEvent<SVGSVGElement>) {
    const px = clientXToPx(e.clientX);
    const idx = pxToIndex(px);
    let mode: DragMode = "create";
    if (Math.abs(px - selStartPx) <= HANDLE_HIT) mode = "resize-left";
    else if (Math.abs(px - selEndPx) <= HANDLE_HIT) mode = "resize-right";
    else if (px > selStartPx && px < selEndPx) mode = "move";

    dragRef.current = {
      mode,
      originFromIdx: fromIdx,
      originToIdx: toIdx,
      startIdx: idx,
    };
    setDraft(mode === "create" ? { fromIdx: idx, toIdx: idx } : { fromIdx, toIdx });
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const idx = pxToIndex(clientXToPx(e.clientX));

    if (drag.mode === "create") {
      setDraft({ fromIdx: Math.min(drag.startIdx, idx), toIdx: Math.max(drag.startIdx, idx) });
    } else if (drag.mode === "move") {
      const span = drag.originToIdx - drag.originFromIdx;
      const delta = idx - drag.startIdx;
      let newFrom = drag.originFromIdx + delta;
      let newTo = drag.originToIdx + delta;
      if (newFrom < 0) {
        newFrom = 0;
        newTo = span;
      }
      if (newTo > points.length - 1) {
        newTo = points.length - 1;
        newFrom = newTo - span;
      }
      setDraft({ fromIdx: newFrom, toIdx: newTo });
    } else if (drag.mode === "resize-left") {
      setDraft({ fromIdx: Math.min(idx, drag.originToIdx), toIdx: drag.originToIdx });
    } else if (drag.mode === "resize-right") {
      setDraft({ fromIdx: drag.originFromIdx, toIdx: Math.max(idx, drag.originFromIdx) });
    }
  }

  function handlePointerUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag || !draft) return;
    onChange(points[draft.fromIdx].date, points[draft.toIdx].date);
    setDraft(null);
  }

  return (
    <div className="viz-brush-wrapper">
      <svg
        ref={svgRef}
        className="viz-svg viz-brush-svg"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="slider"
        aria-label="Выбор диапазона дат перетаскиванием"
        aria-valuetext={`${from || points[0].date} — ${to || points[points.length - 1].date}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        {areaPath && (
          <path d={areaPath} fill="var(--color-text-muted)" opacity={0.18} stroke="none" />
        )}
        <path d={linePath} fill="none" stroke="var(--color-text-muted)" strokeWidth={1} />

        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={Math.max(0, selStartPx - MARGIN.left)}
          height={innerHeight}
          fill="var(--color-surface-alt)"
          opacity={0.7}
        />
        <rect
          x={selEndPx}
          y={MARGIN.top}
          width={Math.max(0, WIDTH - MARGIN.right - selEndPx)}
          height={innerHeight}
          fill="var(--color-surface-alt)"
          opacity={0.7}
        />

        <rect
          x={selStartPx}
          y={MARGIN.top}
          width={Math.max(1, selEndPx - selStartPx)}
          height={innerHeight}
          fill="var(--color-primary)"
          opacity={0.14}
          stroke="var(--color-primary)"
          strokeWidth={1}
          style={{ cursor: "grab" }}
        />
        <rect
          x={selStartPx - 3}
          y={MARGIN.top}
          width={6}
          height={innerHeight}
          rx={2}
          fill="var(--color-primary)"
          style={{ cursor: "ew-resize" }}
        />
        <rect
          x={selEndPx - 3}
          y={MARGIN.top}
          width={6}
          height={innerHeight}
          rx={2}
          fill="var(--color-primary)"
          style={{ cursor: "ew-resize" }}
        />
      </svg>
    </div>
  );
}
