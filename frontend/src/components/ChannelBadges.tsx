import { Badge } from "./Badge";

const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  vk: "VK",
  max: "MAX",
};

type Props = { channels: string[] } | { channel: string | null };

export function ChannelBadges(props: Props) {
  const channels = "channels" in props ? props.channels : props.channel ? [props.channel] : [];

  if (channels.length === 0) return <>—</>;

  return (
    <span className="badge-group">
      {channels.map((channel) => (
        <Badge key={channel} tone="info">
          {CHANNEL_LABELS[channel] ?? channel}
        </Badge>
      ))}
    </span>
  );
}
