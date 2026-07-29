import { useEffect, useState } from "react";
import { SalesApi } from "../api/resources";
import { formatDateTime } from "../utils/format";
import { LoadingState } from "./EmptyState";
import type { PaymentReceipt } from "../api/types";

export function ReceiptViewerModal({
  paymentId,
  onClose,
}: {
  paymentId: number;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [receipts, setReceipts] = useState<PaymentReceipt[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [fileType, setFileType] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void SalesApi.listReceipts(paymentId).then((items) => {
      if (cancelled) return;
      setReceipts(items);
      setSelectedId(items[0]?.id ?? null);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [paymentId]);

  useEffect(() => {
    if (selectedId === null) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setFileUrl(null);
    void SalesApi.downloadReceipt(paymentId, selectedId).then(({ blob }) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setFileUrl(objectUrl);
      setFileType(blob.type);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [paymentId, selectedId]);

  const selected = receipts.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-details" onClick={(e) => e.stopPropagation()}>
        <div className="modal-details-header">
          <h2>Квитанция</h2>
          <button className="button-secondary" onClick={onClose}>
            Закрыть
          </button>
        </div>

        {loading ? (
          <LoadingState />
        ) : receipts.length === 0 ? (
          <p className="modal-message">Квитанция не найдена.</p>
        ) : (
          <>
            {receipts.length > 1 && (
              <div className="receipt-tabs">
                {receipts.map((r, i) => (
                  <button
                    key={r.id}
                    className={r.id === selectedId ? "button-secondary active" : "button-secondary"}
                    onClick={() => setSelectedId(r.id)}
                  >
                    №{i + 1} от {formatDateTime(r.uploaded_at)}
                  </button>
                ))}
              </div>
            )}

            <div className="receipt-frame">
              {!fileUrl ? (
                <LoadingState />
              ) : fileType.startsWith("image/") ? (
                <img src={fileUrl} alt="Квитанция" />
              ) : fileType === "application/pdf" ? (
                <iframe src={fileUrl} title="Квитанция" />
              ) : (
                <p className="modal-message">
                  Формат файла не поддерживает просмотр в браузере. Скачайте файл, чтобы открыть
                  его.
                </p>
              )}
            </div>

            {fileUrl && selected && (
              <div className="modal-actions">
                <a
                  className="button-secondary"
                  href={fileUrl}
                  download={selected.original_filename ?? `receipt_${selected.id}`}
                >
                  Скачать
                </a>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
