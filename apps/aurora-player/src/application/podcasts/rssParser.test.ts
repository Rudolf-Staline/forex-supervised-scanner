import { describe, expect, it } from "vitest";
import { parsePodcastRss, PodcastRssParseError } from "./rssParser";

describe("parsePodcastRss", () => {
  it("parses and cleans podcast episodes with enclosures", () => {
    const parsed = parsePodcastRss(
      `<?xml version="1.0"?>
      <rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
        <channel>
          <title>Aurora Sessions</title>
          <description>Deep listening</description>
          <item>
            <title>Night signal</title>
            <guid>episode-1</guid>
            <pubDate>Wed, 15 Apr 2026 09:00:00 GMT</pubDate>
            <itunes:duration>01:02:03</itunes:duration>
            <enclosure url="https://example.com/episode-1.mp3" type="audio/mpeg" />
          </item>
          <item>
            <title>Missing enclosure</title>
          </item>
        </channel>
      </rss>`,
      "https://example.com/feed.xml",
      new Date("2026-04-15T10:00:00.000Z")
    );

    expect(parsed.feed.title).toBe("Aurora Sessions");
    expect(parsed.episodes).toHaveLength(1);
    expect(parsed.episodes[0]?.durationSeconds).toBe(3723);
  });

  it("rejects invalid RSS", () => {
    expect(() => parsePodcastRss("<rss>", "https://example.com/feed.xml")).toThrow(PodcastRssParseError);
  });
});
