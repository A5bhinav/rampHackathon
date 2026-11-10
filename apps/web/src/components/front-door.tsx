import { BrandMark } from "./nav";
import { Rings } from "./ring-band";

/** The paper page with one white card (spec 0041): sign-in, sign-up, empty front doors, not found. */
export function FrontDoor({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <main className="front">
      <Rings className="rings front-rings" />
      <div className="card signin">
        <p className="brand"><BrandMark />Dialback</p>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}
