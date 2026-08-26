import { Badge } from "./Badge";
import { CHANNEL_LABELS } from "../utils/channels";

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
