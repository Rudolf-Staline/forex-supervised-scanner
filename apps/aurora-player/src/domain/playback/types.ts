import type { MediaId, MediaItem } from "../media/types";

export type PlaybackStatus = "idle" | "loading" | "playing" | "paused" | "ended" | "error";

export type RepeatMode = "off" | "one" | "all";

export type PlaybackQueueState = {
  readonly items: readonly MediaItem[];
  readonly currentIndex: number | null;
  readonly repeat: RepeatMode;
  readonly shuffle: boolean;
  readonly playedIds: readonly MediaId[];
};

export type PlaybackSnapshot = {
  readonly queue: PlaybackQueueState;
  readonly status: PlaybackStatus;
  readonly positionSeconds: number;
  readonly durationSeconds: number | null;
  readonly errorMessage: string | null;
};

export type ShuffleStrategy = {
  shuffle(items: readonly MediaItem[]): readonly MediaItem[];
};
