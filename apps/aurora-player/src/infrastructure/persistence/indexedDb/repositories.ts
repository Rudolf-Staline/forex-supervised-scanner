import type { Favorite } from "../../../domain/favorites/types";
import type { PlaybackHistoryEntry } from "../../../domain/history/types";
import type { MediaId, MediaItem } from "../../../domain/media/types";
import type { Playlist, PlaylistId } from "../../../domain/playlists/types";
import type { PodcastEpisode, PodcastFeed, PodcastFeedId } from "../../../domain/podcasts/types";
import type {
  FavoriteRepository,
  MediaRepository,
  PlaybackHistoryRepository,
  PlaylistRepository,
  PodcastRepository,
  SyncOperationRepository
} from "../../../domain/repositories";
import type { SyncOperation } from "../../../domain/sync/types";
import { requestToPromise, withStore } from "./client";
import { STORE_NAMES } from "./schema";

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} record is invalid`);
  }
  return value as Record<string, unknown>;
};

const getString = (record: Record<string, unknown>, key: string, label: string): string => {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label}.${key} must be a non-empty string`);
  }
  return value;
};

const castRecord = <TValue>(value: unknown, label: string, key: string): TValue => {
  const record = asRecord(value, label);
  getString(record, key, label);
  return value as TValue;
};

const castRecords = <TValue>(values: readonly unknown[], label: string, key: string): readonly TValue[] =>
  values.map((value) => castRecord<TValue>(value, label, key));

export class IndexedDbMediaRepository implements MediaRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async upsert(item: MediaItem): Promise<void> {
    await withStore(this.db, STORE_NAMES.media, "readwrite", async (store) => {
      await requestToPromise(store.put(item));
    });
  }

  public async getById(id: MediaId): Promise<MediaItem | null> {
    return withStore(this.db, STORE_NAMES.media, "readonly", async (store) => {
      const result = await requestToPromise<unknown>(store.get(id));
      return result === undefined ? null : castRecord<MediaItem>(result, "media", "id");
    });
  }

  public async list(): Promise<readonly MediaItem[]> {
    return withStore(this.db, STORE_NAMES.media, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return castRecords<MediaItem>(result, "media", "id");
    });
  }

  public async remove(id: MediaId): Promise<void> {
    await withStore(this.db, STORE_NAMES.media, "readwrite", async (store) => {
      await requestToPromise(store.delete(id));
    });
  }
}

export class IndexedDbPlaylistRepository implements PlaylistRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async upsert(playlist: Playlist): Promise<void> {
    await withStore(this.db, STORE_NAMES.playlists, "readwrite", async (store) => {
      await requestToPromise(store.put(playlist));
    });
  }

  public async getById(id: PlaylistId): Promise<Playlist | null> {
    return withStore(this.db, STORE_NAMES.playlists, "readonly", async (store) => {
      const result = await requestToPromise<unknown>(store.get(id));
      return result === undefined ? null : castRecord<Playlist>(result, "playlist", "id");
    });
  }

  public async list(): Promise<readonly Playlist[]> {
    return withStore(this.db, STORE_NAMES.playlists, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return castRecords<Playlist>(result, "playlist", "id");
    });
  }

  public async remove(id: PlaylistId): Promise<void> {
    await withStore(this.db, STORE_NAMES.playlists, "readwrite", async (store) => {
      await requestToPromise(store.delete(id));
    });
  }
}

export class IndexedDbFavoriteRepository implements FavoriteRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async set(favorite: Favorite): Promise<void> {
    await withStore(this.db, STORE_NAMES.favorites, "readwrite", async (store) => {
      await requestToPromise(store.put(favorite));
    });
  }

  public async unset(mediaId: MediaId): Promise<void> {
    await withStore(this.db, STORE_NAMES.favorites, "readwrite", async (store) => {
      await requestToPromise(store.delete(mediaId));
    });
  }

  public async list(): Promise<readonly Favorite[]> {
    return withStore(this.db, STORE_NAMES.favorites, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return castRecords<Favorite>(result, "favorite", "mediaId");
    });
  }

  public async has(mediaId: MediaId): Promise<boolean> {
    return withStore(this.db, STORE_NAMES.favorites, "readonly", async (store) => {
      const result = await requestToPromise<unknown>(store.get(mediaId));
      return result !== undefined;
    });
  }
}

export class IndexedDbPlaybackHistoryRepository implements PlaybackHistoryRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async append(entry: PlaybackHistoryEntry): Promise<void> {
    await withStore(this.db, STORE_NAMES.history, "readwrite", async (store) => {
      await requestToPromise(store.put(entry));
    });
  }

  public async listRecent(limit: number): Promise<readonly PlaybackHistoryEntry[]> {
    if (!Number.isInteger(limit) || limit < 1) {
      throw new Error("History limit must be a positive integer");
    }
    return withStore(this.db, STORE_NAMES.history, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return castRecords<PlaybackHistoryEntry>(result, "history", "id")
        .slice()
        .sort((left, right) => right.playedAt.localeCompare(left.playedAt))
        .slice(0, limit);
    });
  }

  public async removeForMedia(mediaId: MediaId): Promise<void> {
    await withStore(this.db, STORE_NAMES.history, "readwrite", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      const entries = castRecords<PlaybackHistoryEntry>(result, "history", "id").filter(
        (entry) => entry.mediaId === mediaId
      );
      for (const entry of entries) {
        await requestToPromise(store.delete(entry.id));
      }
    });
  }
}

export class IndexedDbPodcastRepository implements PodcastRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async upsertFeed(feed: PodcastFeed): Promise<void> {
    await withStore(this.db, STORE_NAMES.podcastFeeds, "readwrite", async (store) => {
      await requestToPromise(store.put(feed));
    });
  }

  public async upsertEpisode(episode: PodcastEpisode): Promise<void> {
    await withStore(this.db, STORE_NAMES.podcastEpisodes, "readwrite", async (store) => {
      await requestToPromise(store.put(episode));
    });
  }

  public async getFeed(id: PodcastFeedId): Promise<PodcastFeed | null> {
    return withStore(this.db, STORE_NAMES.podcastFeeds, "readonly", async (store) => {
      const result = await requestToPromise<unknown>(store.get(id));
      return result === undefined ? null : castRecord<PodcastFeed>(result, "podcastFeed", "id");
    });
  }

  public async listFeeds(): Promise<readonly PodcastFeed[]> {
    return withStore(this.db, STORE_NAMES.podcastFeeds, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return castRecords<PodcastFeed>(result, "podcastFeed", "id");
    });
  }

  public async listEpisodes(feedId: PodcastFeedId): Promise<readonly PodcastEpisode[]> {
    return withStore(this.db, STORE_NAMES.podcastEpisodes, "readonly", async (store) => {
      const index = store.index("feedId");
      const result = await requestToPromise<unknown[]>(index.getAll(feedId));
      return castRecords<PodcastEpisode>(result, "podcastEpisode", "mediaId");
    });
  }
}

export class IndexedDbSyncOperationRepository implements SyncOperationRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async enqueue(operation: SyncOperation): Promise<void> {
    await withStore(this.db, STORE_NAMES.syncQueue, "readwrite", async (store) => {
      await requestToPromise(store.put(operation));
    });
  }

  public async listPending(target?: SyncOperation["target"]): Promise<readonly SyncOperation[]> {
    return withStore(this.db, STORE_NAMES.syncQueue, "readonly", async (store) => {
      const result =
        target === undefined
          ? await requestToPromise<unknown[]>(store.getAll())
          : await requestToPromise<unknown[]>(store.index("target").getAll(target));
      return [...castRecords<SyncOperation>(result, "syncOperation", "id")].sort((left, right) =>
        left.createdAt.localeCompare(right.createdAt)
      );
    });
  }

  public async remove(id: SyncOperation["id"]): Promise<void> {
    await withStore(this.db, STORE_NAMES.syncQueue, "readwrite", async (store) => {
      await requestToPromise(store.delete(id));
    });
  }
}

export type AuroraRepositories = {
  readonly media: MediaRepository;
  readonly playlists: PlaylistRepository;
  readonly favorites: FavoriteRepository;
  readonly history: PlaybackHistoryRepository;
  readonly podcasts: PodcastRepository;
  readonly syncOperations: SyncOperationRepository;
};

export const createAuroraRepositories = (db: IDBDatabase): AuroraRepositories => ({
  media: new IndexedDbMediaRepository(db),
  playlists: new IndexedDbPlaylistRepository(db),
  favorites: new IndexedDbFavoriteRepository(db),
  history: new IndexedDbPlaybackHistoryRepository(db),
  podcasts: new IndexedDbPodcastRepository(db),
  syncOperations: new IndexedDbSyncOperationRepository(db)
});
