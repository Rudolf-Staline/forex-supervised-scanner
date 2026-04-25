import { describe, expect, it } from "vitest";
import { PlaybackQueue } from "./playbackQueue";
import { makeMediaItem } from "../../test/fakes/media";

describe("PlaybackQueue", () => {
  it("moves through the queue and ends when repeat is off", () => {
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");
    const queue = new PlaybackQueue([first, second]);

    expect(queue.current()?.id).toBe(first.id);
    expect(queue.next()?.id).toBe(second.id);
    expect(queue.next()).toBeNull();
    expect(queue.snapshot().currentIndex).toBeNull();
  });

  it("loops to the first item when repeat all is enabled", () => {
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");
    const queue = new PlaybackQueue([first, second]);

    queue.setRepeat("all");
    expect(queue.next()?.id).toBe(second.id);
    expect(queue.next()?.id).toBe(first.id);
  });

  it("replays the current item on repeat one at end of track", () => {
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");
    const queue = new PlaybackQueue([first, second]);

    queue.setRepeat("one");
    expect(queue.handleTrackEnded()?.id).toBe(first.id);
    expect(queue.snapshot().playedIds).toEqual([first.id]);
  });

  it("keeps the current item first when enabling deterministic shuffle", () => {
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");
    const third = makeMediaItem("media_3", "Third");
    const queue = new PlaybackQueue([first, second, third], {
      shuffle: (items) => [...items].reverse()
    });

    expect(queue.next()?.id).toBe(second.id);
    queue.setShuffle(true);

    const snapshot = queue.snapshot();
    expect(snapshot.items.map((item) => item.id)).toEqual([second.id, third.id, first.id]);
    expect(snapshot.currentIndex).toBe(0);
  });

  it("restores original ordering around the current item when shuffle is disabled", () => {
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");
    const third = makeMediaItem("media_3", "Third");
    const queue = new PlaybackQueue([first, second, third], {
      shuffle: (items) => [...items].reverse()
    });

    queue.next();
    queue.setShuffle(true);
    queue.setShuffle(false);

    const snapshot = queue.snapshot();
    expect(snapshot.items.map((item) => item.id)).toEqual([first.id, second.id, third.id]);
    expect(queue.current()?.id).toBe(second.id);
  });
});
