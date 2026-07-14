import { PAGE_SIZES } from "../api/types";
import type { PageSize } from "../api/types";

interface Props {
  page: number;
  pageSize: PageSize;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: PageSize) => void;
}

export function PaginationControls({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: Props) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="pagination-controls">
      <span>
        Показано {from}–{to} из {total}
      </span>
      <label>
        На странице:{" "}
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value) as PageSize)}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <button disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        ← Назад
      </button>
      <span>
        Стр. {page} из {pageCount}
      </span>
      <button disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>
        Вперёд →
      </button>
    </div>
  );
}
