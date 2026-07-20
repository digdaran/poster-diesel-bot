import { useCallback, useState } from "react";

export function useAsyncAction<Args extends unknown[]>(
  action: (...args: Args) => Promise<void>,
): { run: (...args: Args) => void; pending: boolean } {
  const [pending, setPending] = useState(false);

  const run = useCallback(
    (...args: Args) => {
      setPending(true);
      void action(...args).finally(() => setPending(false));
    },
    [action],
  );

  return { run, pending };
}
