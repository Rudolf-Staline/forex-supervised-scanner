import type { Brand } from "../common/brand";
import type { MediaId } from "../media/types";
import type { ISODateTime } from "../../shared/time";

export type PlaylistId = Brand<string, "PlaylistId">;

export type Playlist = {
  readonly id: PlaylistId;
  readonly name: string;
  readonly mediaIds: readonly MediaId[];
  readonly createdAt: ISODateTime;
  readonly updatedAt: ISODateTime;
  readonly description?: string;
};
