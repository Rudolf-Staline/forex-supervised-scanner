import type { Favorite } from "./favorites/types";
import type { PlaybackHistoryEntry } from "./history/types";
import type { MediaId, MediaItem } from "./media/types";
import type { Playlist, PlaylistId } from "./playlists/types";
import type { PodcastEpisode, PodcastFeed, PodcastFeedId } from "./podcasts/types";
import type { SyncOperation } from "./sync/types";

export interface MediaRepository {
  upsert(item: MediaItem): Promise<void>;
  getById(id: MediaId): Promise<MediaItem | null>;
  list(): Promise<readonly MediaItem[]>;
  remove(id: MediaId): Promise<void>;
}

export interface PlaylistRepository {
  upsert(playlist: Playlist): Promise<void>;
  getById(id: PlaylistId): Promise<Playlist | null>;
  list(): Promise<readonly Playlist[]>;
  remove(id: PlaylistId): Promise<void>;
}

export interface FavoriteRepository {
  set(favorite: Favorite): Promise<void>;
  unset(mediaId: MediaId): Promise<void>;
  list(): Promise<readonly Favorite[]>;
  has(mediaId: MediaId): Promise<boolean>;
}

export interface PlaybackHistoryRepository {
  append(entry: PlaybackHistoryEntry): Promise<void>;
  listRecent(limit: number): Promise<readonly PlaybackHistoryEntry[]>;
  removeForMedia(mediaId: MediaId): Promise<void>;
}

export interface PodcastRepository {
  upsertFeed(feed: PodcastFeed): Promise<void>;
  upsertEpisode(episode: PodcastEpisode): Promise<void>;
  getFeed(id: PodcastFeedId): Promise<PodcastFeed | null>;
  listFeeds(): Promise<readonly PodcastFeed[]>;
  listEpisodes(feedId: PodcastFeedId): Promise<readonly PodcastEpisode[]>;
}

export interface SyncOperationRepository {
  enqueue(operation: SyncOperation): Promise<void>;
  listPending(target?: SyncOperation["target"]): Promise<readonly SyncOperation[]>;
  remove(id: SyncOperation["id"]): Promise<void>;
}
