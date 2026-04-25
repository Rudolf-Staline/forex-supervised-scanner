import type { SyncOperationRepository } from "../../domain/repositories";
import type { SyncOperation, SyncTarget } from "../../domain/sync/types";

export interface SyncOperationHandler {
  target: SyncTarget;
  push(operation: SyncOperation): Promise<void>;
}

export class DeferredSyncService {
  public constructor(
    private readonly operations: SyncOperationRepository,
    private readonly handlers: readonly SyncOperationHandler[]
  ) {}

  public async flush(target: SyncTarget): Promise<number> {
    const handler = this.handlers.find((candidate) => candidate.target === target);
    if (handler === undefined) {
      throw new Error(`No sync handler registered for ${target}`);
    }
    const pending = await this.operations.listPending(target);
    let flushed = 0;
    for (const operation of pending) {
      await handler.push(operation);
      await this.operations.remove(operation.id);
      flushed += 1;
    }
    return flushed;
  }
}
