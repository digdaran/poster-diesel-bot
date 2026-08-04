interface DateRangePickerProps {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}

const PRESETS = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
];

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function DateRangePicker({ from, to, onChange }: DateRangePickerProps) {
  function applyPreset(days: number) {
    const toDate = new Date();
    const fromDate = new Date();
    fromDate.setDate(fromDate.getDate() - (days - 1));
    onChange(isoDate(fromDate), isoDate(toDate));
  }

  return (
    <div className="filters">
      {PRESETS.map((p) => (
        <button
          key={p.days}
          type="button"
          className="button-secondary"
          onClick={() => applyPreset(p.days)}
        >
          {p.label}
        </button>
      ))}
      <button type="button" className="button-secondary" onClick={() => onChange("", "")}>
        Всё время
      </button>
      <label>
        с <input type="date" value={from} onChange={(e) => onChange(e.target.value, to)} />
      </label>
      <label>
        по <input type="date" value={to} onChange={(e) => onChange(from, e.target.value)} />
      </label>
    </div>
  );
}
