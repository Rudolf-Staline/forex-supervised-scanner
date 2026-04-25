import { useMemo, useState } from "react";

export type VirtualizedListProps<TItem> = {
  readonly items: readonly TItem[];
  readonly itemHeight: number;
  readonly height: number;
  readonly overscan?: number;
  readonly getKey: (item: TItem) => string;
  readonly renderItem: (item: TItem, index: number) => React.ReactNode;
  readonly ariaLabel: string;
};

export function VirtualizedList<TItem>({
  items,
  itemHeight,
  height,
  overscan = 4,
  getKey,
  renderItem,
  ariaLabel
}: VirtualizedListProps<TItem>): React.ReactElement {
  const [scrollTop, setScrollTop] = useState(0);
  const totalHeight = items.length * itemHeight;
  const range = useMemo(() => {
    const start = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
    const visibleCount = Math.ceil(height / itemHeight) + overscan * 2;
    const end = Math.min(items.length, start + visibleCount);
    return { start, end };
  }, [height, itemHeight, items.length, overscan, scrollTop]);

  const visibleItems = items.slice(range.start, range.end);

  return (
    <div
      aria-label={ariaLabel}
      className="overflow-y-auto border border-aurora-border bg-aurora-panel"
      role="list"
      style={{ height }}
      tabIndex={0}
      onScroll={(event) => {
        setScrollTop(event.currentTarget.scrollTop);
      }}
    >
      <div className="relative" style={{ height: totalHeight }}>
        {visibleItems.map((item, offset) => {
          const index = range.start + offset;
          return (
            <div
              key={getKey(item)}
              className="absolute left-0 right-0"
              role="listitem"
              style={{ height: itemHeight, transform: `translateY(${index * itemHeight}px)` }}
            >
              {renderItem(item, index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
