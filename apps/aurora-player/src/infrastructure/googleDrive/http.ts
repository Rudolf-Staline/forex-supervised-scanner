import {
  GoogleDriveAuthExpiredError,
  GoogleDriveError,
  GoogleDriveNetworkError,
  GoogleDriveRateLimitError
} from "./errors";
import type { GoogleDriveAuthService } from "./auth";
import type { DriveApi, DriveFile } from "./types";

export type DriveHttpClientOptions = {
  readonly baseUrl: string;
  readonly maxRetries: number;
  readonly retryDelayMs: number;
};

type DriveListPayload = {
  readonly files: readonly DriveFile[];
  readonly nextPageToken: string | null;
};

const DEFAULT_OPTIONS: DriveHttpClientOptions = {
  baseUrl: "https://www.googleapis.com/drive/v3",
  maxRetries: 2,
  retryDelayMs: 250
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const optionalString = (record: Record<string, unknown>, key: string): string | null => {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
};

const requiredString = (record: Record<string, unknown>, key: string): string => {
  const value = optionalString(record, key);
  if (value === null) {
    throw new GoogleDriveError(`Drive response file.${key} is missing`);
  }
  return value;
};

const parseDriveFile = (value: unknown): DriveFile => {
  if (!isRecord(value)) {
    throw new GoogleDriveError("Drive response contains an invalid file record");
  }
  const size = optionalString(value, "size");
  const parsedSize = size === null ? null : Number.parseInt(size, 10);
  return {
    id: requiredString(value, "id"),
    name: requiredString(value, "name"),
    mimeType: requiredString(value, "mimeType"),
    modifiedTime: requiredString(value, "modifiedTime"),
    sizeBytes: parsedSize === null || Number.isNaN(parsedSize) ? null : parsedSize,
    checksum: optionalString(value, "md5Checksum"),
    webViewLink: optionalString(value, "webViewLink")
  };
};

const parseDriveListPayload = (payload: unknown): DriveListPayload => {
  if (!isRecord(payload)) {
    throw new GoogleDriveError("Drive list response is invalid");
  }
  const files = payload.files;
  if (!Array.isArray(files)) {
    throw new GoogleDriveError("Drive list response does not contain files");
  }
  return {
    files: files.map(parseDriveFile),
    nextPageToken: optionalString(payload, "nextPageToken")
  };
};

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });

export class GoogleDriveHttpClient implements DriveApi {
  private readonly options: DriveHttpClientOptions;

  public constructor(
    private readonly auth: GoogleDriveAuthService,
    private readonly fetchImpl: typeof fetch = fetch,
    options: Partial<DriveHttpClientOptions> = {}
  ) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  public async listMediaFiles(): Promise<readonly DriveFile[]> {
    const files: DriveFile[] = [];
    let pageToken: string | null = null;
    do {
      const payload = await this.requestList(pageToken);
      files.push(...payload.files);
      pageToken = payload.nextPageToken;
    } while (pageToken !== null);
    return files;
  }

  private async requestList(pageToken: string | null): Promise<DriveListPayload> {
    const query = new URLSearchParams({
      fields: "nextPageToken,files(id,name,mimeType,modifiedTime,size,md5Checksum,webViewLink)",
      pageSize: "1000",
      q: "trashed = false and (mimeType contains 'audio/' or mimeType contains 'video/')"
    });
    if (pageToken !== null) {
      query.set("pageToken", pageToken);
    }
    const url = `${this.options.baseUrl}/files?${query.toString()}`;
    return this.requestJson(url, parseDriveListPayload);
  }

  private async requestJson<TValue>(url: string, parser: (payload: unknown) => TValue): Promise<TValue> {
    for (let attempt = 0; attempt <= this.options.maxRetries; attempt += 1) {
      const token = await this.auth.getAccessToken();
      try {
        const response = await this.fetchImpl(url, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json"
          }
        });
        if (response.status === 401) {
          await this.auth.refreshAccessToken();
          if (attempt < this.options.maxRetries) {
            continue;
          }
          throw new GoogleDriveAuthExpiredError();
        }
        if (response.status === 429) {
          if (attempt < this.options.maxRetries) {
            await sleep(this.options.retryDelayMs);
            continue;
          }
          throw new GoogleDriveRateLimitError();
        }
        if (response.status >= 500 && attempt < this.options.maxRetries) {
          await sleep(this.options.retryDelayMs);
          continue;
        }
        if (!response.ok) {
          throw new GoogleDriveError(`Google Drive request failed with ${response.status}`, response.status);
        }
        const payload: unknown = await response.json();
        return parser(payload);
      } catch (error) {
        if (error instanceof GoogleDriveError) {
          throw error;
        }
        if (attempt < this.options.maxRetries) {
          await sleep(this.options.retryDelayMs);
          continue;
        }
        throw new GoogleDriveNetworkError(error instanceof Error ? error.message : undefined);
      }
    }
    throw new GoogleDriveNetworkError();
  }
}
