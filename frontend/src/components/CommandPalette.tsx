import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "./icons";
import type { IconName } from "./icons";

export interface CommandPaletteItem {
  to: string;
  label: string;
  icon: IconName;
  newTab?: boolean;
}

interface Props {
  items: CommandPaletteItem[];
  open: boolean;
  onClose: () => void;
}

// Ctrl/Cmd+K — быстрый переход между разделами без мыши (см. обсуждение при
// редизайне: панель используют операторы весь день, разделов уже 13).
export function CommandPalette({ items, open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => item.label.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Модалка монтируется в момент открытия — фокусируем в следующем кадре.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const go = (item: CommandPaletteItem) => {
    onClose();
    if (item.newTab) {
      window.open(item.to, "_blank", "noopener,noreferrer");
    } else {
      navigate(item.to);
    }
  };

  if (!open) return null;

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[activeIndex];
      if (item) go(item);
    }
  };

  return (
    <div className="command-palette-overlay" onClick={onClose}>
      <div
        className="command-palette"
        role="dialog"
        aria-label="Быстрая навигация"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="command-palette-input-row">
          <Icon name="search" size={16} />
          <input
            ref={inputRef}
            className="command-palette-input"
            placeholder="Куда перейти?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <span className="command-palette-kbd">Esc</span>
        </div>
        <div className="command-palette-list">
          {filtered.length === 0 && <div className="command-palette-empty">Ничего не найдено</div>}
          {filtered.map((item, i) => (
            <div
              key={item.to}
              className={"command-palette-item" + (i === activeIndex ? " is-active" : "")}
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => go(item)}
            >
              <Icon name={item.icon} size={16} />
              <span>{item.label}</span>
              {item.newTab && <Icon name="external" size={13} className="nav-link-external-mark" />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
