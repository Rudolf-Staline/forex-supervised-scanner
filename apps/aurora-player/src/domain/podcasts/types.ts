import type { Brand } from "../common/brand";
import type { MediaId } from "../media/types";
import type { ISODateTime } from "../../shared/time";

export type PodcastFeedId = Brand<string, "PodcastFeedId">;

export type PodcastFeed = {
  readonly id: PodcastFeedId;
  readonly title: string;
  readonly feedUrl: string;
  readonly updatedAt: ISODateTime;
  readonly description?: string;
  readonly artworkUrl?: string;
};

export type PodcastEpisode = {
  readonly mediaId: MediaId;
  readonly feedId: PodcastFeedId;
  readonly guid: string;
  readonly title: string;
  readonly enclosureUrl: string;
  readonly publishedAt?: ISODateTime;
  readonly durationSeconds?: number;
};
