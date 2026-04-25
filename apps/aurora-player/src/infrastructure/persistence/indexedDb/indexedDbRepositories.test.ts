import { afterEach, describe, expect, it } from "vitest";
import { makeBrand } from "../../../domain/common/brand";
import type { Favorite } from "../../../domain/favorites/types";
import type { PlaybackHistoryEntry } from "../../../domain/history/types";
import type { Playlist } from "../../../domain/playlists/types";
import type { SyncOperation } from "../../../domain/sync/types";
import { makeMediaItem } from "../../../test/fakes/media";
import { openAuroraDatabase, type IndexedDbConnection } from "./client";
import { createAuroraRepositories } from "./repositories";

let activeConnection: IndexedDbConnection | null = null;
let activeDatabaseName: string | null = null;

afterEach(async () => {
  activeConnection?.close();
  if (activeDatabaseName !== null) {
    const databaseName = activeDatabaseName;
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.deleteDatabase(databaseName);
      request.onsuccess = () => {
        resolve();
      };
      request.onerror = () => {
        reject(request.error ?? new Error("Failed to delete test IndexedDB"));
      };
    });
  }
  activeConnection = null;
  activeDatabaseName = null;
});

const openTestDatabase = async (): Promise<IndexedDbConnection> => {
  activeDatabaseName = `aurora-test-${crypto.randomUUID()}`;
  activeConnection = await openAuroraDatabase(activeDatabaseName);
  return activeConnection;
};

describe("IndexedDB repositories", () => {
  it("persists media, favorites and playlists through dedicated repositories", async () => {
    const connection = await openTestDatabase();
    const repositories = createAuroraRepositories(connection.db);
    const media = makeMediaItem("media_1", "Track");
    const favorite: Favorite = { mediaId: media.id, createdAt: "2026-04-15T10:00:00.000Z" };
    const playlist: Playlist = {
      id: makeBrand("playlist_1", "PlaylistId"),
      name: "Night Drive",
      mediaIds: [media.id],
      createdAt: "2026-04-15T10:00:00.000Z",
      updatedAt: "2026-04-15T10:00:00.000Z"
    };

    await repositories.media.upsert(media);
    await repositories.favorites.set(favorite);
    await repositories.playlists.upsert(playlist);

    expect(await repositories.media.getById(media.id)).toEqual(media);
    expect(await repositories.favorites.has(media.id)).toBe(true);
    expect(await repositories.playlists.getById(playlist.id)).toEqual(playlist);
  });

  it("returns recent history in descending played order", async () => {
    const connection = await openTestDatabase();
    const repositories = createAuroraRepositories(connection.db);
    const first: PlaybackHistoryEntry = {
      id: makeBrand("history_1", "PlaybackHistoryId"),
      mediaId: makeBrand("media_1", "MediaId"),
      playedAt: "2026-04-15T09:00:00.000Z",
      positionSeconds: 10,
      completed: false
    };
    const second: PlaybackHistoryEntry = {
      id: makeBrand("history_2", "PlaybackHistoryId"),
      mediaId: makeBrand("media_2", "MediaId"),
      playedAt: "2026-04-15T10:00:00.000Z",
      positionSeconds: 120,
      completed: true
    };

    await repositories.history.append(first);
    await repositories.history.append(second);

    const recent = await repositories.history.listRecent(1);
    expect(recent).toEqual([second]);
  });

  it("filters pending sync operations by target", async () => {
    const connection = await openTestDatabase();
    const repositories = createAuroraRepositories(connection.db);
    const operation: SyncOperation = {
      id: makeBrand("sync_1", "SyncOperationId"),
      target: "google-drive",
      kind: "media-upserted",
      entityId: "media_1",
      createdAt: "2026-04-15T10:00:00.000Z",
      retryCount: 0,
      payload: { mediaId: "media_1" }
    };

    await repositories.syncOperations.enqueue(operation);

    expect(await repositories.syncOperations.listPending("google-drive")).toEqual([operation]);
    expect(await repositories.syncOperations.listPending("user-profile")).toEqual([]);
  });
});
