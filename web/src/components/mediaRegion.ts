export interface MediaRegion {
  timeS: number;
  box: [number, number, number, number];
  width: number;
  height: number;
}

/** A fixed contact box is valid only around its observed frame, never a trajectory. */
export function regionForPresentation(
  region: MediaRegion | undefined,
  presentedTimeS: number | null,
  nativeWidth: number,
  nativeHeight: number,
) {
  if (!region || presentedTimeS === null || !Number.isFinite(presentedTimeS)) return null;
  const { box, width, height, timeS } = region;
  if (
    !Number.isFinite(timeS) ||
    timeS < 0 ||
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0 ||
    nativeWidth !== width ||
    nativeHeight !== height ||
    box.length !== 4 ||
    !box.every(Number.isFinite) ||
    box[0] < 0 ||
    box[1] < 0 ||
    box[2] > width ||
    box[3] > height ||
    box[2] <= box[0] ||
    box[3] <= box[1] ||
    Math.abs(presentedTimeS - timeS) > 0.15
  )
    return null;
  return region;
}
