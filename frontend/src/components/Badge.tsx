import type { ReactNode } from "react";

type Tone = "success" | "danger" | "info" | "muted";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
