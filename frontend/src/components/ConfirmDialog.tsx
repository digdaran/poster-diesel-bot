import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";

interface PendingConfirm {
  message: string;
  resolve: (confirmed: boolean) => void;
}

interface ConfirmContextValue {
  confirm: (message: string) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | undefined>(undefined);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);

  const confirm = useCallback((message: string) => {
    return new Promise<boolean>((resolve) => {
      setPending({ message, resolve });
    });
  }, []);

  const settle = (confirmed: boolean) => {
    pending?.resolve(confirmed);
    setPending(null);
  };

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {pending && (
        <div className="modal-overlay" onClick={() => settle(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p className="modal-message">{pending.message}</p>
            <div className="modal-actions">
              <button className="button-secondary" onClick={() => settle(false)}>
                Отмена
              </button>
              <button onClick={() => settle(true)}>Подтвердить</button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (message: string) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm должен использоваться внутри ConfirmProvider");
  return ctx.confirm;
}
