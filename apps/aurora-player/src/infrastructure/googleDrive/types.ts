import type { ISODateTime } from "../../shared/time";

export type DriveAccessToken = {
  readonly accessToken: string;
  readonly expiresAtMs: number;
};

export type DriveFile = {
  readonly id: string;
  readonly name: string;
  readonly mimeType: string;
  readonly modifiedTime: ISODateTime;
  readonly sizeBytes: number | null;
  readonly checksum: string | null;
  readonly webViewLink: string | null;
};

export type DriveFileRecord = DriveFile & {
  readonly syncedAt: ISODateTime;
};

export interface DriveApi {
  listMediaFiles(): Promise<readonly DriveFile[]>;
}

export interface DriveFileRepository {
  replaceAll(files: readonly DriveFileRecord[]): Promise<void>;
  list(): Promise<readonly DriveFileRecord[]>;
}
