import { useState } from "react";
import type { PageSize } from "../api/types";

export function usePagination(initialPageSize: PageSize = 50) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState<PageSize>(initialPageSize);

  const setPageSize = (size: PageSize) => {
    setPageSizeState(size);
    setPage(1); // иначе можно оказаться на несуществующей странице
  };

  return { page, pageSize, setPage, setPageSize };
}
