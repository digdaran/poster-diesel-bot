import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { GiveawaysApi } from "../api/resources";
import type { Giveaway, GiveawayPoster } from "../api/types";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { LoadingState } from "../components/EmptyState";

interface PosterWithPreview extends GiveawayPoster {
  previewUrl: string | null;
}

export function GiveawayDetailPage() {
  const { id } = useParams<{ id: string }>();
  const giveawayId = Number(id);
  const { hasPermission } = useAuth();
  const canEdit = hasPermission("giveaway_edit");
  const { showToast } = useToast();
  const confirm = useConfirm();

  const [giveaway, setGiveaway] = useState<Giveaway | null>(null);
  const [posters, setPosters] = useState<PosterWithPreview[]>([]);
  const [loading, setLoading] = useState(true);
  const [caption, setCaption] = useState("");
  const [name, setName] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const objectUrlsRef = useRef<string[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      const [g, list] = await Promise.all([
        GiveawaysApi.get(giveawayId),
        GiveawaysApi.listPosters(giveawayId),
      ]);
      setGiveaway(g);
      setName(g.name);
      setCaption(g.digital_poster_caption ?? "");

      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      objectUrlsRef.current = [];
      const withPreviews = await Promise.all(
        list.map(async (p) => {
          try {
            const { blob } = await apiDownload(GiveawaysApi.posterFileUrl(giveawayId, p.id));
            const url = URL.createObjectURL(blob);
            objectUrlsRef.current.push(url);
            return { ...p, previewUrl: url };
          } catch {
            return { ...p, previewUrl: null };
          }
        }),
      );
      setPosters(withPreviews);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    return () => {
      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [giveawayId]);

  const { run: onSave, pending: saving } = useAsyncAction(async (e: FormEvent) => {
    e.preventDefault();
    try {
      const updated = await GiveawaysApi.update(giveawayId, {
        name,
        digital_poster_caption: caption,
      });
      setGiveaway(updated);
      showToast("Изменения сохранены");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось сохранить изменения", "error");
    }
  });

  const { run: onUpload, pending: uploading } = useAsyncAction(async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    try {
      await GiveawaysApi.uploadPoster(giveawayId, file);
      if (fileInputRef.current) fileInputRef.current.value = "";
      showToast("Постер загружен");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось загрузить постер", "error");
    }
  });

  const onDelete = async (poster: PosterWithPreview) => {
    const confirmed = await confirm(
      `Удалить постер «${poster.original_filename ?? poster.id}»? Действие необратимо.`,
    );
    if (!confirmed) return;
    setDeletingId(poster.id);
    try {
      await GiveawaysApi.deletePoster(giveawayId, poster.id);
      showToast("Постер удалён");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось удалить постер", "error");
    } finally {
      setDeletingId(null);
    }
  };

  if (loading && !giveaway) return <LoadingState message="Загрузка розыгрыша…" />;
  if (!giveaway) return <div className="page-loading">Розыгрыш не найден</div>;

  return (
    <div>
      <p>
        <Link to="/giveaways">← К списку коллекций</Link>
      </p>
      <h1>{giveaway.name}</h1>

      <section>
        <h2>Основное</h2>
        {canEdit ? (
          <form onSubmit={onSave} className="inline-form-column">
            <label>
              Название
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </label>
            <label>
              Подпись к постеру (общая для всех загруженных изображений)
              <textarea
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                rows={3}
              />
            </label>
            <button type="submit" disabled={saving}>
              {saving ? "Сохраняем…" : "Сохранить"}
            </button>
          </form>
        ) : (
          <p>{giveaway.digital_poster_caption || "Подпись не задана"}</p>
        )}
      </section>

      <section>
        <h2>Цифровые постеры</h2>
        <p>
          Участнику после подтверждения оплаты отправляется один случайно выбранный постер
          из загруженных ниже.
        </p>
        {loading ? (
          <LoadingState />
        ) : posters.length === 0 ? (
          <p>Постеры ещё не загружены.</p>
        ) : (
          <div className="poster-grid">
            {posters.map((p) => (
              <div key={p.id} className="poster-card">
                {p.previewUrl ? (
                  <img src={p.previewUrl} alt={p.original_filename ?? `Постер ${p.id}`} />
                ) : (
                  <div className="poster-card-placeholder">Нет превью</div>
                )}
                {canEdit && (
                  <button
                    className="button-danger"
                    disabled={deletingId === p.id}
                    onClick={() => void onDelete(p)}
                  >
                    Удалить
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        {canEdit && (
          <div className="inline-form">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
            />
            <button onClick={() => onUpload()} disabled={uploading}>
              {uploading ? "Загружаем…" : "Загрузить постер"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
