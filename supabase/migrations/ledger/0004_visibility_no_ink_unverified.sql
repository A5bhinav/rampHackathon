-- ledger: two more ways a sighting can be hidden. Owner: Inesh. Spec 0092.
--
-- no_ink: a person can't see it, because drawing it leaves no mark on the page: text under a shape or an
--   image drawn after it, in invisible render mode, clipped away, or in the colour of what's under it
--   (Part A, the PDF reader).
-- unverified: its digits couldn't be confirmed against the page (Part B: a scan's two readings disagreed,
--   or a digital PDF's glyphs didn't match its text layer).
--
-- Both are hidden: kept, never quoted, never eligible (select() counts only visible sightings).
-- Numbered 0004 because open PR #120 adds 0003; migrate.py applies new files by name, so a gap is harmless.
--
-- Its own file: a new enum value can't be used in the transaction that adds it.

alter type ledger.visibility add value if not exists 'no_ink';
alter type ledger.visibility add value if not exists 'unverified';
