export class GoogleDriveError extends Error {
  public constructor(message: string, public readonly statusCode: number | null = null) {
    super(message);
    this.name = "GoogleDriveError";
  }
}

export class GoogleDriveAuthExpiredError extends GoogleDriveError {
  public constructor(message = "Google Drive authorization expired") {
    super(message, 401);
    this.name = "GoogleDriveAuthExpiredError";
  }
}

export class GoogleDriveRateLimitError extends GoogleDriveError {
  public constructor(message = "Google Drive rate limit reached") {
    super(message, 429);
    this.name = "GoogleDriveRateLimitError";
  }
}

export class GoogleDriveNetworkError extends GoogleDriveError {
  public constructor(message = "Google Drive network request failed") {
    super(message, null);
    this.name = "GoogleDriveNetworkError";
  }
}

export const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : "Unknown Google Drive error";
