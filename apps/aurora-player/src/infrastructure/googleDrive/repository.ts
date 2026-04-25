import { requestToPromise, withStore } from "../persistence/indexedDb/client";
import { STORE_NAMES } from "../persistence/indexedDb/schema";
import type { DriveFileRecord, DriveFileRepository } from "./types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const castDriveFileRecord = (value: unknown): DriveFileRecord => {
  if (!isRecord(value) || typeof value.id !== "string") {
    throw new Error("Invalid Google Drive file record in IndexedDB");
  }
  return value as DriveFileRecord;
};

export class IndexedDbDriveFileRepository implements DriveFileRepository {
  public constructor(private readonly db: IDBDatabase) {}

  public async replaceAll(files: readonly DriveFileRecord[]): Promise<void> {
    await withStore(this.db, STORE_NAMES.driveFiles, "readwrite", async (store) => {
      await requestToPromise(store.clear());
      for (const file of files) {
        await requestToPromise(store.put(file));
      }
    });
  }

  public async list(): Promise<readonly DriveFileRecord[]> {
    return withStore(this.db, STORE_NAMES.driveFiles, "readonly", async (store) => {
      const result = await requestToPromise<unknown[]>(store.getAll());
      return result.map(castDriveFileRecord);
    });
  }
}
