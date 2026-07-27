import { useEffect, useState } from "react";
import { SettingsApi } from "../api/resources";
import type { PlatformSettings } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { useToast } from "../components/Toast";
import { LoadingState } from "../components/EmptyState";

export function SettingsPage() {
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [telegramContact, setTelegramContact] = useState("");
  const [vkContact, setVkContact] = useState("");

  const load = () =>
    void SettingsApi.get().then((s) => {
      setSettings(s);
      setTelegramContact(s.support_contacts.telegram ?? "");
      setVkContact(s.support_contacts.vk ?? "");
    });
  useEffect(load, []);

  const { run: saveContacts, pending: savingContacts } = useAsyncAction(async () => {
    const merged = {
      ...settings?.support_contacts,
      telegram: telegramContact.trim(),
      vk: vkContact.trim(),
    };
    const contacts = Object.fromEntries(Object.entries(merged).filter(([, v]) => v !== ""));
    try {
      await SettingsApi.updateSupportContacts(contacts);
      showToast("Контакты поддержки сохранены");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось сохранить контакты", "error");
    }
  });

  const updateIgnorePhoneVerification = async (checked: boolean) => {
    try {
      await SettingsApi.updateIgnorePhoneVerification(checked);
      showToast(checked ? "Проверка номера отключена" : "Проверка номера включена");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось изменить настройку", "error");
    }
  };

  if (!settings) return <LoadingState />;

  return (
    <div>
      <h1>Настройки</h1>

      <section>
        <h2>Контакты поддержки</h2>
        <p className="settings-hint">
          Кнопка «Написать в поддержку» в боте откроет чат по указанной ссылке. Можно ввести
          юзернейм/ссылку — если оставить поле пустым, участнику покажется обычный текст помощи
          без кнопки для этой платформы.
        </p>
        <label className="settings-field">
          Telegram
          <input
            type="text"
            placeholder="username или https://t.me/username"
            value={telegramContact}
            onChange={(e) => setTelegramContact(e.target.value)}
            disabled={!hasPermission("settings_edit_support_contacts")}
          />
        </label>
        <label className="settings-field">
          VK
          <input
            type="text"
            placeholder="id/короткое имя или https://vk.com/..."
            value={vkContact}
            onChange={(e) => setVkContact(e.target.value)}
            disabled={!hasPermission("settings_edit_support_contacts")}
          />
        </label>
        {hasPermission("settings_edit_support_contacts") && (
          <button onClick={saveContacts} disabled={savingContacts}>
            {savingContacts ? "Сохраняем…" : "Сохранить"}
          </button>
        )}
      </section>

      {hasPermission("ignore_phone_verification_toggle") && (
        <section>
          <h2>Игнорировать подтверждение номера</h2>
          <label>
            <input
              type="checkbox"
              checked={settings.ignore_phone_verification}
              onChange={(e) => void updateIgnorePhoneVerification(e.target.checked)}
            />
            Включено (снижает защиту от захвата аккаунта — см. ТЗ п.7.1)
          </label>
        </section>
      )}
    </div>
  );
}
