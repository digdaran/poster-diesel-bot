import { useState } from "react";
import type { FormEvent } from "react";

interface RefundDialogProps {
  message: string;
  pending: boolean;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}

/** Модалка аннулирования уже завершённой покупки — обязательное поле причины
 * (попадает в AuditLog и хранится на самой покупке, см. DECISIONS_LOG.md №69).
 * Отдельный компонент от ConfirmDialog, т.к. тому не хватает поля ввода. */
export function RefundDialog({ message, pending, onConfirm, onClose }: RefundDialogProps) {
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!trimmed || pending) return;
    onConfirm(trimmed);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <p className="modal-message">{message}</p>
        <form onSubmit={onSubmit}>
          <textarea
            autoFocus
            rows={3}
            placeholder="Причина аннулирования (обязательно)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ width: "100%", resize: "vertical" }}
            required
          />
          <div className="modal-actions">
            <button type="button" className="button-secondary" onClick={onClose} disabled={pending}>
              Отмена
            </button>
            <button type="submit" className="button-danger" disabled={pending || !trimmed}>
              {pending ? "Аннулируем…" : "Аннулировать"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
