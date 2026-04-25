export type ISODateTime = string;

export interface Clock {
  now(): Date;
}

export const systemClock: Clock = {
  now: () => new Date()
};

export const toIsoDateTime = (date: Date): ISODateTime => date.toISOString();
