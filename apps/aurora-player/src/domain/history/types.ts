import type { Brand } from "../common/brand";
import type { MediaId } from "../media/types";
import type { ISODateTime } from "../../shared/time";

export type PlaybackHistoryId = Brand<string, "PlaybackHistoryId">;

export type PlaybackHistoryEntry = {
  readonly id: PlaybackHistoryId;
  readonly mediaId: MediaId;
  readonly playedAt: ISODateTime;
  readonly positionSeconds: number;
  readonly completed: boolean;
};
