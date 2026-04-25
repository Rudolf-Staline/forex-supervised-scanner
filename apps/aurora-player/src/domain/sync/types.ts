import type { Brand } from "../common/brand";
import type { ISODateTime } from "../../shared/time";

export type SyncOperationId = Brand<string, "SyncOperationId">;

export type SyncTarget = "google-drive" | "user-profile";

export type SyncOperationKind =
  | "media-upserted"
  | "playlist-upserted"
  | "favorite-upserted"
  | "history-appended"
  | "podcast-upserted";

export type SyncOperation = {
  readonly id: SyncOperationId;
  readonly target: SyncTarget;
  readonly kind: SyncOperationKind;
  readonly entityId: string;
  readonly createdAt: ISODateTime;
  readonly retryCount: number;
  readonly payload: Readonly<Record<string, unknown>>;
};

export type SyncStatus = "idle" | "syncing" | "completed" | "failed";

export type SyncState = {
  readonly status: SyncStatus;
  readonly startedAt: ISODateTime | null;
  readonly completedAt: ISODateTime | null;
  readonly errorMessage: string | null;
  readonly importedCount: number;
  readonly retryCount: number;
};
