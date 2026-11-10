// The front door (spec 0063): three ways in and the seven stations, one word each. Fixed product copy.
import type { IconName } from "../components/icons";

export const DOORS: { label: string; href: string; tone: "primary" | "secondary" }[] = [
  { label: "Watch the demo", href: "/tour/1", tone: "primary" },
  { label: "AP workspace", href: "/app", tone: "secondary" },
  { label: "Control", href: "/app/control", tone: "secondary" },
];

export const STATION_WORDS: { word: string; icon: IconName }[] = [
  { word: "Detected", icon: "detect" },
  { word: "Held", icon: "hold" },
  { word: "Number", icon: "ledger" },
  { word: "Called", icon: "call" },
  { word: "Decided", icon: "decision" },
  { word: "Approved", icon: "person" },
  { word: "Released", icon: "release" },
];
