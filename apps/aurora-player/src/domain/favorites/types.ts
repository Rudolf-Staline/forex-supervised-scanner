import type { MediaId } from "../media/types";
import type { ISODateTime } from "../../shared/time";

export type Favorite = {
  readonly mediaId: MediaId;
  readonly createdAt: ISODateTime;
};
