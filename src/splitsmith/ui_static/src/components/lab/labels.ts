// Single-key shortcuts when a candidate row is selected.
// For rejected (FP / not-kept) candidates: set ``reason``.
// For kept positives (TP): set ``subclass`` (paper/steel/unknown).
export const REASON_SHORTCUTS: Record<string, string> = {
  x: "cross_bay",
  e: "echo",
  b: "barrel_echo",
  w: "wind",
  m: "movement",
  s: "steel_ring",
  h: "handling",
  a: "agc_artifact",
  v: "speech", // mnemonic: Voice. Was Y, but Y/S confusion (steel_ring next
  // to speech on QWERTY) caused a lot of mis-clicks in practice.
  o: "other",
  u: "unknown",
};
export const SUBCLASS_SHORTCUTS: Record<string, string> = {
  p: "paper",
  s: "steel",
  b: "barrel",
  u: "unknown",
};
