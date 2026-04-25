import { makeBrand } from "../../domain/common/brand";
import type { PodcastEpisode, PodcastFeed } from "../../domain/podcasts/types";
import { toIsoDateTime } from "../../shared/time";

export type ParsedPodcastRss = {
  readonly feed: PodcastFeed;
  readonly episodes: readonly PodcastEpisode[];
};

export class PodcastRssParseError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "PodcastRssParseError";
  }
}

const textContent = (parent: Element, selector: string): string | null => {
  const value = parent.querySelector(selector)?.textContent?.trim();
  return value === undefined || value.length === 0 ? null : value;
};

const enclosureUrl = (item: Element): string | null => {
  const enclosure = item.querySelector("enclosure");
  const url = enclosure?.getAttribute("url")?.trim();
  return url === undefined || url.length === 0 ? null : url;
};

const parseDuration = (value: string | null): number | undefined => {
  if (value === null) {
    return undefined;
  }
  const parts = value.split(":").map((part) => Number.parseInt(part, 10));
  if (parts.some((part) => Number.isNaN(part))) {
    return undefined;
  }
  if (parts.length === 1) {
    return parts[0];
  }
  if (parts.length === 2) {
    const minutes = parts[0];
    const seconds = parts[1];
    return minutes === undefined || seconds === undefined ? undefined : minutes * 60 + seconds;
  }
  const hours = parts[0];
  const minutes = parts[1];
  const seconds = parts[2];
  return hours === undefined || minutes === undefined || seconds === undefined
    ? undefined
    : hours * 3600 + minutes * 60 + seconds;
};

export const parsePodcastRss = (xml: string, feedUrl: string, now: Date = new Date()): ParsedPodcastRss => {
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (document.querySelector("parsererror") !== null) {
    throw new PodcastRssParseError("Podcast RSS XML is invalid");
  }
  const channel = document.querySelector("channel");
  if (channel === null) {
    throw new PodcastRssParseError("Podcast RSS channel is missing");
  }

  const title = textContent(channel, "title");
  if (title === null) {
    throw new PodcastRssParseError("Podcast RSS title is missing");
  }

  const updatedAt = toIsoDateTime(now);
  const feedId = makeBrand(`podcast_${feedUrl}`, "PodcastFeedId");
  const description = textContent(channel, "description");
  const artworkUrl = textContent(channel, "image url");
  const feed: PodcastFeed = {
    id: feedId,
    title,
    feedUrl,
    updatedAt,
    ...(description === null ? {} : { description }),
    ...(artworkUrl === null ? {} : { artworkUrl })
  };

  const episodes = Array.from(channel.querySelectorAll("item")).flatMap((item): PodcastEpisode[] => {
    const episodeTitle = textContent(item, "title");
    const url = enclosureUrl(item);
    if (episodeTitle === null || url === null) {
      return [];
    }
    const guid = textContent(item, "guid") ?? url;
    const published = textContent(item, "pubDate");
    const publishedDate = published === null ? null : new Date(published);
    const durationSeconds = parseDuration(textContent(item, "duration, itunes\\:duration"));
    const episode: PodcastEpisode = {
      mediaId: makeBrand(`podcast_episode_${guid}`, "MediaId"),
      feedId,
      guid,
      title: episodeTitle,
      enclosureUrl: url,
      ...(publishedDate === null || Number.isNaN(publishedDate.getTime())
        ? {}
        : { publishedAt: toIsoDateTime(publishedDate) }),
      ...(durationSeconds === undefined ? {} : { durationSeconds })
    };
    return [episode];
  });

  return { feed, episodes };
};
