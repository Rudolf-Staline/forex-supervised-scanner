import type { MediaId, MediaItem } from "../../domain/media/types";
import type { PlaybackQueueState, RepeatMode, ShuffleStrategy } from "../../domain/playback/types";

const sameMediaId = (left: MediaId, right: MediaId): boolean => left === right;

const itemAt = (items: readonly MediaItem[], index: number | null): MediaItem | null => {
  if (index === null) {
    return null;
  }
  return items[index] ?? null;
};

const defaultShuffleStrategy: ShuffleStrategy = {
  shuffle: (items) => [...items].sort(() => Math.random() - 0.5)
};

export class PlaybackQueue {
  private originalItems: readonly MediaItem[];
  private state: PlaybackQueueState;

  public constructor(
    items: readonly MediaItem[] = [],
    private readonly shuffleStrategy: ShuffleStrategy = defaultShuffleStrategy
  ) {
    this.originalItems = [...items];
    this.state = {
      items: [...items],
      currentIndex: items.length > 0 ? 0 : null,
      repeat: "off",
      shuffle: false,
      playedIds: []
    };
  }

  public snapshot(): PlaybackQueueState {
    return {
      ...this.state,
      items: [...this.state.items],
      playedIds: [...this.state.playedIds]
    };
  }

  public current(): MediaItem | null {
    return itemAt(this.state.items, this.state.currentIndex);
  }

  public load(items: readonly MediaItem[], startId?: MediaId): MediaItem | null {
    this.originalItems = [...items];
    const requestedIndex =
      startId === undefined ? -1 : items.findIndex((item) => sameMediaId(item.id, startId));
    const currentIndex = items.length === 0 ? null : Math.max(requestedIndex, 0);
    this.state = {
      items: [...items],
      currentIndex,
      repeat: this.state.repeat,
      shuffle: false,
      playedIds: []
    };
    return this.current();
  }

  public enqueue(item: MediaItem): void {
    this.originalItems = [...this.originalItems, item];
    this.state = {
      ...this.state,
      items: [...this.state.items, item],
      currentIndex: this.state.currentIndex ?? 0
    };
  }

  public remove(id: MediaId): void {
    const current = this.current();
    this.originalItems = this.originalItems.filter((item) => !sameMediaId(item.id, id));
    const filtered = this.state.items.filter((item) => !sameMediaId(item.id, id));
    const nextIndex =
      current === null || sameMediaId(current.id, id)
        ? filtered.length > 0
          ? 0
          : null
        : filtered.findIndex((item) => sameMediaId(item.id, current.id));
    this.state = {
      ...this.state,
      items: filtered,
      currentIndex: nextIndex === -1 ? null : nextIndex
    };
  }

  public setRepeat(mode: RepeatMode): void {
    this.state = { ...this.state, repeat: mode };
  }

  public setShuffle(enabled: boolean): void {
    if (enabled === this.state.shuffle) {
      return;
    }

    const current = this.current();
    if (enabled) {
      const remaining =
        current === null ? this.state.items : this.state.items.filter((item) => !sameMediaId(item.id, current.id));
      const shuffled = this.shuffleStrategy.shuffle(remaining);
      const items = current === null ? shuffled : [current, ...shuffled];
      this.state = {
        ...this.state,
        items,
        currentIndex: items.length > 0 ? 0 : null,
        shuffle: true
      };
      return;
    }

    const currentIndex =
      current === null
        ? this.originalItems.length > 0
          ? 0
          : null
        : this.originalItems.findIndex((item) => sameMediaId(item.id, current.id));
    this.state = {
      ...this.state,
      items: [...this.originalItems],
      currentIndex: currentIndex === -1 ? null : currentIndex,
      shuffle: false
    };
  }

  public next(): MediaItem | null {
    const current = this.current();
    if (this.state.items.length === 0) {
      this.state = { ...this.state, currentIndex: null };
      return null;
    }
    if (this.state.repeat === "one" && current !== null) {
      return current;
    }

    const currentIndex = this.state.currentIndex ?? -1;
    const nextIndex = currentIndex + 1;
    if (nextIndex < this.state.items.length) {
      this.state = { ...this.state, currentIndex: nextIndex };
      return this.current();
    }
    if (this.state.repeat === "all") {
      this.state = { ...this.state, currentIndex: 0 };
      return this.current();
    }
    this.state = { ...this.state, currentIndex: null };
    return null;
  }

  public previous(): MediaItem | null {
    if (this.state.items.length === 0) {
      return null;
    }
    const currentIndex = this.state.currentIndex ?? 0;
    if (currentIndex > 0) {
      this.state = { ...this.state, currentIndex: currentIndex - 1 };
      return this.current();
    }
    if (this.state.repeat === "all") {
      this.state = { ...this.state, currentIndex: this.state.items.length - 1 };
      return this.current();
    }
    return this.current();
  }

  public handleTrackEnded(): MediaItem | null {
    const current = this.current();
    if (current !== null) {
      this.state = {
        ...this.state,
        playedIds: [...this.state.playedIds, current.id]
      };
    }
    return this.next();
  }
}
