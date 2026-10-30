import { Badge } from "./Badge";

/** Количество с подарками по акции «N + M в подарок»: «10 +2 🎁». */
export function QuantityWithGift({ quantity, gift }: { quantity: number; gift: number }) {
  return (
    <>
      {quantity}
      {gift > 0 && (
        <span className="muted-text" title="Подарочные экземпляры по акции">
          {" "}
          +{gift} 🎁
        </span>
      )}
    </>
  );
}

/** Сколько подарков не влезло в тираж — их выдают вручную в следующем тираже. */
export function GiftsNotIssuedBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <>
      {" "}
      <Badge tone="warning">подарки не выданы: {count}</Badge>
    </>
  );
}
