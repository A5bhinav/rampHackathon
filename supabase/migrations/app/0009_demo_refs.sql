-- Demo mode (spec 0011). A demo reset records the Ramp ids its seed created, by the seed's refs
-- ({"users": {"clerk": "<ramp user id>"}, "vendors": {"acme": "<ramp vendor id>"}}), so a later
-- `demo change`, run from another shell, acts on the right vendor and person. Ids only: no bank
-- details, which `demo change` reads from the seed file each time (ADR-8).
alter table app.datasets add column demo_refs jsonb;

comment on column app.datasets.demo_refs is
  'Demo datasets only: the Ramp ids the reset seeded, keyed by kind and seed ref. Never bank details.';
