import { useEffect, useState } from "react";
import { SettingsApi } from "../api/resources";
import type { PlatformSettings } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export function SettingsPage() {
  const { hasPermission } = useAuth();
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [contactsText, setContactsText] = useState("");

  const load = () =>
    void SettingsApi.get().then((s) => {
      setSettings(s);
      setContactsText(JSON.stringify(s.support_contacts, null, 2));
    });
  useEffect(load, []);

  if (!settings) return <div>Загрузка…</div>;

  const saveContacts = async () => {
    try {
      const parsed = JSON.parse(contactsText);
      await SettingsApi.updateSupportContacts(parsed);
      load();
    } catch {
      alert("Контакты поддержки должны быть валидным JSON-объектом");
    }
  };

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
          <button onClick={saveContacts}>Сохранить</button>
        )}
      </section>

      {hasPermission("payment_provider_switch") && (
        <section>
          <h2>Активный платёжный провайдер</h2>
          <select
            value={settings.payment_provider_override ?? ""}
            onChange={(e) => SettingsApi.updatePaymentProvider(e.target.value || null).then(load)}
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
              onChange={(e) =>
                SettingsApi.updateIgnorePhoneVerification(e.target.checked).then(load)
              }
            />
            Включено (снижает защиту от захвата аккаунта — см. ТЗ п.7.1)
          </label>
        </section>
      )}
    </div>
  );
}
