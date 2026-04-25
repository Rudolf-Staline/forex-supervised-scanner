import type { Brand } from "../common/brand";
import type { ISODateTime } from "../../shared/time";

export type MediaId = Brand<string, "MediaId">;

export type MediaKind = "audio" | "video" | "podcast-episode";

export type LocalMediaSource = {
  readonly kind: "local-file";
  readonly fileName: string;
  readonly mimeType: string;
  readonly sizeBytes: number;
  readonly lastModifiedMs: number;
};

export type DriveMediaSource = {
  readonly kind: "google-drive";
  readonly fileId: string;
  readonly mimeType: string;
  readonly checksum?: string;
  readonly webViewLink?: string;
};

export type PodcastMediaSource = {
  readonly kind: "podcast";
  readonly feedUrl: string;
  readonly enclosureUrl: string;
  readonly guid: string;
  readonly mimeType?: string;
};

export type MediaSource = LocalMediaSource | DriveMediaSource | PodcastMediaSource;

export type MediaItem = {
  readonly id: MediaId;
  readonly kind: MediaKind;
  readonly title: string;
  readonly source: MediaSource;
  readonly createdAt: ISODateTime;
  readonly updatedAt: ISODateTime;
  readonly artist?: string;
  readonly album?: string;
  readonly durationSeconds?: number;
  readonly artworkUrl?: string;
};

export type ResolvedMediaSource = {
  readonly url: string;
  readonly mimeType?: string;
};

export interface MediaSourceResolver {
  resolve(item: MediaItem): Promise<ResolvedMediaSource>;
}
