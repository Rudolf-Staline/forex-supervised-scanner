import { describe, expect, it } from "vitest";
import type { Clock } from "../../shared/time";
import { GoogleDriveAuthService, type DriveTokenRefresher, type DriveTokenStore } from "./auth";
import { GoogleDriveHttpClient } from "./http";
import { GoogleDriveSyncService } from "./syncService";
import type { DriveAccessToken, DriveApi, DriveFileRecord, DriveFileRepository } from "./types";

const fixedClock: Clock = {
  now: () => new Date("2026-04-15T10:00:00.000Z")
};

class MemoryTokenStore implements DriveTokenStore {
  public token: DriveAccessToken | null = null;

  public async load(): Promise<DriveAccessToken | null> {
    return this.token;
  }

  public async save(token: DriveAccessToken): Promise<void> {
    this.token = token;
  }

  public async clear(): Promise<void> {
    this.token = null;
  }
}

class StaticTokenRefresher implements DriveTokenRefresher {
  public refreshCount = 0;

  public async refresh(): Promise<DriveAccessToken> {
    this.refreshCount += 1;
    return {
      accessToken: `token_${this.refreshCount}`,
      expiresAtMs: fixedClock.now().getTime() + 600_000
    };
  }
}

class MemoryDriveRepository implements DriveFileRepository {
  public records: readonly DriveFileRecord[] = [];

  public async replaceAll(files: readonly DriveFileRecord[]): Promise<void> {
    this.records = [...files];
  }

  public async list(): Promise<readonly DriveFileRecord[]> {
    return this.records;
  }
}

describe("Google Drive infrastructure", () => {
  it("refreshes an expired token and parses Drive media files", async () => {
    const store = new MemoryTokenStore();
    store.token = { accessToken: "expired", expiresAtMs: fixedClock.now().getTime() - 1 };
    const refresher = new StaticTokenRefresher();
    const auth = new GoogleDriveAuthService(store, refresher, fixedClock);
    const fetchImpl: typeof fetch = async () =>
      new Response(
        JSON.stringify({
          files: [
            {
              id: "drive-file-1",
              name: "Track.mp3",
              mimeType: "audio/mpeg",
              modifiedTime: "2026-04-15T09:00:00.000Z",
              size: "1234",
              md5Checksum: "abc",
              webViewLink: "https://drive.example/file"
            }
          ]
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    const client = new GoogleDriveHttpClient(auth, fetchImpl, { retryDelayMs: 0 });

    const files = await client.listMediaFiles();

    expect(refresher.refreshCount).toBe(1);
    expect(files).toHaveLength(1);
    expect(files[0]?.name).toBe("Track.mp3");
  });

  it("retries once after a 401 response", async () => {
    const store = new MemoryTokenStore();
    const refresher = new StaticTokenRefresher();
    const auth = new GoogleDriveAuthService(store, refresher, fixedClock);
    const responses = [
      new Response("{}", { status: 401 }),
      new Response(JSON.stringify({ files: [] }), { status: 200, headers: { "Content-Type": "application/json" } })
    ];
    const fetchImpl: typeof fetch = async () =>
      responses.shift() ?? new Response(JSON.stringify({ files: [] }), { status: 200 });
    const client = new GoogleDriveHttpClient(auth, fetchImpl, { maxRetries: 1, retryDelayMs: 0 });

    const files = await client.listMediaFiles();

    expect(files).toEqual([]);
    expect(refresher.refreshCount).toBe(2);
  });

  it("persists sync state after a successful Drive index sync", async () => {
    const api: DriveApi = {
      listMediaFiles: async () => [
        {
          id: "drive-file-1",
          name: "Track.mp3",
          mimeType: "audio/mpeg",
          modifiedTime: "2026-04-15T09:00:00.000Z",
          sizeBytes: 1234,
          checksum: null,
          webViewLink: null
        }
      ]
    };
    const repository = new MemoryDriveRepository();
    const sync = new GoogleDriveSyncService(api, repository, fixedClock);

    const state = await sync.syncMediaIndex();

    expect(state.status).toBe("completed");
    expect(state.importedCount).toBe(1);
    expect(repository.records[0]?.syncedAt).toBe("2026-04-15T10:00:00.000Z");
  });

  it("records failed sync state without throwing into the UI", async () => {
    const api: DriveApi = {
      listMediaFiles: async () => {
        throw new Error("Drive unavailable");
      }
    };
    const repository = new MemoryDriveRepository();
    const sync = new GoogleDriveSyncService(api, repository, fixedClock);

    const state = await sync.syncMediaIndex();

    expect(state.status).toBe("failed");
    expect(state.errorMessage).toBe("Drive unavailable");
    expect(state.retryCount).toBe(1);
  });
});
