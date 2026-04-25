import type { Clock } from "../../shared/time";
import { toIsoDateTime } from "../../shared/time";
import { errorMessage } from "./errors";
import type { DriveApi, DriveFileRecord, DriveFileRepository } from "./types";
import type { SyncState } from "../../domain/sync/types";

const idleState: SyncState = {
  status: "idle",
  startedAt: null,
  completedAt: null,
  errorMessage: null,
  importedCount: 0,
  retryCount: 0
};

export class GoogleDriveSyncService {
  private state: SyncState = idleState;

  public constructor(
    private readonly driveApi: DriveApi,
    private readonly driveFiles: DriveFileRepository,
    private readonly clock: Clock
  ) {}

  public getState(): SyncState {
    return this.state;
  }

  public async syncMediaIndex(): Promise<SyncState> {
    const startedAt = toIsoDateTime(this.clock.now());
    this.state = {
      status: "syncing",
      startedAt,
      completedAt: null,
      errorMessage: null,
      importedCount: 0,
      retryCount: this.state.retryCount
    };

    try {
      const files = await this.driveApi.listMediaFiles();
      const syncedAt = toIsoDateTime(this.clock.now());
      const records: readonly DriveFileRecord[] = files.map((file) => ({ ...file, syncedAt }));
      await this.driveFiles.replaceAll(records);
      this.state = {
        status: "completed",
        startedAt,
        completedAt: syncedAt,
        errorMessage: null,
        importedCount: records.length,
        retryCount: this.state.retryCount
      };
      return this.state;
    } catch (error) {
      this.state = {
        status: "failed",
        startedAt,
        completedAt: null,
        errorMessage: errorMessage(error),
        importedCount: 0,
        retryCount: this.state.retryCount + 1
      };
      return this.state;
    }
  }
}
