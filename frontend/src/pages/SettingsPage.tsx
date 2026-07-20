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
  const [contactsText, setContactsText] = useState("");

  const load = () =>
    void SettingsApi.get().then((s) => {
      setSettings(s);
      setContactsText(JSON.stringify(s.support_contacts, null, 2));
    });
  useEffect(load, []);

  const parseContacts = (): Record<string, string> | null => {
    try {
      return JSON.parse(contactsText);
    } catch {
      return null;
    }
  };

  const { run: saveContacts, pending: savingContacts } = useAsyncAction(async () => {
    const parsed = parseContacts();
    if (!parsed) {
      showToast("Контакты поддержки должны быть валидным JSON-объектом", "error");
      return;
    }
    try {
      await SettingsApi.updateSupportContacts(parsed);
      showToast("Контакты поддержки сохранены");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось сохранить контакты", "error");
    }
  });

  const updatePaymentProvider = async (value: string) => {
    try {
      await SettingsApi.updatePaymentProvider(value || null);
      showToast("Платёжный провайдер обновлён");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось обновить провайдера", "error");
    }
  };

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
        <textarea
          rows={6}
          value={contactsText}
          onChange={(e) => setContactsText(e.target.value)}
          disabled={!hasPermission("settings_edit_support_contacts")}
        />
        {hasPermission("settings_edit_support_contacts") && (
          <button onClick={saveContacts} disabled={savingContacts}>
            {savingContacts ? "Сохраняем…" : "Сохранить"}
          </button>
        )}
      </section>

      {hasPermission("payment_provider_switch") && (
        <section>
          <h2>Активный платёжный провайдер</h2>
          <select
            value={settings.payment_provider_override ?? ""}
            onChange={(e) => void updatePaymentProvider(e.target.value)}
          >
            <option value="">По умолчанию из .env</option>
            <option value="mock">Mock (тест)</option>
            <option value="tbank">Т-Банк</option>
            <option value="vtb">ВТБ</option>
          </select>
        </section>
      )}

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
