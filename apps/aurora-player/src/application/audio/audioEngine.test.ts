import { describe, expect, it } from "vitest";
import { AudioEngineService } from "./audioEngine";
import type { MediaSourceResolver } from "../../domain/media/types";
import { FakeAudioTransport } from "../../test/fakes/fakeAudioTransport";
import { makeMediaItem } from "../../test/fakes/media";

const resolver: MediaSourceResolver = {
  resolve: async (item) => ({ url: `blob:${item.id}`, mimeType: "audio/mpeg" })
};

describe("AudioEngineService", () => {
  it("loads, plays and seeks without depending on React", async () => {
    const transport = new FakeAudioTransport();
    const engine = new AudioEngineService(transport, resolver);
    const track = makeMediaItem("media_1", "First");

    await engine.loadQueue([track]);
    expect(engine.snapshot().status).toBe("paused");

    await engine.play();
    expect(engine.snapshot().status).toBe("playing");
    expect(transport.playCount).toBe(1);

    engine.seek(42);
    expect(engine.snapshot().positionSeconds).toBe(42);
  });

  it("advances to the next track on end-of-track", async () => {
    const transport = new FakeAudioTransport();
    const engine = new AudioEngineService(transport, resolver);
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");

    await engine.loadQueue([first, second]);
    await engine.play();
    await engine.handleTrackEnded();

    expect(engine.snapshot().queue.currentIndex).toBe(1);
    expect(engine.snapshot().status).toBe("playing");
    expect(transport.loadedSources.at(-1)?.url).toBe(`blob:${second.id}`);
  });

  it("marks playback ended when the queue is exhausted", async () => {
    const transport = new FakeAudioTransport();
    const engine = new AudioEngineService(transport, resolver);
    const track = makeMediaItem("media_1", "First");

    await engine.loadQueue([track]);
    await engine.handleTrackEnded();

    expect(engine.snapshot().status).toBe("ended");
    expect(engine.snapshot().queue.currentIndex).toBeNull();
  });

  it("replays the same track with repeat one", async () => {
    const transport = new FakeAudioTransport();
    const engine = new AudioEngineService(transport, resolver);
    const first = makeMediaItem("media_1", "First");
    const second = makeMediaItem("media_2", "Second");

    await engine.loadQueue([first, second]);
    engine.setRepeat("one");
    await engine.handleTrackEnded();

    expect(engine.snapshot().queue.currentIndex).toBe(0);
    expect(transport.loadedSources.at(-1)?.url).toBe(`blob:${first.id}`);
  });

  it("rejects invalid seek positions", async () => {
    const transport = new FakeAudioTransport();
    const engine = new AudioEngineService(transport, resolver);

    expect(() => {
      engine.seek(-1);
    }).toThrow("Seek position");
  });
});
